"""Rescate desde la tarjeta (o una imagen .img de ella): busca clips Sony borrados o
perdidos tras un formateo rápido, por firma, sin depender del sistema de archivos.

Reconoce:
  * MP4 / XAVC S / XAVC HS / XAVC S-I  (caja 'ftyp' alineada a sector)
  * MXF / XAVC                          (partition pack de cabecera)
  * XML de metadatos Sony               (<NonRealTimeMeta>, contiene S-Log3/S-Cinetone)
"""
import hashlib
import os
import re
import struct
import sys

from . import mp4, mxf, photos, sonycard

PHOTO_KINDS = ('raw', 'heif', 'jpg')
LONE_HEADER = 1 << 20       # un "clip" de este tamaño o menos es solo una cabecera, sin vídeo
PHOTO_EXTS = ('.ARW', '.DNG', '.TIF', '.HIF', '.HEIC', '.AVIF', '.JPG', '.JPEG')

SECTOR = 512
BLOCK = 8 << 20
KNOWN_BRANDS = {b'XAVC', b'mp42', b'mp41', b'isom', b'iso2', b'iso4', b'avc1', b'qt  ', b'MSNV'}
MAX_INCOMPLETE = 512 << 30
# cajas que Sony escribe después del moov (la 'meta' lleva el XML con S-Log3/S-Cinetone)
TRAILING_BOXES = {b'meta', b'uuid', b'free', b'skip', b'udta'}


class Source:
    """Lectura alineada a sector de un archivo, volumen (E:) o disco físico."""

    def __init__(self, path):
        self.label = path
        m = re.fullmatch(r'([A-Za-z]):\\?', path.strip())
        if m:
            path = r'\\.\%s:' % m.group(1).upper()
        if sys.platform == 'darwin' and path.startswith('/dev/disk'):
            path = '/dev/rdisk' + path[len('/dev/disk'):]      # acceso en bruto, mucho más rápido
        self.path = path
        self.raw = path.startswith('\\\\.\\') or path.startswith('/dev/')
        self.f = open(path, 'rb', buffering=0)
        self.size = self._size()

    def _size(self):
        if self.raw and sys.platform != 'win32':
            import fcntl
            try:
                if sys.platform == 'darwin':
                    bs = struct.unpack('I', fcntl.ioctl(self.f, 0x40046418, b'\0' * 4))[0]
                    count = struct.unpack('Q', fcntl.ioctl(self.f, 0x40086419, b'\0' * 8))[0]
                    return bs * count                  # DKIOCGETBLOCKSIZE × DKIOCGETBLOCKCOUNT
                return struct.unpack('Q', fcntl.ioctl(self.f, 0x80081272, b'\0' * 8))[0]
            except OSError:
                pass
        if self.raw and sys.platform == 'win32':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            handle = msvcrt.get_osfhandle(self.f.fileno())
            length = ctypes.c_longlong(0)
            got = wintypes.DWORD(0)
            ok = ctypes.windll.kernel32.DeviceIoControl(
                wintypes.HANDLE(handle), 0x7405C, None, 0,          # IOCTL_DISK_GET_LENGTH_INFO
                ctypes.byref(length), 8, ctypes.byref(got), None)
            if ok:
                return length.value
        self.f.seek(0, 2)
        return self.f.tell()

    def read(self, offset, n):
        """Lee n bytes desde offset (alineando a sector si es un dispositivo)."""
        if offset >= self.size or n <= 0:
            return b''
        n = min(n, self.size - offset)
        a = offset // 4096 * 4096
        b = -(-(offset + n) // 4096) * 4096
        self.f.seek(a)
        data = self.f.read(min(b, -(-self.size // 4096) * 4096) - a)
        return data[offset - a:offset - a + n]

    def close(self):
        self.f.close()


def _drive_of(path):
    m = re.match(r'(?:\\\\\.\\)?([A-Za-z]):', path)
    return m.group(1).upper() if m else None


def _device_drive(source):
    """Letra de unidad si el origen es una tarjeta leída en bruto (E: o \\\\.\\E:)."""
    m = re.fullmatch(r'(?:\\\\\.\\)?([A-Za-z]):\\?', source.strip())
    return m.group(1).upper() if m else None


def _block_hits(data, pos):
    """Firmas alineadas a sector dentro de un bloque leído en pos."""
    hits = []
    i = data.find(b'ftyp')
    while i >= 0:
        start = pos + i - 4
        if start >= 0 and start % SECTOR == 0 and 4 <= i < BLOCK + 4:
            size = struct.unpack('>I', data[i - 4:i])[0]
            brand = data[i + 4:i + 8]
            if 12 <= size <= 512 and brand in KNOWN_BRANDS:
                hits.append((start, 'mp4', brand.decode('latin1')))
            elif 12 <= size <= 512 and brand in photos.HEIF_BRANDS:
                hits.append((start, 'heif', brand.decode('latin1')))
        i = data.find(b'ftyp', i + 1)
    for sig in (b'II*\0', b'MM\0*'):            # RAW (ARW, DNG...) y TIFF
        i = data.find(sig)
        while i >= 0:
            start = pos + i
            if start % SECTOR == 0 and i < BLOCK and photos.is_tiff_start(data, i):
                hits.append((start, 'raw', ''))
            i = data.find(sig, i + 1)
    i = data.find(b'\xff\xd8\xff')
    while i >= 0:
        start = pos + i
        if start % SECTOR == 0 and i < BLOCK and photos.is_jpeg_start(data, i):
            hits.append((start, 'jpg', ''))
        i = data.find(b'\xff\xd8\xff', i + 1)
    i = data.find(mxf.PP_PREFIX)
    while i >= 0:
        start = pos + i
        if start % SECTOR == 0 and i < BLOCK and i + 14 < len(data) and data[i + 13] == 2:
            hits.append((start, 'mxf', ''))
        i = data.find(mxf.PP_PREFIX, i + 1)
    i = data.find(b'<?xml')
    while i >= 0:
        start = pos + i
        if start % SECTOR == 0 and i < BLOCK and b'NonRealTimeMeta' in data[i:i + 1024]:
            hits.append((start, 'xml', ''))
        i = data.find(b'<?xml', i + 1)
    i = data.find(b'moov')
    while i >= 0:
        start = pos + i - 4
        if start >= 0 and start % SECTOR == 0 and 4 <= i < BLOCK + 4 and data[i + 8:i + 12] == b'mvhd':
            hits.append((start, 'moov', ''))
        i = data.find(b'moov', i + 1)
    return sorted(hits)


class SignatureScanner:
    """Recorre el origen bloque a bloque; skip_to() salta los clips ya recuperados."""

    def __init__(self, src, progress=None, should_stop=None, start=0, end=None):
        self.src = src
        self.pos = start // SECTOR * SECTOR
        self.begin = self.pos
        self.end = min(end or src.size, src.size)
        self.progress = progress
        self.should_stop = should_stop
        self.stopped = False

    def __iter__(self):
        while self.pos < self.end:
            if self.should_stop and self.should_stop():
                self.stopped = True
                return
            pos = self.pos
            data = self.src.read(pos, BLOCK + 64)
            if not data:
                break
            self.pos = pos + BLOCK
            yield from _block_hits(data, pos)
            if self.progress:
                self.progress(min(1.0, (self.pos - self.begin) / max(1, self.end - self.begin)))

    def skip_to(self, offset):
        self.pos = max(self.pos, offset // SECTOR * SECTOR)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(16 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_targets(folder, kinds=('video', 'foto')):
    """Archivos de una carpeta a localizar en la tarjeta: {tamaño: [(nombre, sha256)]}.
    Solo los de los tipos que se buscan (si se buscan fotos, los vídeos no cuentan)."""
    exts = (('.MP4', '.MXF') if 'video' in kinds else ()) + (PHOTO_EXTS if 'foto' in kinds else ())
    targets = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and exts and name.upper().endswith(exts):
            targets.setdefault(os.path.getsize(path), []).append((name, _sha256(path)))
    return targets


def mp4_extent(src, start, limit):
    """Recorre las cajas desde start. Devuelve (fin, completo, nota)."""
    pos = start
    seen = set()
    while pos + 8 <= src.size:
        h = src.read(pos, 16)
        size, btype = struct.unpack('>I4s', h[:8])
        hs = 8
        if not mp4.is_box_type(btype):
            break
        if size == 1:
            size = struct.unpack('>Q', h[8:16])[0]
            hs = 16
        if size == 0 or size < hs:
            if btype == b'mdat':
                return limit, False, 'mdat sin cerrar (grabación interrumpida)'
            break
        if btype == b'mdat' and pos + size > limit and b'moov' not in seen:
            return limit, False, 'mdat con tamaño no actualizado'
        if b'moov' in seen and b'mdat' in seen and btype not in TRAILING_BOXES:
            break
        seen.add(btype)
        pos += size
        if pos > src.size:
            return src.size, False, 'cortado al final del disco'
    if b'moov' in seen and b'mdat' in seen:
        return pos, True, 'completo'
    if b'mdat' in seen and b'moov' not in seen:
        return min(pos, limit), False, 'falta el índice (moov)'
    if b'mdat' not in seen:
        return min(limit, start + (1 << 20)), False, 'solo cabecera; los datos pueden estar más adelante'
    return min(pos, limit), False, 'estructura incompleta'


def mxf_extent(src, start, limit):
    head = src.read(start, 4096)
    klv = mxf.read_klv(head, 0)
    if not klv:
        return limit, False, 'cabecera MXF ilegible'
    hl = klv[1]
    v = head[hl:hl + 48]
    _maj, _min, _kag, _this, _prev, footer = struct.unpack('>HHIQQQ', v[:32])
    if footer and start + footer < src.size:
        pos = start + footer
        for _ in range(64):
            h = src.read(pos, 32)
            k = mxf.read_klv(h, 0)
            if not k:
                break
            key, khl, length = k
            pos += khl + length
            if key == mxf.RIP_KEY:
                return pos, True, 'completo'
        return min(pos, limit), False, 'footer sin RIP'
    return limit, False, 'MXF sin cerrar (grabación interrumpida)'


def _friendly_name(path, name, fecha, number):
    """Renombra con la fecha de la cámara: 2026-10-27_16-42_0002.ARW (si se conoce)."""
    m = re.match(r'(\d{4})[-:](\d\d)[-:](\d\d)[ T_](\d\d):?(\d\d)', fecha or '')
    if not m:
        return path, name
    stem, ext = os.path.splitext(name)
    suffix = '_INCOMPLETO' if stem.endswith('_INCOMPLETO') else ''
    new = '%s-%s-%s_%s-%s_%04d%s%s' % (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5),
                                       number, suffix, ext)
    target = os.path.join(os.path.dirname(path), new)
    try:
        os.replace(path, target)
    except OSError:
        return path, name
    return target, new


def _same_device(source, out_dir):
    """True si la carpeta de salida está en la misma tarjeta que se rescata."""
    out = os.path.abspath(out_dir)
    if _device_drive(source):
        return _device_drive(source) == _drive_of(out)
    if sys.platform == 'darwin' and source.startswith('/dev/'):
        from . import devices
        return any(out == m or out.startswith(m.rstrip('/') + '/')
                   for m in devices.mount_points(source))
    return False


def carve(source, out_dir, log=print, progress=None, reference=None, compare_dir=None,
          on_clip=None, should_stop=None, start=0, end=None, kinds=('video', 'foto')):
    """Busca y extrae clips de vídeo y fotos (kinds: 'video', 'foto').
    Con compare_dir se detiene en cuanto encuentra todos los archivos de esa carpeta y
    comprueba que los recuperados son idénticos byte a byte.
    on_clip(dict) se llama con cada archivo guardado; should_stop() permite cancelar."""
    want_video = 'video' in kinds
    want_photo = 'foto' in kinds
    if _same_device(source, out_dir):
        raise ValueError('La carpeta de salida no puede estar en la misma unidad que se rescata: '
                         'escribir ahí podría sobrescribir los vídeos borrados.')
    os.makedirs(out_dir, exist_ok=True)
    targets = load_targets(compare_dir, kinds) if compare_dir else None
    wanted = sum(len(v) for v in targets.values()) if targets else 0
    matched = {}
    if targets:
        log('Archivos a localizar: %d (de %s)' % (wanted, compare_dir))
    src = Source(source)
    results = []

    def new_name(kind, start, suffix='', ext=None):
        ext = (ext or kind).upper()
        return '%s_%04d_%012X%s.%s' % (ext, len(results) + 1, start, suffix, ext)

    def report(out, name, kind, start, size, complete, note, brand):
        info = {}
        if kind != 'xml':
            from . import clipinfo
            info = photos.summary(out) if kind in PHOTO_KINDS else clipinfo.summary(out)
            out, name = _friendly_name(out, name, info.get('fecha'), len(results) + 1)
        extra = ''
        same_as = None
        if targets and kind != 'xml' and size in targets:
            digest = _sha256(out)
            for tname, tsha in targets[size]:
                if tsha == digest and tname not in matched:
                    matched[tname] = name
                    same_as = tname
                    extra = '  = %s (idéntico)' % tname
        log('  %s  %8.1f MB  %s%s%s' % (name, size / 1048576, note,
                                        ' [%s]' % brand if brand else '', extra))
        result = {'path': out, 'kind': kind, 'complete': complete, 'note': note,
                  'offset': start, 'size': size, 'identico_a': same_as}
        results.append(result)
        if on_clip and kind != 'xml':
            on_clip(dict(result, **info))

    def save(start, end, kind, complete, note, brand, ext=None):
        name = new_name(kind, start, '' if complete else '_INCOMPLETO', ext)
        out = os.path.join(out_dir, name)
        with open(out, 'wb') as o:
            pos = start
            while pos < end:
                chunk = src.read(pos, min(BLOCK, end - pos))
                if not chunk:
                    break
                o.write(chunk)
                pos += len(chunk)
        report(out, name, kind, start, end - start, complete, note, brand)

    def extent(kind, start, limit):
        return (mp4_extent if kind == 'mp4' else mxf_extent)(src, start, limit)

    def try_pair(clip, om):
        shift = sonycard.locate(src, clip['start'], clip['mdat_off'], om, log)
        if shift is None:
            return False
        name = new_name('mp4', clip['start'])
        out = os.path.join(out_dir, name)
        _size, origin = sonycard.rebuild(src, clip['start'], clip['mdat_off'], clip['mdat_hs'],
                                         clip['mdat_decl'], shift, om, out)
        om.used = True
        clip['paired'] = True
        report(out, name, 'mp4', clip['start'], os.path.getsize(out), True,
               'reconstruido (índice en 0x%X, %d fotogramas, %s)' % (om.pos, om.frames, origin),
               clip['brand'])
        return True

    waiting = []        # cabeceras MP4 cuyo índice está en otra parte de la tarjeta
    orphans = []        # índices (moov) sueltos aún sin pareja
    try:
        log('Origen: %s (%.2f GB)' % (src.label, src.size / 1e9))
        log('Buscando %s...' % ' y '.join(
            t for t, on in (('clips de vídeo', want_video), ('fotos', want_photo)) if on))
        scanner = SignatureScanner(src, progress, should_stop, start, end)
        if start or end:
            log('Zona: de %.1f GB a %.1f GB' % (scanner.begin / 1e9, scanner.end / 1e9))
        covered_until = -1
        for start, kind, brand in scanner:
            if start < covered_until:
                continue        # firma falsa dentro de un clip o foto ya recuperados
            if kind in PHOTO_KINDS:
                for clip in waiting:
                    if clip['limit'] is None:
                        clip['limit'] = start
                if not want_photo:
                    continue
                r = photos.extent(kind, src, start, src.size)
                if r is None:
                    continue    # firma suelta (p.ej. un Exif dentro de otro archivo)
                end, complete, note, ext = r
                save(start, end, kind, complete, note, brand, ext)
                if complete:
                    covered_until = end
                    scanner.skip_to(end)
            elif not want_video:
                continue
            elif kind == 'xml':
                if targets:
                    continue
                data = src.read(start, 65536)
                e = data.find(b'</NonRealTimeMeta>')
                if e >= 0:
                    e += len(b'</NonRealTimeMeta>')
                    e += 2 if data[e:e + 2] == b'\r\n' else 1 if data[e:e + 1] == b'\n' else 0
                    save(start, start + e, 'xml', True, 'metadatos', '')
                continue
            elif kind == 'moov':
                om = sonycard.read_orphan_moov(src, start)
                if om is None:
                    continue
                # su clip suele ser la cabecera más reciente sin emparejar
                for clip in reversed([c for c in waiting if not c['paired']][-4:]):
                    if try_pair(clip, om):
                        break
                if not om.used:
                    orphans = (orphans + [om])[-16:]
            else:
                for clip in waiting:
                    if clip['limit'] is None:
                        clip['limit'] = start
                end, complete, note = extent(kind, start, min(start + MAX_INCOMPLETE, src.size))
                if complete:
                    save(start, end, kind, complete, note, brand)
                    covered_until = end
                    scanner.skip_to(end)
                else:
                    hdr = sonycard.clip_header(src, start) if kind == 'mp4' else None
                    clip = {'start': start, 'kind': kind, 'brand': brand, 'paired': False,
                            'limit': None, 'mdat_off': None}
                    if hdr:
                        clip['mdat_off'], clip['mdat_hs'], clip['mdat_decl'] = hdr
                        for om in reversed(orphans):
                            if not om.used and try_pair(clip, om):
                                break
                    waiting = [c for c in waiting + [clip] if not c['paired']]
            if targets and len(matched) == wanted:
                log('Encontrados los %d clips buscados: búsqueda detenida en %.1f GB de %.1f GB.'
                    % (wanted, scanner.pos / 1e9, src.size / 1e9))
                break
        if scanner.stopped:
            log('Búsqueda detenida por el usuario en %.1f GB de %.1f GB.' % (
                scanner.pos / 1e9, src.size / 1e9))
        if not targets:
            # clips sin índice: se guardan tal cual para repararlos con una referencia
            for clip in waiting:
                if clip['paired']:
                    continue
                limit = clip['limit'] or min(clip['start'] + MAX_INCOMPLETE, src.size)
                end, complete, note = extent(clip['kind'], clip['start'], limit)
                if end - clip['start'] <= LONE_HEADER:
                    # la A9 III deja copias de cabecera sueltas (sin vídeo) junto a cada índice
                    log('  cabecera sin datos en 0x%X: descartada' % clip['start'])
                    continue
                save(clip['start'], end, clip['kind'], complete, note, clip['brand'])
    finally:
        src.close()
    if targets:
        log('')
        log('Comparación con %s:' % compare_dir)
        for size in sorted(targets):
            for tname, _sha in targets[size]:
                log('  %-28s %s' % (tname, ('IDÉNTICO → ' + matched[tname]) if tname in matched
                                    else 'no encontrado'))

    if reference:
        from . import repair
        for r in results:
            if r['kind'] == 'mp4' and not r['complete']:
                out = r['path'].replace('_INCOMPLETO.MP4', '_REPARADO.MP4')
                log('')
                log('Reparando %s ...' % os.path.basename(r['path']))
                try:
                    ref = repair.pick_reference(r['path'], reference, log)
                    repair.repair(r['path'], ref, out, log=log)
                    r['repaired'] = out
                    if on_clip:
                        from . import clipinfo
                        on_clip(dict(r, path=out, complete=True, note='reparado con referencia',
                                     **clipinfo.summary(out)))
                except repair.RepairError as e:
                    log('  no se pudo reparar: %s' % e)
    log('')
    log('Listo: %d clips de vídeo, %d fotos, %d incompletos, %d XML de metadatos.' % (
        sum(1 for r in results if r['complete'] and r['kind'] in ('mp4', 'mxf')),
        sum(1 for r in results if r['complete'] and r['kind'] in PHOTO_KINDS),
        sum(1 for r in results if not r['complete']),
        sum(1 for r in results if r['kind'] == 'xml')))
    return results


def make_image(source, image_path, log=print, progress=None, should_stop=None):
    """Copia sector a sector la tarjeta a un .img (los sectores ilegibles se rellenan con ceros)."""
    if _same_device(source, os.path.dirname(os.path.abspath(image_path))):
        raise ValueError('La imagen no puede guardarse en la misma tarjeta.')
    src = Source(source)
    bad = 0
    try:
        log('Creando imagen de %s (%.2f GB) en %s' % (src.label, src.size / 1e9, image_path))
        with open(image_path, 'wb') as o:
            pos = 0
            while pos < src.size:
                if should_stop and should_stop():
                    log('Copia detenida por el usuario: la imagen está incompleta.')
                    break
                n = min(BLOCK, src.size - pos)
                try:
                    data = src.read(pos, n)
                except OSError:
                    data = bytearray()
                    for s in range(pos, pos + n, 4096):
                        try:
                            data += src.read(s, min(4096, pos + n - s))
                        except OSError:
                            data += b'\0' * min(4096, pos + n - s)
                            bad += 1
                o.write(data)
                pos += n
                if progress:
                    progress(pos / src.size)
    finally:
        src.close()
    log('Imagen creada. Bloques ilegibles: %d' % bad)
    return bad
