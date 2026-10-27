"""Utilidades NAL para H.264 (XAVC S / XAVC S-I) y H.265 (XAVC HS)."""

AVC = 'avc'
HEVC = 'hevc'

CODEC_KIND = {
    b'avc1': AVC, b'avc3': AVC, b'AVC1': AVC,
    b'hvc1': HEVC, b'hev1': HEVC,
}

_AVC_OK = set(range(1, 14)) | {19, 20}
_HEVC_OK = set(range(0, 10)) | set(range(16, 22)) | set(range(32, 41))
# NAL no-VCL que abren una nueva unidad de acceso si llegan después de un VCL
_AVC_AU_START = {6, 7, 8, 9, 14, 15}
_HEVC_AU_START = {32, 33, 34, 35, 39, 41, 42, 43, 44} | set(range(48, 56))

AVC_NAMES = {1: 'slice', 5: 'IDR', 6: 'SEI', 7: 'SPS', 8: 'PPS', 9: 'AUD'}
HEVC_NAMES = {19: 'IDR_W_RADL', 20: 'IDR_N_LP', 21: 'CRA', 32: 'VPS', 33: 'SPS',
              34: 'PPS', 35: 'AUD', 39: 'SEI'}


def header_size(kind):
    return 1 if kind == AVC else 2


def nal_type(kind, b0):
    return b0 & 0x1F if kind == AVC else (b0 >> 1) & 0x3F


def header_ok(kind, b0, b1):
    if b0 & 0x80:
        return False
    if kind == AVC:
        return (b0 & 0x1F) in _AVC_OK
    t = (b0 >> 1) & 0x3F
    layer = ((b0 & 1) << 5) | (b1 >> 3)
    return t in _HEVC_OK and layer == 0 and (b1 & 7) != 0


def is_vcl(kind, t):
    return 1 <= t <= 5 if kind == AVC else t < 32


def is_au_start_nonvcl(kind, t):
    return t in (_AVC_AU_START if kind == AVC else _HEVC_AU_START)


def first_in_picture(kind, data, nal_pos):
    """True si el slice que empieza en nal_pos es el primero de su imagen."""
    if kind == AVC:
        return bool(data[nal_pos + 1] & 0x80)   # first_mb_in_slice == 0
    return bool(data[nal_pos + 2] & 0x80)       # first_slice_segment_in_pic_flag


def is_keyframe_type(kind, t):
    return t == 5 if kind == AVC else 16 <= t <= 23


class _Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def bit(self):
        b = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return b

    def ue(self):
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError
        v = 0
        for _ in range(zeros):
            v = (v << 1) | self.bit()
        return (1 << zeros) - 1 + v


def avc_slice_is_intra(data, nal_pos):
    """slice_type I/SI en un slice H.264 no IDR."""
    try:
        r = _Bits(bytes(data[nal_pos + 1:nal_pos + 9]))
        r.ue()
        return r.ue() % 5 in (2, 4)
    except (ValueError, IndexError):
        return False


def iter_length_prefixed(data, start, end, length_size, kind):
    """Itera (pos_nal, tamaño, tipo) mientras los NAL sean válidos."""
    hs = header_size(kind)
    p = start
    while p + length_size + hs <= end:
        n = int.from_bytes(data[p:p + length_size], 'big')
        if n < hs or p + length_size + n > end:
            return
        b0 = data[p + length_size]
        b1 = data[p + length_size + 1] if hs == 2 else 0
        if not header_ok(kind, b0, b1):
            return
        yield p, n, nal_type(kind, b0)
        p += length_size + n


def detect_length_prefixed(data, start, end, length_size=4, min_nals=3):
    """Intenta reconocer AVC o HEVC con prefijo de longitud al inicio del mdat."""
    best = None
    for kind in (AVC, HEVC):
        count = 0
        types = []
        for _p, _n, t in iter_length_prefixed(data, start, end, length_size, kind):
            count += 1
            types.append(t)
            if count >= 12:
                break
        if count >= min_nals and any(is_vcl(kind, t) for t in types):
            if best is None or count > best[1]:
                best = (kind, count, types)
    return best
