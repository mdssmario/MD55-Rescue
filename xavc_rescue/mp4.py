"""Lectura y escritura mínima de cajas ISO-BMFF (MP4 / MOV / XAVC S)."""
import struct

CONTAINER_TYPES = {
    b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts', b'dinf',
    b'udta', b'mvex', b'tref',
}


class Box:
    __slots__ = ('type', 'payload', 'children')

    def __init__(self, btype, payload=b'', children=None):
        self.type = btype
        self.payload = payload
        self.children = children

    def child(self, btype):
        for c in self.children or ():
            if c.type == btype:
                return c
        return None

    def path(self, *types):
        box = self
        for t in types:
            box = box.child(t)
            if box is None:
                return None
        return box

    def all(self, btype):
        out = []
        for c in self.children or ():
            if c.type == btype:
                out.append(c)
            out.extend(c.all(btype))
        return out

    def clone(self):
        if self.children is None:
            return Box(self.type, self.payload)
        return Box(self.type, children=[c.clone() for c in self.children])

    def to_bytes(self):
        if self.children is not None:
            body = b''.join(c.to_bytes() for c in self.children)
        else:
            body = self.payload
        size = 8 + len(body)
        if size > 0xFFFFFFFF:
            return struct.pack('>I4sQ', 1, self.type, size + 8) + body
        return struct.pack('>I4s', size, self.type) + body


def parse_boxes(data, offset=0, end=None):
    end = len(data) if end is None else end
    boxes = []
    while offset + 8 <= end:
        size, btype = struct.unpack_from('>I4s', data, offset)
        hdr = 8
        if size == 1:
            size = struct.unpack_from('>Q', data, offset + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - offset
        if size < hdr or offset + size > end:
            raise ValueError('caja %r corrupta en %d' % (btype, offset))
        if btype in CONTAINER_TYPES:
            try:
                box = Box(btype, children=parse_boxes(data, offset + hdr, offset + size))
            except ValueError:
                # p.ej. 'udta' de QuickTime con datos que no son cajas
                box = Box(btype, bytes(data[offset + hdr:offset + size]))
        else:
            box = Box(btype, bytes(data[offset + hdr:offset + size]))
        boxes.append(box)
        offset += size
    return boxes


def is_box_type(btype):
    return len(btype) == 4 and all(32 <= c < 127 for c in btype)


class TopBox:
    """Caja de primer nivel localizada en disco (sin leer su contenido)."""
    __slots__ = ('type', 'offset', 'hsize', 'size', 'truncated', 'open_ended')

    def __init__(self, btype, offset, hsize, size, truncated=False, open_ended=False):
        self.type = btype
        self.offset = offset
        self.hsize = hsize
        self.size = size
        self.truncated = truncated
        self.open_ended = open_ended

    @property
    def data_start(self):
        return self.offset + self.hsize

    @property
    def end(self):
        return self.offset + self.size

    def __repr__(self):
        return 'TopBox(%s @%d size=%d%s)' % (
            self.type.decode('latin1'), self.offset, self.size,
            ' TRUNCADA' if self.truncated else '')


def scan_top_level(f, file_size, start=0):
    """Recorre las cajas de primer nivel. Se detiene ante la primera caja inválida."""
    boxes = []
    pos = start
    while pos + 8 <= file_size:
        f.seek(pos)
        hdr = f.read(16)
        size, btype = struct.unpack('>I4s', hdr[:8])
        hsize = 8
        open_ended = False
        if not is_box_type(btype):
            break
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack('>Q', hdr[8:16])[0]
            hsize = 16
        elif size == 0:
            size = file_size - pos
            open_ended = True
        if size < hsize:
            break
        truncated = pos + size > file_size
        boxes.append(TopBox(btype, pos, hsize, size, truncated, open_ended))
        if truncated or open_ended:
            break
        pos += size
    return boxes


# ---------------------------------------------------------------- tablas

def full_box_version(payload):
    return payload[0]


def read_mdhd(payload):
    """Devuelve (timescale, duration) de mvhd o mdhd."""
    if payload[0] == 1:
        ts, dur = struct.unpack_from('>IQ', payload, 20)
    else:
        ts, dur = struct.unpack_from('>II', payload, 12)
    return ts, dur


def write_mdhd_duration(box, duration):
    """Actualiza la duración de mvhd/mdhd (pasa a versión 1 si hace falta)."""
    p = box.payload
    if p[0] == 1:
        box.payload = p[:24] + struct.pack('>Q', duration) + p[32:]
    elif duration <= 0xFFFFFFFF:
        box.payload = p[:16] + struct.pack('>I', duration) + p[20:]
    else:
        ctime, mtime, ts = struct.unpack_from('>III', p, 4)
        box.payload = (b'\x01' + p[1:4] + struct.pack('>QQIQ', ctime, mtime, ts, duration) + p[20:])


def write_tkhd_duration(box, duration):
    p = box.payload
    if p[0] == 1:
        box.payload = p[:28] + struct.pack('>Q', duration) + p[36:]
    elif duration <= 0xFFFFFFFF:
        box.payload = p[:20] + struct.pack('>I', duration) + p[24:]
    else:
        ctime, mtime, tid, res = struct.unpack_from('>IIII', p, 4)
        box.payload = (b'\x01' + p[1:4] + struct.pack('>QQIIQ', ctime, mtime, tid, res, duration) + p[24:])


def read_hdlr(payload):
    return payload[8:12]


def read_stts(payload):
    n = struct.unpack_from('>I', payload, 4)[0]
    return [struct.unpack_from('>II', payload, 8 + 8 * i) for i in range(n)]


def read_ctts(payload):
    version = payload[0]
    n = struct.unpack_from('>I', payload, 4)[0]
    fmt = '>Ii' if version == 1 else '>II'
    return version, [struct.unpack_from(fmt, payload, 8 + 8 * i) for i in range(n)]


def read_stsz(payload):
    ss, n = struct.unpack_from('>II', payload, 4)
    if ss:
        return ss, [ss] * n
    return 0, list(struct.unpack_from('>%dI' % n, payload, 12))


def read_stz2(payload):
    field = payload[7]
    n = struct.unpack_from('>I', payload, 8)[0]
    data = payload[12:]
    if field == 16:
        return 0, list(struct.unpack_from('>%dH' % n, data))
    if field == 8:
        return 0, list(data[:n])
    out = []
    for i in range(n):
        b = data[i // 2]
        out.append(b >> 4 if i % 2 == 0 else b & 15)
    return 0, out


def read_stsc(payload):
    n = struct.unpack_from('>I', payload, 4)[0]
    return [struct.unpack_from('>III', payload, 8 + 12 * i) for i in range(n)]


def read_stco(payload, is64):
    n = struct.unpack_from('>I', payload, 4)[0]
    return list(struct.unpack_from('>%d%s' % (n, 'Q' if is64 else 'I'), payload, 8))


def read_stss(payload):
    n = struct.unpack_from('>I', payload, 4)[0]
    return list(struct.unpack_from('>%dI' % n, payload, 8))


def read_elst(payload):
    version = payload[0]
    n = struct.unpack_from('>I', payload, 4)[0]
    fmt, step = ('>Qqhh', 20) if version == 1 else ('>Iihh', 12)
    return version, [struct.unpack_from(fmt, payload, 8 + step * i) for i in range(n)]


def make_elst(version, entries):
    fmt = '>Qqhh' if version == 1 else '>Iihh'
    return Box(b'elst', bytes([version, 0, 0, 0]) + struct.pack('>I', len(entries))
               + b''.join(struct.pack(fmt, *e) for e in entries))


def run_length(values):
    out = []
    for v in values:
        if out and out[-1][1] == v:
            out[-1][0] += 1
        else:
            out.append([1, v])
    return out


def make_stts(deltas):
    rl = run_length(deltas)
    return Box(b'stts', b'\0\0\0\0' + struct.pack('>I', len(rl))
               + b''.join(struct.pack('>II', c, d) for c, d in rl))


def make_ctts(version, offsets):
    rl = run_length(offsets)
    fmt = '>Ii' if version == 1 else '>II'
    return Box(b'ctts', bytes([version, 0, 0, 0]) + struct.pack('>I', len(rl))
               + b''.join(struct.pack(fmt, c, o) for c, o in rl))


def make_stsz(sizes):
    if sizes and all(s == sizes[0] for s in sizes):
        return Box(b'stsz', b'\0\0\0\0' + struct.pack('>II', sizes[0], len(sizes)))
    return Box(b'stsz', b'\0\0\0\0' + struct.pack('>II', 0, len(sizes))
               + struct.pack('>%dI' % len(sizes), *sizes))


def make_stsc(samples_per_chunk):
    entries = []
    for i, n in enumerate(samples_per_chunk):
        if not entries or entries[-1][1] != n:
            entries.append((i + 1, n, 1))
    return Box(b'stsc', b'\0\0\0\0' + struct.pack('>I', len(entries))
               + b''.join(struct.pack('>III', *e) for e in entries))


def make_stco(offsets):
    if offsets and max(offsets) > 0xFFFFFFFF:
        return Box(b'co64', b'\0\0\0\0' + struct.pack('>I', len(offsets))
                   + struct.pack('>%dQ' % len(offsets), *offsets))
    return Box(b'stco', b'\0\0\0\0' + struct.pack('>I', len(offsets))
               + struct.pack('>%dI' % len(offsets), *offsets))


def make_stss(indices_1based):
    return Box(b'stss', b'\0\0\0\0' + struct.pack('>I', len(indices_1based))
               + struct.pack('>%dI' % len(indices_1based), *indices_1based))


def stsd_fourcc(payload):
    """Código (avc1, hvc1, twos, rtmd...) de la primera entrada de stsd."""
    if len(payload) < 16:
        return b'????'
    return payload[12:16]


def stsd_length_size(payload):
    """Tamaño del prefijo de longitud NAL leído de avcC / hvcC."""
    i = payload.find(b'avcC')
    if i >= 0 and i + 8 < len(payload):
        return (payload[i + 8] & 3) + 1
    i = payload.find(b'hvcC')
    if i >= 0 and i + 25 < len(payload):
        return (payload[i + 4 + 21] & 3) + 1
    return 4
