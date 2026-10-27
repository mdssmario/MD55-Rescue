"""Herramientas de tarjeta: información, salud, pruebas y reparación (Windows y macOS).

  * info()            modelo, capacidad, particiones, sistema de archivos.
  * health()          contadores de desgaste/errores si la tarjeta o el lector los exponen
                      (CFexpress/NVMe suele hacerlo; las SD casi nunca por USB).
  * surface_scan()    lee toda la tarjeta: sectores ilegibles y zonas lentas. No escribe.
  * capacity_test()   como H2testw/F3: llena el ESPACIO LIBRE con datos verificables, los
                      relee sin caché y comprueba capacidad real, integridad y velocidad.
                      No toca los archivos existentes, pero sí sobrescribe lo borrado.
  * fs_repair()       comprueba y repara el sistema de archivos (chkdsk / diskutil).
  * reformat()        borra todo y rehace partición + formato al estilo SD (alineado a
                      4 MB, FAT32 hasta 32 GB, exFAT por encima). Destructivo.
"""
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time

from . import carve

GB = 1000 ** 3
MB = 1000 ** 2
CHUNK = 8 << 20                 # unidad de escritura/lectura de las pruebas
TEST_FILE = 1 << 30             # tamaño de cada archivo de la prueba de capacidad
TEST_DIR = 'MD55_TEST'
SPEED_CLASSES = [('V90', 90), ('V60', 60), ('V30', 30), ('U3/V30', 30), ('V10 / Clase 10', 10)]
ALLOWED_BUS = {'USB', 'SD', 'MMC', 'SCSI'}


class CardError(Exception):
    pass


def _run(args, timeout=120):
    flags = 0x08000000 if sys.platform == 'win32' else 0
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)


def _ps(script):
    r = _run(['powershell', '-NoProfile', '-Command', script])
    out = r.stdout.decode('utf-8', 'replace').strip()
    return json.loads(out) if out else None


def _letter(source):
    m = re.fullmatch(r'(?:\\\\\.\\)?([A-Za-z]):\\?', source.strip())
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------- información

def info(source):
    """Datos del disco que contiene 'source' (letra en Windows, /dev/diskN en macOS)."""
    if sys.platform == 'win32':
        letter = _letter(source)
        if not letter:
            raise CardError('Indica la tarjeta por su letra (p.ej. H:).')
        data = _ps(
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$p=Get-Partition -DriveLetter %s -ErrorAction Stop; $d=$p|Get-Disk;"
            "$v=Get-Volume -DriveLetter %s -ErrorAction SilentlyContinue;"
            "[pscustomobject]@{number=$d.Number; model=$d.FriendlyName; bus=[string]$d.BusType;"
            " size=$d.Size; style=[string]$d.PartitionStyle; health=[string]$d.HealthStatus;"
            " system=($d.IsSystem -or $d.IsBoot); fs=$v.FileSystem; label=$v.FileSystemLabel;"
            " free=$v.SizeRemaining; volsize=$v.Size;"
            " partitions=@(Get-Partition -DiskNumber $d.Number | ForEach-Object {"
            "  [pscustomobject]@{n=$_.PartitionNumber; offset=$_.Offset; size=$_.Size;"
            "  letter=[string]$_.DriveLetter; type=[string]$_.MbrType}})} | ConvertTo-Json -Depth 4"
            % (letter, letter))
        if not data:
            raise CardError('No se encontró la unidad %s:' % letter)
        import ctypes
        # 2 = DRIVE_REMOVABLE: tarjetas y lectores. Los SSD/discos USB salen como fijos (3).
        data['removable'] = ctypes.windll.kernel32.GetDriveTypeW('%s:\\' % letter) == 2
        data['id'] = '%s:' % letter
        data['model'] = (data.get('model') or '').strip() or 'Tarjeta / lector USB'
        return data
    if sys.platform == 'darwin':
        import plistlib
        disk = os.path.basename(source).replace('rdisk', 'disk')
        whole = re.match(r'(disk\d+)', disk).group(1)
        d = plistlib.loads(_run(['diskutil', 'info', '-plist', whole]).stdout)
        parts = plistlib.loads(_run(['diskutil', 'list', '-plist', whole]).stdout)
        plist = []
        for entry in parts.get('AllDisksAndPartitions', []):
            for p in entry.get('Partitions', []):
                plist.append({'n': p.get('DeviceIdentifier'), 'offset': None, 'size': p.get('Size'),
                              'letter': p.get('MountPoint') or '', 'type': p.get('Content')})
        mount = next((p['letter'] for p in plist if p['letter']), None)
        usage = shutil.disk_usage(mount) if mount else None
        return {'id': '/dev/' + whole, 'number': whole, 'model': d.get('MediaName') or whole,
                'bus': d.get('BusProtocol') or '', 'size': d.get('TotalSize') or d.get('Size'),
                'removable': bool(d.get('RemovableMedia') or d.get('Removable')),
                'style': d.get('Content'), 'health': d.get('SMARTStatus') or '',
                'system': bool(d.get('Internal')) and not d.get('RemovableMedia'),
                'fs': None, 'label': None, 'free': usage.free if usage else None,
                'volsize': usage.total if usage else None, 'mount': mount, 'partitions': plist}
    raise CardError('Sistema no soportado para esta función.')


def mount_point(card):
    """Carpeta raíz montada de la tarjeta (para la prueba de capacidad)."""
    if sys.platform == 'win32':
        return card['id'] + '\\'
    return card.get('mount')


def safety_check(card, allow_virtual=False, allow_fixed=False):
    """Evita actuar sobre el disco del sistema o discos que no parecen una tarjeta.
    Solo se permiten unidades extraíbles (tarjetas y lectores); un SSD o disco USB externo
    figura como fijo y queda bloqueado salvo que se fuerce con allow_fixed."""
    if card.get('system'):
        raise CardError('Es un disco del sistema: operación bloqueada.')
    if not card.get('removable') and not allow_fixed and not allow_virtual:
        raise CardError('%s no es una unidad extraíble (parece un disco o SSD externo, no una tarjeta): '
                        'operación bloqueada. Si de verdad es un lector de tarjetas, usa la opción '
                        '«no es extraíble».' % card.get('id'))
    bus = (card.get('bus') or '').upper()
    if sys.platform == 'win32' and bus not in ALLOWED_BUS and not (
            allow_virtual and bus in ('FILE BACKED VIRTUAL', 'VIRTUAL')):
        raise CardError('El disco no es una tarjeta ni un lector USB (bus %s): operación bloqueada.'
                        % (card.get('bus') or '?'))
    if (card.get('size') or 0) > 2 * 1024 ** 4:
        raise CardError('Más de 2 TB: no parece una tarjeta de memoria. Operación bloqueada.')
    here = _letter(os.path.splitdrive(os.path.abspath(__file__))[0] + '\\') if sys.platform == 'win32' else None
    if here and any(p.get('letter') == here for p in card.get('partitions', [])):
        raise CardError('El programa se está ejecutando desde ese disco: operación bloqueada.')


# ---------------------------------------------------------------- salud

def health(card):
    """Contadores de salud. Devuelve dict con lo disponible y una conclusión en texto."""
    out = {'disponible': False}
    if sys.platform == 'win32':
        try:
            data = _ps("$d=Get-PhysicalDisk | Where-Object DeviceId -eq '%s';"
                       "$r=$d | Get-StorageReliabilityCounter -ErrorAction Stop;"
                       "$r | Select-Object Wear,Temperature,PowerOnHours,ReadErrorsTotal,"
                       "ReadErrorsUncorrected,WriteErrorsTotal,WriteErrorsUncorrected | ConvertTo-Json"
                       % card.get('number'))
        except (ValueError, subprocess.SubprocessError):
            data = None
        if data:
            data = {k: v for k, v in data.items() if v not in (None, 0) or k == 'Wear'}
            if any(v is not None for v in data.values()):
                out.update(data)
                out['disponible'] = data.get('Wear') is not None or len(data) > 1
        out['estado'] = card.get('health')
    elif sys.platform == 'darwin':
        out['estado'] = card.get('health')
        out['disponible'] = card.get('health') not in ('', 'Not Supported', None)
    if out.get('Wear') is not None:
        left = max(0, 100 - int(out['Wear']))
        out['conclusion'] = 'Vida útil restante estimada: %d %% (desgaste %s %%).' % (left, out['Wear'])
    elif out['disponible']:
        out['conclusion'] = 'La tarjeta informa estado: %s.' % out.get('estado')
    else:
        out['conclusion'] = ('Esta tarjeta o su lector no informan de desgaste (normal en SD por USB). '
                             'Usa la prueba de capacidad y velocidad para evaluarla.')
    return out


# ---------------------------------------------------------------- prueba de superficie

def surface_scan(source, log=print, progress=None, should_stop=None):
    """Lee toda la tarjeta en bruto. Sin escrituras. Requiere permisos de Administrador."""
    src = carve.Source(source)
    bad = []
    speeds = []
    pos = 0
    t_start = time.perf_counter()
    try:
        log('Leyendo %s entera (%.1f GB). No se escribe nada.' % (src.label, src.size / GB))
        while pos < src.size:
            if should_stop and should_stop():
                log('Detenido por el usuario en %.1f GB.' % (pos / GB))
                break
            n = min(CHUNK, src.size - pos)
            t0 = time.perf_counter()
            try:
                src.read(pos, n)
                dt = time.perf_counter() - t0
                speeds.append((pos, n / max(dt, 1e-6)))
            except OSError:
                # localizar los sectores malos dentro del bloque
                for s in range(pos, pos + n, 64 * 1024):
                    try:
                        src.read(s, min(64 * 1024, pos + n - s))
                    except OSError:
                        if bad and bad[-1][1] == s:
                            bad[-1][1] = s + 64 * 1024
                        else:
                            bad.append([s, s + 64 * 1024])
                log('  error de lectura cerca de %.2f GB' % (pos / GB))
            pos += n
            if progress:
                progress(pos / src.size)
    finally:
        src.close()
    elapsed = time.perf_counter() - t_start
    vals = sorted(v for _p, v in speeds)
    res = {'leido': pos, 'segundos': elapsed, 'zonas_ilegibles': bad,
           'bytes_ilegibles': sum(b - a for a, b in bad)}
    if vals:
        median = vals[len(vals) // 2]
        slow = [p for p, v in speeds if v < median / 4]
        res.update(lectura_media=pos / max(elapsed, 1e-6) / MB, lectura_min=vals[0] / MB,
                   lectura_mediana=median / MB, zonas_lentas=len(slow))
        log('Velocidad de lectura: media %.0f MB/s, mínima %.0f MB/s.' % (res['lectura_media'],
                                                                         res['lectura_min']))
        if slow:
            log('Zonas muy lentas (menos de 1/4 de la mediana): %d bloques de 8 MB.' % len(slow))
    if bad:
        log('ATENCIÓN: %d zonas ilegibles (%.1f MB). La tarjeta tiene sectores dañados: no la '
            'uses para grabar.' % (len(bad), res['bytes_ilegibles'] / MB))
    else:
        log('Sin sectores ilegibles.')
    return res


# ---------------------------------------------------------------- E/S sin caché

class _RawFile:
    """Archivo con escritura/lectura que no pasa por la caché del sistema, para medir la
    velocidad real y verificar lo que de verdad hay en la tarjeta."""

    def __init__(self, path, write):
        self.path = path
        self.write_mode = write
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            k.CreateFileW.restype = wintypes.HANDLE
            k.VirtualAlloc.restype = ctypes.c_void_p
            access = 0x40000000 if write else 0x80000000            # GENERIC_WRITE / READ
            disp = 2 if write else 3                                 # CREATE_ALWAYS / OPEN_EXISTING
            flags = 0x20000000 | 0x80000000                          # NO_BUFFERING | WRITE_THROUGH
            self.h = k.CreateFileW(path, access, 1, None, disp, flags, None)
            if self.h in (None, wintypes.HANDLE(-1).value):
                raise OSError(ctypes.get_last_error(), 'No se pudo abrir %s' % path)
            self.k = k
            self.ctypes = ctypes
            self.buf = k.VirtualAlloc(None, CHUNK, 0x3000, 0x04)     # alineado a página
        else:
            self.f = open(path, 'wb' if write else 'rb', buffering=0)
            if sys.platform == 'darwin':
                import fcntl
                fcntl.fcntl(self.f.fileno(), 48, 1)                  # F_NOCACHE

    def write(self, data):
        if sys.platform != 'win32':
            self.f.write(data)
            return
        c = self.ctypes
        c.memmove(self.buf, data, len(data))
        done = c.c_ulong(0)
        if not self.k.WriteFile(self.h, c.c_void_p(self.buf), len(data), c.byref(done), None) \
                or done.value != len(data):
            raise OSError('error de escritura')

    def read(self, n):
        if sys.platform != 'win32':
            return self.f.read(n)
        c = self.ctypes
        done = c.c_ulong(0)
        if not self.k.ReadFile(self.h, c.c_void_p(self.buf), n, c.byref(done), None):
            raise OSError('error de lectura')
        return c.string_at(self.buf, done.value)

    def close(self):
        if sys.platform == 'win32':
            self.k.CloseHandle(self.h)
            self.k.VirtualFree(self.ctypes.c_void_p(self.buf), 0, 0x8000)
        else:
            if self.write_mode:
                self.f.flush()
                os.fsync(self.f.fileno())
            self.f.close()


_BASE = None


def _pattern(index):
    """Bloque de 8 MB verificable: datos pseudoaleatorios + en cada sector de 4 KB su
    posición global, para detectar tarjetas falsas que «dan la vuelta» y repiten datos."""
    global _BASE
    if _BASE is None:
        _BASE = random.Random(5555).randbytes(CHUNK)
    shift = (index * 4099) % 4096
    block = bytearray(_BASE[shift:] + _BASE[:shift])
    base = index * CHUNK
    for off in range(0, CHUNK, 4096):
        struct.pack_into('<8sQ', block, off, b'MD55TEST', base + off)
    return bytes(block)


def _sector_owner(data, off):
    tag, where = struct.unpack_from('<8sQ', data, off)
    return where if tag == b'MD55TEST' else None


# ---------------------------------------------------------------- prueba de capacidad

def capacity_test(root, log=print, progress=None, should_stop=None, max_bytes=None, keep=False):
    """Llena el espacio libre de 'root' con datos verificables y los comprueba."""
    folder = os.path.join(root, TEST_DIR)
    os.makedirs(folder, exist_ok=True)
    free = shutil.disk_usage(root).free
    total = free - 16 * 1024 * 1024
    if max_bytes:
        total = min(total, max_bytes)
    total = total // CHUNK * CHUNK
    if total <= 0:
        raise CardError('No hay espacio libre para la prueba.')
    log('Prueba de capacidad: se escribirán %.2f GB en %s y se verificarán.' % (total / GB, folder))
    files = []
    w_speeds = []
    written = 0
    stopped = False
    try:
        idx = 0
        while written < total and not stopped:
            path = os.path.join(folder, 'MD55_%04d.dat' % (len(files) + 1))
            size = min(TEST_FILE, total - written)
            f = _RawFile(path, True)
            files.append((path, idx, size))
            try:
                for _ in range(size // CHUNK):
                    if should_stop and should_stop():
                        stopped = True
                        break
                    data = _pattern(idx)
                    t0 = time.perf_counter()
                    f.write(data)
                    w_speeds.append(CHUNK / max(time.perf_counter() - t0, 1e-6))
                    idx += 1
                    written += CHUNK
                    if progress:
                        progress(0.5 * written / total)
            except OSError as e:
                log('Error de escritura a %.2f GB: %s' % (written / GB, e))
                stopped = True
            finally:
                f.close()
        log('Escrito: %.2f GB. Verificando sin caché…' % (written / GB))

        ok = bad = wrapped = 0
        first_bad = None
        r_speeds = []
        checked = 0
        for path, first_idx, size in files:
            f = _RawFile(path, False)
            try:
                for k in range(size // CHUNK):
                    gidx = first_idx + k
                    if gidx * CHUNK >= written:
                        break
                    if should_stop and should_stop():
                        break
                    t0 = time.perf_counter()
                    data = f.read(CHUNK)
                    r_speeds.append(len(data) / max(time.perf_counter() - t0, 1e-6))
                    expect = _pattern(gidx)
                    if data == expect:
                        ok += CHUNK
                    else:
                        for off in range(0, CHUNK, 4096):
                            if data[off:off + 4096] != expect[off:off + 4096]:
                                bad += 4096
                                owner = _sector_owner(data, off) if len(data) >= off + 16 else None
                                if owner is not None and owner != gidx * CHUNK + off:
                                    wrapped += 4096
                                if first_bad is None:
                                    first_bad = gidx * CHUNK + off
                            else:
                                ok += 4096
                    checked += CHUNK
                    if progress:
                        progress(0.5 + 0.5 * checked / max(written, 1))
            except OSError as e:
                log('Error de lectura en %s: %s' % (os.path.basename(path), e))
                bad += size
            finally:
                f.close()
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)

    def stats(v):
        v = sorted(v)
        return (sum(v) / len(v) / MB, v[max(0, len(v) // 100)] / MB) if v else (0, 0)

    w_avg, w_min = stats(w_speeds)
    r_avg, _r_min = stats(r_speeds)
    res = {'escrito': written, 'correcto': ok, 'erroneo': bad, 'repetido': wrapped,
           'primer_error': first_bad, 'escritura_media': w_avg, 'escritura_minima': w_min,
           'lectura_media': r_avg, 'detenido': stopped}
    log('Escritura: media %.0f MB/s, mínima sostenida %.0f MB/s · Lectura: media %.0f MB/s'
        % (w_avg, w_min, r_avg))
    cls = next((name for name, need in SPEED_CLASSES if w_min >= need), None)
    res['clase'] = cls
    log('Clase de velocidad que cumple de verdad: %s' % (cls or 'ninguna (menos de 10 MB/s sostenidos)'))
    if bad == 0 and not stopped:
        log('RESULTADO: correcta. Los %.2f GB escritos se leyeron idénticos.' % (written / GB))
        res['veredicto'] = 'correcta'
    elif wrapped:
        log('RESULTADO: TARJETA FALSA. Solo %.2f GB son reales: a partir de ahí repite datos.'
            % ((first_bad or 0) / GB))
        res['veredicto'] = 'falsa'
    elif bad:
        log('RESULTADO: DATOS CORRUPTOS en %.1f MB (primer error a %.2f GB). No la uses para grabar.'
            % (bad / MB, (first_bad or 0) / GB))
        res['veredicto'] = 'errores'
    else:
        res['veredicto'] = 'incompleta'
    return res


# ---------------------------------------------------------------- reparación

def fs_repair(card, log=print):
    """Comprueba y repara el sistema de archivos. No borra datos (puede mover fragmentos
    perdidos a FOUND.000). Requiere permisos de Administrador."""
    if sys.platform == 'win32':
        r = _run(['chkdsk', card['id'], '/f', '/x'], timeout=3600)
        text = r.stdout.decode('cp850', 'replace')
    else:
        target = card.get('partitions', [{}])[0].get('n') or card['id']
        r = _run(['diskutil', 'repairVolume', target], timeout=3600)
        text = (r.stdout + r.stderr).decode('utf-8', 'replace')
    for line in text.splitlines():
        if line.strip():
            log('  ' + line.strip())
    log('Comprobación terminada (código %d).' % r.returncode)
    return r.returncode


def sd_layout(size):
    """Sistema de archivos y tamaño de clúster recomendados por la SD Association."""
    if size <= 32 * GB:
        return 'FAT32', '32K'
    if size <= 512 * GB:
        return 'exFAT', '128K'
    return 'exFAT', '256K'


def reformat(card, label='MD55', log=print, allow_virtual=False, simulate=False, allow_fixed=False):
    """BORRA TODO: tabla de particiones nueva (MBR, alineada a 4 MB) y formato estilo SD.
    Con simulate=True solo muestra los pasos, sin ejecutar nada."""
    safety_check(card, allow_virtual, allow_fixed)
    fs, unit = sd_layout(card['size'])
    log('%s %s (%.1f GB): MBR, partición alineada a 4 MB, %s con clúster %s…'
        % ('Simulación de formateo de' if simulate else 'Formateando', card['id'],
           card['size'] / GB, fs, unit))
    if sys.platform == 'win32':
        letter = _letter(card['id'])
        script = '\n'.join([
            'select disk %d' % card['number'],
            'clean',
            'convert mbr',
            'create partition primary align=4096',
            'format fs=%s unit=%s quick label=%s' % (fs.lower(), unit, label),
            'assign letter=%s' % letter if letter else 'assign',
            'exit', ''])
        if simulate:
            for line in script.splitlines()[:-2]:
                log('  diskpart> ' + line)
            return fs, unit
        path = os.path.join(os.environ.get('TEMP', '.'), 'md55_diskpart.txt')
        with open(path, 'w') as f:
            f.write(script)
        try:
            r = _run(['diskpart', '/s', path], timeout=1800)
        finally:
            os.remove(path)
        text = r.stdout.decode('cp850', 'replace')
    else:
        fmt = 'MS-DOS FAT32' if fs == 'FAT32' else 'ExFAT'
        cmd = ['diskutil', 'eraseDisk', fmt, label, 'MBRFormat', card['id']]
        if simulate:
            log('  ' + ' '.join(cmd))
            return fs, unit
        r = _run(cmd, timeout=1800)
        text = (r.stdout + r.stderr).decode('utf-8', 'replace')
    for line in text.splitlines():
        if line.strip() and 'Microsoft' not in line and 'Copyright' not in line:
            log('  ' + line.strip())
    if r.returncode != 0:
        raise CardError('El formateo falló (código %d). Revisa el registro.' % r.returncode)
    log('Formato completado. Recomendado: formatear también en la cámara antes de grabar.')
    return fs, unit
