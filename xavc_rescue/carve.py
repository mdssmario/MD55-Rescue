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

from . import clipinfo, mp4, mxf, photos, sonycard

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


def _plan_size(plan):
    return plan['end'] - plan['start'] if plan['type'] == 'range' else plan['out_size']


def proposed_name(item):
    """Nombre del archivo recuperado: con la fecha de la cámara si se conoce."""
    m = re.match(r'(\d{4})[-:](\d\d)[-:](\d\d)[ T_](\d\d):?(\d\d)', item['info'].get('fecha') or '')
    suffix = '' if item['complete'] else '_INCOMPLETO'
    if m:
        return '%s-%s-%s_%s-%s_%04d%s.%s' % (m.group(1), m.group(2), m.group(3), m.group(4),
                                             m.group(5), item['id'], suffix, item['ext'])
    return '%s_%04d_%012X%s.%s' % (item['ext'], item['id'], item['offset'], suffix, item['ext'])


def scan(src, kinds=('video', 'foto'), log=print, progress=None, should_stop=None, start=0, end=None,
         on_item=None, preview_dir=None, include_xml=False, emit_incomplete=True):
    """Explora el origen SIN escribir nada y devuelve lo que se puede recuperar.

    Cada elemento lleva su plan de extracción (un tramo contiguo o el montaje de un clip
    repartido por la tarjeta), sus metadatos (fecha, cámara, códec, perfil, duración) y, en
    las fotos, una vista previa. on_item(item) se llama con cada uno; si devuelve True, la
    exploración se detiene."""
    want_video = 'video' in kinds
    want_photo = 'foto' in kinds
    items = []
    state = {'stop': False}

    def emit(kind, ext, plan, complete, note, brand, info=None, preview=None):
        item = {'id': len(items) + 1, 'kind': kind, 'ext': ext.upper(), 'plan': plan,
                'complete': complete, 'note': note, 'brand': brand,
                'offset': plan.get('start', plan.get('header_pos', 0)), 'size': _plan_size(plan),
                'info': info or {}, 'preview': preview}
        item['name'] = proposed_name(item)
        items.append(item)
        log('  %s  %8.1f MB  %s%s' % (item['name'], item['size'] / 1048576, note,
                                      ' [%s]' % brand if brand else ''))
        if on_item and on_item(item):
            state['stop'] = True

    def video_range(s, e, kind, complete, note, brand):
        head = src.read(s, min(65536, e - s))
        tail = src.read(max(s, e - 262144), min(262144, e - s))
        info = clipinfo.summary_blobs([head, tail]) if kind == 'mp4' else {}
        emit(kind, kind, {'type': 'range', 'start': s, 'end': e}, complete, note, brand, info)

    def photo(s, kind, brand):
        r = photos.extent(kind, src, s, src.size)
        if r is None:
            return None     # firma suelta (p.ej. un Exif dentro de otro archivo)
        e, complete, note, ext = r
        head = src.read(s, min(e - s, 2621440))
        preview = None
        jpeg = photos.embedded_jpeg(head)
        if jpeg and preview_dir:
            os.makedirs(preview_dir, exist_ok=True)
            preview = os.path.join(preview_dir, 'previa_%04d.jpg' % (len(items) + 1))
            with open(preview, 'wb') as f:
                f.write(jpeg)
        emit(kind, ext, {'type': 'range', 'start': s, 'end': e}, complete, note, brand,
             photos.summary_blob(head, ext), preview)
        return e if complete else None

    def try_pair(clip, om):
        shift = sonycard.locate(src, clip['start'], clip['mdat_off'], om, log)
        if shift is None:
            return False
        p = sonycard.plan(src, clip['start'], clip['mdat_off'], clip['mdat_hs'], clip['mdat_decl'],
                          shift, om)
        om.used = True
        clip['paired'] = True
        emit('mp4', 'mp4', p, True, 'reconstruido (índice en 0x%X, %d fotogramas, %s)'
             % (om.pos, om.frames, p['origin']), clip['brand'],
             clipinfo.summary_blobs([om.moov_bytes + om.tail_bytes]))
        return True

    def extent(kind, s, limit):
        return (mp4_extent if kind == 'mp4' else mxf_extent)(src, s, limit)

    waiting = []        # cabeceras MP4 cuyo índice está en otra parte de la tarjeta
    orphans = []        # índices (moov) sueltos aún sin pareja
    log('Origen: %s (%.2f GB)' % (src.label, src.size / 1e9))
    log('Buscando %s...' % ' y '.join(
        t for t, on in (('clips de vídeo', want_video), ('fotos', want_photo)) if on))
    scanner = SignatureScanner(src, progress, should_stop, start, end)
    if start or end:
        log('Zona: de %.1f GB a %.1f GB' % (scanner.begin / 1e9, scanner.end / 1e9))
    covered_until = -1
    for pos, kind, brand in scanner:
        if pos < covered_until:
            continue        # firma falsa dentro de un clip o foto ya localizados
        if kind in PHOTO_KINDS:
            for clip in waiting:
                if clip['limit'] is None:
                    clip['limit'] = pos
            if want_photo:
                e = photo(pos, kind, brand)
                if e:
                    covered_until = e
                    scanner.skip_to(e)
        elif not want_video:
            continue
        elif kind == 'xml':
            if include_xml:
                data = src.read(pos, 65536)
                e = data.find(b'</NonRealTimeMeta>')
                if e >= 0:
                    e += len(b'</NonRealTimeMeta>')
                    e += 2 if data[e:e + 2] == b'\r\n' else 1 if data[e:e + 1] == b'\n' else 0
                    emit('xml', 'xml', {'type': 'range', 'start': pos, 'end': pos + e}, True,
                         'metadatos', '')
        elif kind == 'moov':
            om = sonycard.read_orphan_moov(src, pos)
            if om is not None:
                # su clip suele ser la cabecera más reciente sin emparejar
                for clip in reversed([c for c in waiting if not c['paired']][-4:]):
                    if try_pair(clip, om):
                        break
                if not om.used:
                    orphans = (orphans + [om])[-16:]
        else:
            for clip in waiting:
                if clip['limit'] is None:
                    clip['limit'] = pos
            e, complete, note = extent(kind, pos, min(pos + MAX_INCOMPLETE, src.size))
            if complete:
                video_range(pos, e, kind, True, note, brand)
                covered_until = e
                scanner.skip_to(e)
            else:
                hdr = sonycard.clip_header(src, pos) if kind == 'mp4' else None
                clip = {'start': pos, 'kind': kind, 'brand': brand, 'paired': False,
                        'limit': None, 'mdat_off': None}
                if hdr:
                    clip['mdat_off'], clip['mdat_hs'], clip['mdat_decl'] = hdr
                    for om in reversed(orphans):
                        if not om.used and try_pair(clip, om):
                            break
                waiting = [c for c in waiting + [clip] if not c['paired']]
        if state['stop']:
            break
    if scanner.stopped:
        log('Exploración detenida por el usuario en %.1f GB de %.1f GB.' % (
            scanner.pos / 1e9, src.size / 1e9))
    if emit_incomplete and not state['stop']:
        # clips sin índice: se ofrecen tal cual para repararlos con una referencia
        for clip in waiting:
            if clip['paired']:
                continue
            limit = clip['limit'] or min(clip['start'] + MAX_INCOMPLETE, src.size)
            e, complete, note = extent(clip['kind'], clip['start'], limit)
            if e - clip['start'] <= LONE_HEADER:
                # la A9 III deja copias de cabecera sueltas (sin vídeo) junto a cada índice
                log('  cabecera sin datos en 0x%X: descartada' % clip['start'])
                continue
            video_range(clip['start'], e, clip['kind'], complete, note, clip['brand'])
    return items


def extract_item(src, item, out_dir, progress=None):
    """Guarda un elemento de la exploración. Devuelve la ruta del archivo creado."""
    path = os.path.join(out_dir, item['name'])
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = '%s_%d%s' % (base, n, ext)
        n += 1
    p = item['plan']
    if p['type'] == 'sony':
        sonycard.write_plan(src, p, path, BLOCK, progress)
        return path
    with open(path, 'wb') as o:
        pos = p['start']
        while pos < p['end']:
            chunk = src.read(pos, min(BLOCK, p['end'] - pos))
            if not chunk:
                break
            o.write(chunk)
            pos += len(chunk)
            if progress:
                progress(len(chunk))
    return path


class _Verifier:
    """Compara lo recuperado con una carpeta de originales (SHA-256)."""

    def __init__(self, compare_dir, kinds, log):
        self.targets = load_targets(compare_dir, kinds) if compare_dir else None
        self.wanted = sum(len(v) for v in self.targets.values()) if self.targets else 0
        self.matched = {}
        self.compare_dir = compare_dir
        self.log = log
        if self.targets:
            log('Archivos a localizar: %d (de %s)' % (self.wanted, compare_dir))

    def check(self, path, size):
        if not self.targets or size not in self.targets:
            return None
        digest = _sha256(path)
        for tname, tsha in self.targets[size]:
            if tsha == digest and tname not in self.matched:
                self.matched[tname] = os.path.basename(path)
                self.log('     = %s (idéntico)' % tname)
                return tname
        return None

    def complete(self):
        return bool(self.targets) and len(self.matched) == self.wanted

    def report(self):
        if not self.targets:
            return
        self.log('')
        self.log('Comparación con %s:' % self.compare_dir)
        for size in sorted(self.targets):
            for tname, _sha in self.targets[size]:
                self.log('  %-28s %s' % (tname, ('IDÉNTICO → ' + self.matched[tname])
                                         if tname in self.matched else 'no encontrado'))


def _repair_incomplete(saved, reference, log, on_clip):
    from . import repair
    for r in saved:
        if r['kind'] == 'mp4' and not r['complete']:
            base, ext = os.path.splitext(r['path'])
            out = (base.replace('_INCOMPLETO', '_REPARADO', 1) if '_INCOMPLETO' in base
                   else base + '_REPARADO') + ext
            log('')
            log('Reparando %s ...' % os.path.basename(r['path']))
            try:
                ref = repair.pick_reference(r['path'], reference, log)
                repair.repair(r['path'], ref, out, log=log)
                r['repaired'] = out
                if on_clip:
                    on_clip(dict(r, path=out, complete=True, note='reparado con referencia',
                                 **clipinfo.summary(out)))
            except repair.RepairError as e:
                log('  no se pudo reparar: %s' % e)


def _public(item, **extra):
    """Datos de un elemento para la interfaz (sin el plan interno)."""
    d = {k: item[k] for k in ('id', 'kind', 'ext', 'complete', 'note', 'offset', 'size', 'name',
                              'preview')}
    d.update(item['info'])
    d.update(extra)
    return d


def _check_output(source, out_dir):
    if _same_device(source, out_dir):
        raise ValueError('La carpeta de salida no puede estar en la misma unidad que se rescata: '
                         'escribir ahí podría sobrescribir los vídeos borrados.')
    os.makedirs(out_dir, exist_ok=True)


def _summary(log, saved):
    log('')
    log('Listo: %d clips de vídeo, %d fotos, %d incompletos, %d XML de metadatos.' % (
        sum(1 for r in saved if r['complete'] and r['kind'] in ('mp4', 'mxf')),
        sum(1 for r in saved if r['complete'] and r['kind'] in PHOTO_KINDS),
        sum(1 for r in saved if not r['complete']),
        sum(1 for r in saved if r['kind'] == 'xml')))


def carve(source, out_dir, log=print, progress=None, reference=None, compare_dir=None,
          on_clip=None, should_stop=None, start=0, end=None, kinds=('video', 'foto')):
    """Explora y recupera todo lo que encuentra (sin selección previa).
    Con compare_dir se detiene en cuanto encuentra todos los archivos de esa carpeta y
    comprueba que los recuperados son idénticos byte a byte."""
    _check_output(source, out_dir)
    verifier = _Verifier(compare_dir, kinds, log)
    src = Source(source)
    saved = []

    def on_item(item):
        path = extract_item(src, item, out_dir)
        same = verifier.check(path, item['size']) if item['kind'] != 'xml' else None
        r = _public(item, path=path, identico_a=same)
        saved.append(r)
        if on_clip and item['kind'] != 'xml':
            on_clip(r)
        if verifier.complete():
            log('Encontrados los %d archivos buscados: búsqueda detenida.' % verifier.wanted)
            return True
        return False

    try:
        scan(src, kinds, log, progress, should_stop, start, end, on_item,
             include_xml=not verifier.targets, emit_incomplete=not verifier.targets)
    finally:
        src.close()
    verifier.report()
    if reference:
        _repair_incomplete(saved, reference, log, on_clip)
    _summary(log, saved)
    return saved


def explore(source, out_dir, wait_selection, log=print, progress=None, should_stop=None,
            on_item=None, on_clip=None, compare_dir=None, reference=None, start=0, end=None,
            kinds=('video', 'foto'), preview_dir=None):
    """Explora sin escribir, espera a que el usuario elija y recupera solo lo elegido.

    wait_selection(items) devuelve la lista de ids a recuperar (o None para salir)."""
    _check_output(source, out_dir)
    src = Source(source)
    saved = []
    try:
        items = scan(src, kinds, log, progress, should_stop, start, end,
                     (lambda it: on_item(_public(it)) if on_item else None), preview_dir)
        log('Exploración terminada: %d archivos encontrados.' % len(items))
        ids = wait_selection(items)
        if ids is None:
            log('No se recupera nada.')
            return []
        chosen = [it for it in items if it['id'] in set(ids)]
        total = sum(it['size'] for it in chosen) or 1
        done = [0]

        def step(n):
            done[0] += n
            if progress:
                progress(min(1.0, done[0] / total))

        verifier = _Verifier(compare_dir, kinds, log)
        log('Recuperando %d archivos (%.1f GB)…' % (len(chosen), total / 1e9))
        for it in chosen:
            if should_stop and should_stop():
                log('Recuperación detenida por el usuario.')
                break
            path = extract_item(src, it, out_dir, step)
            same = verifier.check(path, it['size'])
            r = _public(it, path=path, identico_a=same)
            saved.append(r)
            log('  guardado %s' % os.path.basename(path))
            if on_clip:
                on_clip(r)
        verifier.report()
    finally:
        src.close()
    if reference:
        _repair_incomplete(saved, reference, log, on_clip)
    _summary(log, saved)
    return saved


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
