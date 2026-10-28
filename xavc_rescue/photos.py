"""Fotos: RAW (Sony ARW y demás TIFF), HEIF (Sony .HIF / .HEIC) y JPEG.

Para rescatarlas de una tarjeta formateada hay que saber dónde termina cada archivo:
  * ARW / TIFF: el punto más lejano que referencian sus directorios (datos RAW, vista
    previa JPEG, tablas de Sony), más el relleno de ceros hasta 4 KB que añade la cámara.
  * HEIF: la cadena de cajas ISO (ftyp → meta → free → mdat).
  * JPEG: los marcadores hasta EOI, más las imágenes extra declaradas en MPF (Sony añade
    una vista previa grande tras la foto principal).
"""
import struct

from . import mp4

HEIF_BRANDS = {b'heix', b'heic', b'heim', b'heis', b'hevc', b'hevx', b'mif1', b'msf1', b'avif'}
TIFF_TYPES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
POINTER_TAGS = (0x8769, 0x8825, 0xA005, 0x014A)        # Exif, GPS, Interop, SubIFDs
DATA_PAIRS = ((0x0111, 0x0117), (0x0144, 0x0145), (0x0201, 0x0202))  # strips, tiles, JPEG
MAX_PHOTO = 1 << 30
MAX_JPEG = 128 << 20        # sin EOI antes de esto, la firma JPEG era falsa o la foto está rota
PAD = 4096


def _pad_zeros(read, end, limit):
    """Sony rellena con ceros hasta múltiplo de 4 KB: lo incluimos si de verdad son ceros."""
    target = -(-end // PAD) * PAD
    if end < target <= limit and not read(end, target - end).strip(b'\0'):
        return target
    return end


# ---------------------------------------------------------------- TIFF / ARW

class _Tiff:
    def __init__(self, read, limit):
        self.read = read
        self.limit = limit
        head = read(0, 8)
        self.bo = '<' if head[:2] == b'II' else '>'
        self.ifd0 = struct.unpack(self.bo + 'I', head[4:8])[0]
        self.end = 8
        self.seen = set()
        self.tags0 = {}

    def u(self, data, fmt, off=0):
        return struct.unpack_from(self.bo + fmt, data, off)[0]

    def values(self, t, n, raw_or_off):
        size = TIFF_TYPES.get(t, 1)
        data = raw_or_off if size * n <= 4 else self.read(raw_or_off, size * n)
        fmt = {2: 'H', 4: 'I'}.get(size if t in (3, 4, 8, 9, 13) else 0)
        if fmt is None:
            return []
        return [self.u(data, fmt, k * size) for k in range(min(n, len(data) // size))]

    def walk(self, pos, depth=0, root=False):
        if pos in self.seen or pos < 8 or pos + 2 > self.limit or depth > 8 or len(self.seen) > 64:
            return
        self.seen.add(pos)
        count = self.u(self.read(pos, 2), 'H')
        if count == 0 or count > 512:
            return
        table = self.read(pos + 2, count * 12 + 4)
        if len(table) < count * 12 + 4:
            return
        self.end = max(self.end, pos + 2 + count * 12 + 4)
        tags = {}
        for i in range(count):
            e = i * 12
            tag, t, n = struct.unpack_from(self.bo + 'HHI', table, e)
            size = TIFF_TYPES.get(t, 1) * n
            raw = table[e + 8:e + 12]
            if size > 4:
                off = self.u(raw, 'I')
                if off + size <= self.limit:
                    self.end = max(self.end, off + size)
                tags[tag] = (t, n, off)
            else:
                tags[tag] = (t, n, raw)
        if root:
            self.tags0 = tags
        for a, b in DATA_PAIRS:
            if a in tags and b in tags:
                for o, l in zip(self.values(*tags[a]), self.values(*tags[b])):
                    if o + l <= self.limit:
                        self.end = max(self.end, o + l)
        for tag in POINTER_TAGS:
            if tag in tags:
                for p in self.values(*tags[tag]):
                    self.walk(p, depth + 1)
        if 0x927C in tags and isinstance(tags[0x927C][2], int):     # MakerNote de Sony
            off = tags[0x927C][2]
            self.walk(off + (12 if self.read(off, 4) == b'SONY' else 0), depth + 1)
        nxt = self.u(table, 'I', count * 12)
        if nxt:
            self.walk(nxt, depth + 1)

    def string(self, tag):
        if tag not in self.tags0:
            return None
        t, n, v = self.tags0[tag]
        data = v if isinstance(v, bytes) else self.read(v, n)
        return data[:n].split(b'\0')[0].decode('latin1').strip() or None


def tiff_extent(read, limit):
    """(fin, completo, nota, extensión) de un TIFF/ARW que empieza en 0 de read()."""
    t = _Tiff(read, limit)
    if not 8 <= t.ifd0 < 1 << 20:
        return None
    t.walk(t.ifd0, root=True)
    if len(t.seen) < 2 or t.end < 64 * 1024:
        return None        # una cabecera TIFF suelta (p.ej. dentro de un Exif), no una foto
    make = (t.string(0x010F) or '').upper()
    ext = 'ARW' if 'SONY' in make else 'DNG' if 0xC612 in t.tags0 else 'TIF'
    end = _pad_zeros(read, t.end, limit) if 'SONY' in make else t.end
    return end, True, 'foto RAW (%s)' % (t.string(0x0110) or make or 'TIFF'), ext


# ---------------------------------------------------------------- HEIF

def heif_extent(read, limit):
    pos = 0
    seen = set()
    for _ in range(32):
        h = read(pos, 16)
        if len(h) < 8:
            break
        size, btype = struct.unpack('>I4s', h[:8])
        hs = 8
        if not mp4.is_box_type(btype):
            break
        if size == 1:
            size = struct.unpack('>Q', h[8:16])[0]
            hs = 16
        if size < hs:
            break
        if pos + size > limit:
            return limit, False, 'HEIF cortado', None
        seen.add(btype)
        pos += size
        if btype == b'mdat':
            break
    if b'meta' not in seen or b'mdat' not in seen:
        return None
    brand = read(8, 4)
    ext = 'HIF' if brand == b'heix' else 'AVIF' if brand == b'avif' else 'HEIC'
    end = _pad_zeros(read, pos, limit) if brand == b'heix' else pos
    return end, True, 'foto HEIF', ext


# ---------------------------------------------------------------- JPEG

def _mpf_extent(read, app2_data_off, length):
    """Fin de las imágenes declaradas en un segmento APP2 'MPF' (offsets relativos al TIFF)."""
    data = read(app2_data_off, length)
    if not data.startswith(b'MPF\0'):
        return 0
    base = app2_data_off + 4
    tiff = data[4:]
    bo = '<' if tiff[:2] == b'II' else '>'
    try:
        ifd = struct.unpack_from(bo + 'I', tiff, 4)[0]
        n = struct.unpack_from(bo + 'H', tiff, ifd)[0]
        end = 0
        for i in range(n):
            tag, t, cnt, val = struct.unpack_from(bo + 'HHII', tiff, ifd + 2 + i * 12)
            if tag == 0xB002:                     # MPEntry: 16 bytes por imagen
                for k in range(cnt // 16):
                    _attr, size, off, _d1, _d2 = struct.unpack_from(bo + 'IIIHH', tiff, val + k * 16)
                    if off:
                        end = max(end, base + off + size)
        return end
    except struct.error:
        return 0


def jpeg_extent(read, limit, scan_chunk=1 << 20):
    pos = 2
    mpf_end = 0
    while pos + 4 <= limit:
        m = read(pos, 4)
        if len(m) < 4 or m[0] != 0xFF:
            return None
        marker = m[1]
        if marker == 0xFF:
            pos += 1
            continue
        length = struct.unpack('>H', m[2:4])[0]
        if marker == 0xDA:                        # SOS: datos de imagen hasta EOI
            pos += 2 + length
            break
        if length < 2:
            return None
        if marker == 0xE2:
            mpf_end = max(mpf_end, _mpf_extent(read, pos + 4, length - 2))
        pos += 2 + length
    else:
        return None
    # buscar EOI (FF D9) en los datos comprimidos
    while pos < limit:
        chunk = read(pos, min(scan_chunk, limit - pos) + 1)
        if len(chunk) < 2:
            break
        i = chunk.find(b'\xff\xd9')
        if i >= 0:
            # sin relleno: las miniaturas JPEG de Sony terminan justo en EOI (comprobado en tarjeta)
            end = max(pos + i + 2, mpf_end if mpf_end <= limit else 0)
            return end, True, 'foto JPEG', 'JPG'
        pos += len(chunk) - 1
        if pos > MAX_JPEG:
            return None
    return None


# ---------------------------------------------------------------- detección

def is_tiff_start(data, i):
    head = data[i:i + 8]
    if head[:4] not in (b'II*\0', b'MM\0*'):
        return False
    off = struct.unpack(('<' if head[0] == 0x49 else '>') + 'I', head[4:8])[0]
    return 8 <= off < 65536


def is_jpeg_start(data, i):
    return data[i:i + 3] == b'\xff\xd8\xff' and data[i + 3:i + 4] in (b'\xe0', b'\xe1', b'\xdb', b'\xee')


def extent(kind, src, start, limit):
    """Fin de la foto que empieza en start. None si la firma era falsa."""
    def read(off, n):
        if off < 0 or start + off >= limit:
            return b''
        return src.read(start + off, min(n, limit - start - off))
    span = min(limit - start, MAX_PHOTO)
    fn = {'raw': tiff_extent, 'heif': heif_extent, 'jpg': jpeg_extent}[kind]
    try:
        r = fn(read, span)
    except (struct.error, ValueError):
        return None
    if r is None:
        return None
    end, complete, note, ext = r
    return start + end, complete, note, ext or {'raw': 'TIF', 'heif': 'HEIC', 'jpg': 'JPG'}[kind]


# ---------------------------------------------------------------- información para la tabla

def _exif_from(blob):
    """Busca un TIFF Exif dentro de unos bytes y devuelve (cámara, fecha)."""
    for sig in (b'II*\0', b'MM\0*'):
        i = blob.find(sig)
        while 0 <= i < len(blob) - 8:
            sub = blob[i:]
            t = _Tiff(lambda o, n: sub[o:o + n], len(sub))
            try:
                if 8 <= t.ifd0 < len(sub):
                    t.walk(t.ifd0, root=True)
                    model = t.string(0x0110)
                    date = t.string(0x0132)
                    if model or date:
                        return model, date
            except (struct.error, IndexError):
                pass
            i = blob.find(sig, i + 1)
    return None, None


def summary_blob(head, ext):
    """Cámara, fecha y formato a partir del principio de la foto (leído de la tarjeta)."""
    info = {'camara': None, 'fecha': None, 'codec': None, 'perfil': None, 'duracion': None}
    model, date = _exif_from(head[:256 * 1024])
    info['camara'] = model
    if date:
        info['fecha'] = date[:16].replace(':', '-', 2)
    ext = ext.upper()
    info['codec'] = {'ARW': 'RAW ARW', 'DNG': 'RAW DNG', 'TIF': 'TIFF', 'HIF': 'HEIF',
                     'HEIC': 'HEIF', 'AVIF': 'AVIF', 'JPG': 'JPEG'}.get(ext, ext)
    if model:
        info['codec'] += ' · ' + model
    return info


def summary(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(256 * 1024)
    except OSError:
        head = b''
    return summary_blob(head, path.rsplit('.', 1)[-1])


def embedded_jpeg(data):
    """La mayor JPEG completa dentro de unos bytes: la vista previa de un ARW, la miniatura
    de un HIF o la miniatura Exif de un JPG (su imagen principal no cabe en lo leído)."""
    best = None
    i = data.find(b'\xff\xd8\xff')
    tries = 0
    while i >= 0 and tries < 24:
        tries += 1
        if is_jpeg_start(data, i):
            def read(off, n, _i=i):
                return data[_i + off:_i + off + n]
            try:
                r = jpeg_extent(read, len(data) - i, scan_chunk=len(data))
            except (struct.error, ValueError):
                r = None
            if r and r[1] and (best is None or r[0] > best[1] - best[0]):
                best = (i, i + r[0])
        i = data.find(b'\xff\xd8\xff', i + 3)
    return data[best[0]:best[1]] if best else None
