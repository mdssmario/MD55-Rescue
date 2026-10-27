"""Orden de presentación (POC) de cada fotograma H.264/H.265, para reconstruir 'ctts'.

Con GOP largo (Long GOP) el orden de decodificación no es el de presentación por los
B-frames. Leemos el picture order count de la cabecera de cada slice.
"""
from . import nal


def unescape(data):
    """Quita los bytes de prevención de emulación (00 00 03)."""
    out = bytearray()
    zeros = 0
    for b in data:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


class Bits:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def ue(self):
        z = 0
        while self.u(1) == 0:
            z += 1
            if z > 31:
                raise ValueError('exp-golomb inválido')
        return (1 << z) - 1 + self.u(z)

    def se(self):
        v = self.ue()
        return (v + 1) // 2 if v & 1 else -(v // 2)


class PocError(Exception):
    pass


class AvcPoc:
    def __init__(self):
        self.sps = {}
        self.pps = {}
        self.prev_msb = 0
        self.prev_lsb = 0

    def param(self, t, rbsp):
        r = Bits(rbsp)
        if t == 7:
            profile = r.u(8)
            r.u(16)
            sid = r.ue()
            sep = 0
            if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
                cf = r.ue()
                if cf == 3:
                    sep = r.u(1)
                r.ue()
                r.ue()
                r.u(1)
                if r.u(1):
                    for i in range(12 if cf == 3 else 8):
                        if r.u(1):
                            last = nxt = 8
                            for _ in range(16 if i < 6 else 64):
                                if nxt:
                                    nxt = (last + r.se() + 256) % 256
                                last = nxt or last
            lfn = r.ue() + 4
            ptype = r.ue()
            lpoc = r.ue() + 4 if ptype == 0 else None
            if ptype == 1:
                raise PocError('pic_order_cnt_type 1 no soportado')
            r.ue()
            r.u(1)
            r.ue()
            r.ue()
            fmo = r.u(1)
            self.sps[sid] = dict(lfn=lfn, ptype=ptype, lpoc=lpoc, fmo=fmo, sep=sep)
        elif t == 8:
            pid = r.ue()
            self.pps[pid] = r.ue()

    def slice_poc(self, t, ref_idc, rbsp):
        r = Bits(rbsp)
        r.ue()
        r.ue()
        sps = self.sps[self.pps[r.ue()]]
        if sps['sep']:
            r.u(2)
        frame_num = r.u(sps['lfn'])
        if not sps['fmo'] and r.u(1):
            r.u(1)
        idr = t == 5
        if idr:
            r.ue()
            self.prev_msb = self.prev_lsb = 0
        if sps['ptype'] == 2:
            return frame_num, idr
        lsb = r.u(sps['lpoc'])
        maxl = 1 << sps['lpoc']
        if lsb < self.prev_lsb and self.prev_lsb - lsb >= maxl // 2:
            msb = self.prev_msb + maxl
        elif lsb > self.prev_lsb and lsb - self.prev_lsb > maxl // 2:
            msb = self.prev_msb - maxl
        else:
            msb = self.prev_msb
        if ref_idc:
            self.prev_msb, self.prev_lsb = msb, lsb
        return msb + lsb, idr


class HevcPoc:
    def __init__(self):
        self.sps = {}
        self.pps = {}
        self.prev_msb = 0
        self.prev_lsb = 0

    def param(self, t, rbsp):
        r = Bits(rbsp)
        if t == 33:
            r.u(4)
            msl = r.u(3)
            r.u(1)
            r.u(88)                         # general profile_tier_level
            r.u(8)                          # general_level_idc
            sub = [(r.u(1), r.u(1)) for _ in range(msl)]
            if msl:
                r.u(2 * (8 - msl))
            for prof, lev in sub:
                if prof:
                    r.u(88)
                if lev:
                    r.u(8)
            sid = r.ue()
            cf = r.ue()
            sep = r.u(1) if cf == 3 else 0
            r.ue()
            r.ue()
            if r.u(1):
                r.ue(), r.ue(), r.ue(), r.ue()
            r.ue()
            r.ue()
            self.sps[sid] = dict(lpoc=r.ue() + 4, sep=sep)
        elif t == 34:
            pid = r.ue()
            sid = r.ue()
            dep = r.u(1)
            out = r.u(1)
            extra = r.u(3)
            self.pps[pid] = dict(sid=sid, dep=dep, out=out, extra=extra)

    def slice_poc(self, t, tid, rbsp):
        r = Bits(rbsp)
        r.u(1)                              # first_slice_segment_in_pic_flag (=1)
        if 16 <= t <= 23:
            r.u(1)
        pps = self.pps[r.ue()]
        sps = self.sps[pps['sid']]
        r.u(pps['extra'])
        r.ue()
        if pps['out']:
            r.u(1)
        if sps['sep']:
            r.u(2)
        if t in (19, 20):
            self.prev_msb = self.prev_lsb = 0
            return 0, True
        lsb = r.u(sps['lpoc'])
        maxl = 1 << sps['lpoc']
        if 16 <= t <= 18:                   # BLA: reinicio
            msb = 0
        elif lsb < self.prev_lsb and self.prev_lsb - lsb >= maxl // 2:
            msb = self.prev_msb + maxl
        elif lsb > self.prev_lsb and lsb - self.prev_lsb > maxl // 2:
            msb = self.prev_msb - maxl
        else:
            msb = self.prev_msb
        sub_layer_non_ref = t <= 14 and t % 2 == 0
        if tid == 0 and not sub_layer_non_ref and t not in (6, 7, 8, 9):
            self.prev_msb, self.prev_lsb = msb, lsb
        return msb + lsb, 16 <= t <= 18


def parse_config(kind, stsd):
    """Extrae SPS/PPS(/VPS) de avcC o hvcC."""
    nals = []
    if kind == nal.AVC:
        i = stsd.find(b'avcC')
        if i < 0:
            return nals
        c = stsd[i + 4:]
        p = 6
        for _ in range(c[5] & 0x1F):            # SPS
            ln = int.from_bytes(c[p:p + 2], 'big')
            nals.append(c[p + 2:p + 2 + ln])
            p += 2 + ln
        count = c[p]
        p += 1
        for _ in range(count):                  # PPS
            ln = int.from_bytes(c[p:p + 2], 'big')
            nals.append(c[p + 2:p + 2 + ln])
            p += 2 + ln
    else:
        i = stsd.find(b'hvcC')
        if i < 0:
            return nals
        c = stsd[i + 4:]
        p = 23
        for _ in range(c[22]):
            n = int.from_bytes(c[p + 1:p + 3], 'big')
            p += 3
            for _ in range(n):
                ln = int.from_bytes(c[p:p + 2], 'big')
                nals.append(c[p + 2:p + 2 + ln])
                p += 2 + ln
    return nals


def presentation_offsets(kind, length_size, config_nals, samples, delta, min_shift=0):
    """samples: lista de bytes de cada fotograma (o vistas de memoria).

    Devuelve (lista ctts en unidades de tiempo de la pista, desplazamiento en fotogramas).
    """
    calc = AvcPoc() if kind == nal.AVC else HevcPoc()
    hs = nal.header_size(kind)
    for n in config_nals:
        calc.param(nal.nal_type(kind, n[0]), unescape(n[hs:]))
    keys = []
    epoch = 0
    for data in samples:
        poc = None
        for p, n, t in nal.iter_length_prefixed(data, 0, len(data), length_size, kind):
            body = data[p + length_size:p + length_size + n]
            if t in ((7, 8) if kind == nal.AVC else (33, 34)):
                calc.param(t, unescape(body[hs:]))
            elif nal.is_vcl(kind, t) and poc is None:
                rbsp = unescape(body[hs:hs + 64])
                if kind == nal.AVC:
                    poc, reset = calc.slice_poc(t, (body[0] >> 5) & 3, rbsp)
                else:
                    poc, reset = calc.slice_poc(t, (body[1] & 7) - 1, rbsp)
                if reset:
                    epoch += 1
        if poc is None:
            raise PocError('fotograma sin slice')
        keys.append((epoch, poc))
    order = sorted(range(len(keys)), key=lambda i: keys[i])
    display = [0] * len(keys)
    for d, i in enumerate(order):
        display[i] = d
    shift = max([min_shift] + [i - display[i] for i in range(len(keys))])
    return [(display[i] - i + shift) * delta for i in range(len(keys))], shift
