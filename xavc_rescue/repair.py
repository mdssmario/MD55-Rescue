"""Reconstrucción del índice (moov) de un clip XAVC S / XAVC HS / XAVC S-I interrumpido.

Cuando la cámara se apaga, se queda sin batería o se saca la tarjeta mientras graba,
el vídeo (caja 'mdat') queda en la tarjeta pero falta el índice ('moov') que dice dónde
empieza cada fotograma y cada bloque de audio. Sin él ningún reproductor ni editor lo abre.

Este módulo aprende la estructura de un clip SANO grabado con la misma cámara y la misma
configuración (códec, resolución, fps, bitrate, audio) y la usa para recorrer el mdat
dañado, localizar cada fotograma H.264/H.265, cada bloque de audio LPCM y cada bloque de
metadatos en tiempo real (rtmd), y escribir un moov nuevo.

El flujo de vídeo no se recodifica: los bytes se copian tal cual, así que S-Log3,
S-Cinetone, HLG, 10 bits 4:2:2, etc. se conservan exactamente.
"""
import math
import mmap
import os
import struct
from collections import Counter

from . import mp4, nal, poc

MAX_NAL = 64 << 20
RESYNC_WINDOW = 64 << 20
SAMPLE_CHUNKS = 400
SKIP_STBL = {b'stts', b'ctts', b'stsc', b'stsz', b'stz2', b'stco', b'co64', b'stss',
             b'sdtp', b'sbgp', b'sgpd', b'stps', b'subs', b'saiz', b'saio', b'cslg',
             b'padb', b'stsh'}


class RepairError(Exception):
    pass


def _evenly(seq, n):
    if len(seq) <= n:
        return list(seq)
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)]


class RefTrack:
    """Lo que aprendemos de cada pista del clip de referencia."""

    def __init__(self, trak, index, f):
        self.trak = trak
        self.index = index
        mdia = trak.child(b'mdia')
        self.handler = mp4.read_hdlr(mdia.child(b'hdlr').payload)
        self.timescale, _ = mp4.read_mdhd(mdia.child(b'mdhd').payload)
        stbl = mdia.path(b'minf', b'stbl')
        stsd = stbl.child(b'stsd').payload
        self.stsd = stsd
        self.fourcc = mp4.stsd_fourcc(stsd)
        self.kind = nal.CODEC_KIND.get(self.fourcc) if self.handler == b'vide' else None
        self.length_size = mp4.stsd_length_size(stsd) if self.kind else 0

        if stbl.child(b'stsz') is not None:
            self.fixed_size, sizes = mp4.read_stsz(stbl.child(b'stsz').payload)
        else:
            self.fixed_size, sizes = mp4.read_stz2(stbl.child(b'stz2').payload)
        stts = mp4.read_stts(stbl.child(b'stts').payload)
        deltas = Counter()
        for count, delta in stts:
            deltas[delta] += count
        self.delta = deltas.most_common(1)[0][0] if deltas else 1
        self.ref_shift_frames = 0
        self.out_shift_frames = None
        elst = trak.path(b'edts', b'elst')
        if elst is not None:
            media_times = [e[1] for e in mp4.read_elst(elst.payload)[1] if e[1] != -1]
            if media_times:
                self.ref_shift_frames = media_times[0] // self.delta

        ctts = stbl.child(b'ctts')
        self.ctts_version = 0
        ctts_list = None
        if ctts is not None:
            self.ctts_version, entries = mp4.read_ctts(ctts.payload)
            ctts_list = [o for c, o in entries for _ in range(c)]
        stss = stbl.child(b'stss')
        sync0 = [i - 1 for i in mp4.read_stss(stss.payload)] if stss is not None else None
        self.sync_ratio = len(sync0) / max(1, len(sizes)) if sync0 is not None else 1.0

        co = stbl.child(b'co64') or stbl.child(b'stco')
        offsets = mp4.read_stco(co.payload, co.type == b'co64')
        stsc = mp4.read_stsc(stbl.child(b'stsc').payload)
        spc = []
        for i, (first, n, _sdi) in enumerate(stsc):
            last = stsc[i + 1][0] if i + 1 < len(stsc) else len(offsets) + 1
            spc.extend([n] * (last - first))
        spc = spc[:len(offsets)]

        # bloques (chunks) y posición de cada muestra
        self.chunks = []
        self.sample_offsets = []
        s = 0
        for off, n in zip(offsets, spc):
            nbytes = 0
            for j in range(n):
                if s + j < len(sizes):
                    self.sample_offsets.append(off + nbytes)
                    nbytes += sizes[s + j]
            self.chunks.append((off, n, nbytes))
            s += n
        self.sample_count = len(sizes)
        self.sizes = sizes

        if self.kind:
            self.role = 'video'
        elif self.fixed_size:
            self.role = 'fixed'
        else:
            self.role = 'variable'
        self.static = len(self.chunks) < 3

        body = self.chunks[:-1] or self.chunks
        cnt = Counter(c[2] for c in body)
        self.cands = [size for size, _ in cnt.most_common()]
        self.min_chunk = min(cnt) if cnt else 0
        self.max_chunk = max(cnt) if cnt else 0
        spc_cnt = Counter(c[1] for c in body)
        top, freq = spc_cnt.most_common(1)[0] if spc_cnt else (0, 0)
        self.expected_spc = top if body and freq >= 0.9 * len(body) else None

        self.signature = b''
        self.first_types = set()
        self.magics = []
        self.sync_mode = 'all'
        self.ctts_pattern = None
        self._learn(f, sync0, ctts_list)

    def _learn(self, f, sync0, ctts_list):
        picks = _evenly(self.chunks[:-1] or self.chunks, SAMPLE_CHUNKS)
        if self.role == 'video':
            hs = nal.header_size(self.kind)
            L = self.length_size
            magic = Counter()
            for off, _n, _b in picks:
                f.seek(off)
                h = f.read(L + hs)
                if len(h) < L + hs:
                    continue
                self.first_types.add(nal.nal_type(self.kind, h[L]))
                magic[h] += 1
            total = sum(magic.values())
            self.magics = [m for m, c in magic.most_common(4) if c >= 0.05 * total]
            if sync0 is None:
                self.sync_mode = 'all'
            else:
                self.sync_mode = 'idr'
                for si in sync0[:20]:
                    if si >= len(self.sample_offsets):
                        break
                    f.seek(self.sample_offsets[si])
                    data = f.read(self.sizes[si])
                    if not any(nal.is_keyframe_type(self.kind, t) for _p, _n, t in
                               nal.iter_length_prefixed(data, 0, len(data), L, self.kind)):
                        self.sync_mode = 'intra'
                        break
            if ctts_list:
                # patrón de composición por GOP: el primero suele ser distinto
                bounds = (sync0 or [0]) + [len(ctts_list)]
                gops = [ctts_list[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]
                self.first_gop = gops[0]
                self.gop_patterns = {}
                for g in gops[1:-1] or gops:
                    self.gop_patterns.setdefault(len(g), g)
                typical = Counter(len(g) for g in gops[1:-1] or gops).most_common(1)[0][0]
                self.ctts_pattern = self.gop_patterns[typical]
        elif self.handler != b'soun' and len(picks) >= 3:
            heads = []
            for off, _n, _b in picks:
                f.seek(off)
                heads.append(f.read(16))
            # solo los primeros bytes: el resto (p.ej. en rtmd) cambia de un clip a otro
            prefix = os.path.commonprefix(heads)[:4]
            if len(prefix) >= 3 and prefix.strip(b'\0'):
                self.signature = prefix

    @property
    def name(self):
        h = self.handler.decode('latin1')
        return {'vide': 'vídeo', 'soun': 'audio', 'meta': 'metadatos'}.get(h, h) \
            + ' (%s)' % self.fourcc.decode('latin1', 'replace')


def _find_period(seq):
    n = len(seq)
    if n == 0:
        return []
    body = seq[:max(1, n - max(2, n // 20))]
    for p in range(1, min(64, len(body)) + 1):
        bad = sum(1 for i in range(p, len(body)) if body[i] != body[i - p])
        if bad <= max(1, len(body) // 50):
            return body[:p]
    return body


class Reference:
    def __init__(self, path, log=print):
        self.path = path
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            tops = mp4.scan_top_level(f, size)
            moov_tb = next((t for t in tops if t.type == b'moov' and not t.truncated), None)
            if moov_tb is None:
                raise RepairError('El clip de referencia no tiene índice (moov). '
                                  'Debe ser un clip SANO que se reproduzca bien.')
            f.seek(moov_tb.data_start)
            self.moov = mp4.Box(b'moov', children=mp4.parse_boxes(f.read(moov_tb.size - moov_tb.hsize)))
            ftyp_tb = next((t for t in tops if t.type == b'ftyp'), None)
            self.ftyp_bytes = b''
            if ftyp_tb:
                f.seek(ftyp_tb.offset)
                self.ftyp_bytes = f.read(ftyp_tb.size)
            self.movie_timescale, _ = mp4.read_mdhd(self.moov.child(b'mvhd').payload)
            self.tracks = [RefTrack(trak, i, f) for i, trak in
                           enumerate(c for c in self.moov.children if c.type == b'trak')]

        self.video = next((t for t in self.tracks if t.role == 'video'), None)
        if self.video is None:
            raise RepairError('La referencia no tiene pista de vídeo H.264/H.265 reconocible.')
        for t in self.tracks:
            # bytes de esta pista por fotograma de vídeo: estima el tamaño del último bloque
            t.bytes_per_frame = (sum(c[2] for c in t.chunks) / self.video.sample_count
                                 if t.role == 'fixed' and self.video.sample_count else None)
        self.active = [t for t in self.tracks if not t.static]
        all_chunks = sorted((off, t.index) for t in self.active for off, _n, _b in t.chunks)
        seq = [i for _off, i in all_chunks]
        by_index = {t.index: t for t in self.tracks}
        period_idx = _find_period(seq)
        vi = period_idx.index(self.video.index) if self.video.index in period_idx else 0
        period_idx = period_idx[vi:] + period_idx[:vi]
        self.period = [by_index[i] for i in period_idx]
        gaps = 0
        ends = {}
        for t in self.active:
            for off, _n, b in t.chunks:
                ends[off] = off + b
        prev_end = None
        for off, _i in all_chunks:
            if prev_end is not None and off != prev_end:
                gaps += 1
            prev_end = ends[off]
        self.gaps = gaps

        v = self.video
        log('Referencia: %s' % os.path.basename(path))
        for t in self.tracks:
            extra = ''
            if t.role == 'video':
                extra = ', %s fotogramas/bloque, inicio NAL %s, keyframes: %s' % (
                    t.expected_spc or 'variable',
                    sorted(t.first_types), t.sync_mode)
            elif t.signature:
                extra = ', firma %s' % t.signature[:8].hex()
            log('  pista %d: %s, %d muestras, %d bloques%s%s' % (
                t.index + 1, t.name, t.sample_count, len(t.chunks), extra,
                ' (estática, se omite)' if t.static else ''))
        log('  patrón de intercalado: %s' % ' → '.join(t.name for t in self.period))
        if gaps:
            log('  aviso: la referencia tiene %d huecos entre bloques; se resincronizará.' % gaps)
        if v.kind is None:
            raise RepairError('Códec de vídeo no soportado: %r' % v.fourcc)


class Scanner:
    """Recorre el mdat del clip dañado siguiendo el patrón aprendido."""

    def __init__(self, mm, start, end, ref, log=print, progress=None):
        self.mm = mm
        self.start = start
        self.end = end
        self.ref = ref
        self.period = ref.period
        self.P = len(self.period)
        self.log = log
        self.progress = progress
        self.out = {t.index: [] for t in ref.tracks}     # [(offset, [tamaños])]
        self.keys = []                                    # keyframe por fotograma
        self.skipped = 0
        self.data_end = start
        self.eof = False
        self.first_sps = None
        self.last_frames = None
        self.data_limit = self._data_limit()

    def _data_limit(self):
        """Fin de los datos útiles: Sony rellena el mdat con ceros hasta bloques de 64 MB."""
        pos = self.end
        step = 1 << 20
        while pos > self.start:
            a = max(self.start, pos - step)
            block = self.mm[a:pos]
            stripped = block.rstrip(b'\0')
            if stripped:
                return a + len(stripped)
            pos = a
        return self.start

    def candidates(self, t, frames):
        """Tamaños posibles de un bloque de audio/metadatos, el más probable primero."""
        out = []
        if frames and t.bytes_per_frame and t.fixed_size:
            est = round(t.bytes_per_frame * frames / t.fixed_size)
            out = [est * t.fixed_size, (est + 1) * t.fixed_size, (est - 1) * t.fixed_size]
        return out + [c for c in t.cands[:6] if c not in out]

    # ---------------------------------------------------- comprobaciones
    def video_start_ok(self, pos, t):
        L = t.length_size
        hs = nal.header_size(t.kind)
        mm = self.mm
        if pos + L + hs > self.end:
            return False
        n = int.from_bytes(mm[pos:pos + L], 'big')
        if n < hs or n > MAX_NAL or pos + L + n > self.end:
            return False
        b0 = mm[pos + L]
        b1 = mm[pos + L + 1] if hs == 2 else 0
        if not nal.header_ok(t.kind, b0, b1):
            return False
        if t.first_types and nal.nal_type(t.kind, b0) not in t.first_types:
            return False
        q = pos + L + n          # el siguiente NAL también tiene que ser válido
        if q + L + hs <= self.end:
            n2 = int.from_bytes(mm[q:q + L], 'big')
            if n2 < hs or n2 > MAX_NAL:
                return False
            c0 = mm[q + L]
            c1 = mm[q + L + 1] if hs == 2 else 0
            if not nal.header_ok(t.kind, c0, c1):
                return False
        return True

    def start_ok(self, pos, t):
        if t.role == 'video':
            return self.video_start_ok(pos, t)
        if t.signature:
            return self.mm[pos:pos + len(t.signature)] == t.signature
        return None

    def chain_ok(self, pos, k, depth, strict=False, frames=None):
        if pos > self.end:
            return False
        if pos >= self.data_limit:
            return True             # final de los datos (o relleno de ceros)
        t = self.period[k % self.P]
        r = self.start_ok(pos, t)
        if r is not None:
            return r
        if depth <= 0:
            return not strict
        for s in self.candidates(t, frames):
            if s > 0 and self.chain_ok(pos + s, k + 1, depth - 1, strict, frames):
                return True
        return False

    # ---------------------------------------------------- lectura de bloques
    def read_video(self, pos, k):
        """Devuelve (fin, [(tamaño, keyframe)], llegó_al_final) o None."""
        t = self.period[k % self.P]
        kind, L = t.kind, t.length_size
        hs = nal.header_size(kind)
        mm, end = self.mm, self.end
        nals = []       # (pos, fin, au, vcl)
        aus = []        # [inicio, keyframe]
        p = pos
        seen_vcl = False
        truncated = False
        while True:
            if p + L + hs > end:
                truncated = p < end
                break
            n = int.from_bytes(mm[p:p + L], 'big')
            if n < hs or n > MAX_NAL:
                break
            b0 = mm[p + L]
            b1 = mm[p + L + 1] if hs == 2 else 0
            if not nal.header_ok(kind, b0, b1):
                break
            if p + L + n > end:
                truncated = True
                break
            ty = nal.nal_type(kind, b0)
            vcl = nal.is_vcl(kind, ty)
            if not aus:
                aus.append([p, False])
            elif seen_vcl and (nal.is_au_start_nonvcl(kind, ty) or
                               (vcl and nal.first_in_picture(kind, mm, p + L))):
                aus.append([p, False])
                seen_vcl = False
            if nal.is_keyframe_type(kind, ty):
                aus[-1][1] = True
            elif (vcl and t.sync_mode == 'intra' and kind == nal.AVC
                  and nal.first_in_picture(kind, mm, p + L)
                  and nal.avc_slice_is_intra(mm, p + L)):
                aus[-1][1] = True
            if self.first_sps is None and ty == (7 if kind == nal.AVC else 33):
                self.first_sps = bytes(mm[p + L:p + L + n])
            if vcl:
                seen_vcl = True
            nals.append((p, p + L + n, len(aus) - 1, vcl))
            p += L + n
        if not nals:
            return None

        def samples_until(e):
            out = []
            for i, (start, key) in enumerate(aus):
                if start >= e:
                    break
                a_end = aus[i + 1][0] if i + 1 < len(aus) and aus[i + 1][0] <= e else e
                has_vcl = any(v for (_np, ne, ai, v) in nals if ai == i and ne <= a_end)
                if not has_vcl:
                    break
                out.append((a_end - start, key))
            return out

        natural = nals[-1][1]
        cands = []
        if t.expected_spc and len(aus) > t.expected_spc:
            cands.append(aus[t.expected_spc][0])
        cands.append(natural)
        cands.extend(ne for (_p, ne, _a, _v) in reversed(nals[-200:-1]))
        seen = set()
        for e in cands:
            if e in seen or e <= pos:
                continue
            seen.add(e)
            frames = sum(1 for a in aus if a[0] < e)
            if self.chain_ok(e, k + 1, 2, frames=frames):
                smp = samples_until(e)
                if smp:
                    return e, smp, False
        if truncated and len(aus) > 1:
            # el archivo se corta en mitad de este bloque: nos quedamos con
            # los fotogramas completos (el último está a medias)
            e = aus[-1][0]
            smp = samples_until(e)
            if smp:
                return e, smp, True
        return None

    def read_sized(self, pos, k):
        t = self.period[k % self.P]
        for s in self.candidates(t, self.last_frames) + t.cands[6:]:
            e = pos + s
            if s > 0 and e <= self.end and self.chain_ok(e, k + 1, 2, frames=self.last_frames):
                return s
        step = t.fixed_size or 1
        lo = max(step, (t.min_chunk // 2) // step * step)
        hi = max(t.max_chunk * 2, lo)
        for s in range(lo, hi + 1, step):
            e = pos + s
            if e > self.end:
                break
            if self.chain_ok(e, k + 1, 3, strict=True):
                return s
        return None

    def _block_ok(self, q, k):
        t = self.period[k]
        if not self.start_ok(q, t):
            return False
        if t.role == 'video':
            return self.read_video(q, k) is not None
        return self.read_sized(q, k) is not None

    def resync(self, pos):
        """Busca hacia delante el siguiente bloque válido (vídeo o pista con firma)."""
        v = self.ref.video
        kv = self.period.index(v)
        limit = min(self.end, pos + RESYNC_WINDOW)
        mm = self.mm
        patterns = [(m, kv) for m in v.magics]
        patterns += [(t.signature, k) for k, t in enumerate(self.period) if t.signature]
        if patterns:
            q = pos
            while q < limit:
                hits = [(h, k) for h, k in ((mm.find(m, q, limit), k) for m, k in patterns)
                        if h >= 0]
                if not hits:
                    return None, None
                q = min(h for h, _k in hits)
                for h, k in hits:
                    if h == q and self._block_ok(q, k):
                        return q, k
                q += 1
            return None, None
        for q in range(pos, min(limit, pos + (8 << 20))):
            if self.video_start_ok(q, v) and self.read_video(q, kv):
                return q, kv
        return None, None

    def find_phase(self, pos):
        order = sorted(range(self.P), key=lambda k: self.period[k].role != 'video')
        for k in order:
            if self.chain_ok(pos, k, 3, strict=True):
                return k
        return None

    # ---------------------------------------------------- bucle principal
    def run(self):
        pos = self.start
        k = self.find_phase(pos)
        if k is None:
            q, k = self.resync(pos)
            if q is None:
                raise RepairError('No se encontraron fotogramas reconocibles en el clip dañado. '
                                  '¿La referencia es de la misma cámara y configuración?')
            self.log('  se saltaron %d bytes iniciales hasta el primer bloque de vídeo' % (q - pos))
            self.skipped += q - pos
            pos = q
        total = max(1, self.end - self.start)
        tick = 0
        while pos < self.data_limit:
            t = self.period[k % self.P]
            ok = False
            if t.role == 'video':
                r = self.read_video(pos, k)
                if r:
                    e, smp, hit_eof = r
                    self.out[t.index].append((pos, [s for s, _ in smp]))
                    self.keys.extend(key for _, key in smp)
                    self.last_frames = len(smp)
                    pos = e
                    ok = True
                    if hit_eof:
                        self.eof = True
                        self.data_end = pos
                        break
            else:
                s = self.read_sized(pos, k)
                if s:
                    if t.role == 'fixed' and s % t.fixed_size == 0:
                        self.out[t.index].append((pos, [t.fixed_size] * (s // t.fixed_size)))
                    else:
                        self.out[t.index].append((pos, None))   # bloque descartado
                    pos += s
                    ok = True
            if ok:
                self.data_end = pos
                k += 1
            else:
                q, k2 = self.resync(pos + 1)
                if q is None:
                    break
                self.log('  resincronizado: %d bytes ilegibles en la posición %d' % (q - pos, pos))
                self.skipped += q - pos
                pos, k = q, k2
            tick += 1
            if self.progress and tick % 200 == 0:
                self.progress((pos - self.start) / total)
        if self.progress:
            self.progress(1.0)


def _apply_durations(trak, media_dur, track_ts, movie_ts, media_time=None):
    mdia = trak.child(b'mdia')
    mp4.write_mdhd_duration(mdia.child(b'mdhd'), media_dur)
    movie_dur = media_dur * movie_ts // track_ts
    edts = trak.child(b'edts')
    if media_time and edts is None:
        edts = mp4.Box(b'edts', children=[mp4.make_elst(0, [(0, media_time, 1, 0)])])
        trak.children.insert(trak.children.index(trak.child(b'mdia')), edts)
    if media_time is not None and edts is not None and edts.child(b'elst') is not None:
        version, entries = mp4.read_elst(edts.child(b'elst').payload)
        edts.children = [mp4.make_elst(version, [
            (s, media_time if mt != -1 else mt, ri, rf) for s, mt, ri, rf in entries])]
    if edts is not None and edts.child(b'elst') is not None:
        version, entries = mp4.read_elst(edts.child(b'elst').payload)
        normal = [e for e in entries if e[1] != -1]
        if len(normal) == 1:
            new = []
            total = 0
            for seg, mt, ri, rf in entries:
                if mt != -1:
                    seg = max(0, media_dur - mt) * movie_ts // track_ts
                new.append((seg, mt, ri, rf))
                total += seg
            if any(e[0] > 0xFFFFFFFF for e in new):
                version = 1
            edts.children = [mp4.make_elst(version, new)]
            movie_dur = total
        else:
            trak.children.remove(edts)
    mp4.write_tkhd_duration(trak.child(b'tkhd'), movie_dur)
    return movie_dur


def _ctts_from_poc(t, scanner, chunks, log):
    """Calcula ctts leyendo el POC de cada fotograma; None si no se puede."""
    def samples():
        mm = scanner.mm
        for off, sizes in chunks:
            for s in sizes:
                yield mm[off:off + s]
                off += s
    try:
        ctts, shift = poc.presentation_offsets(
            t.kind, t.length_size, poc.parse_config(t.kind, t.stsd), samples(), t.delta,
            min_shift=t.ref_shift_frames)
    except (poc.PocError, KeyError, IndexError, ValueError) as e:
        log('  aviso: no se pudo leer el orden de fotogramas (%s); se usa el patrón de la '
            'referencia' % e)
        return None
    t.out_shift_frames = shift
    return ctts


def _trim_chunks(chunks, limit):
    """Los primeros `limit` muestras de una lista de bloques (offset, tamaños)."""
    out = []
    for off, smp in chunks:
        if limit <= 0:
            break
        out.append((off, smp[:limit]))
        limit -= len(out[-1][1])
    return out


def build_moov(ref, scanner, shift, log=print):
    moov = ref.moov.clone()
    traks = [c for c in moov.children if c.type == b'trak']
    movie_ts = ref.movie_timescale
    movie_dur = 0
    stats = {}
    v = ref.video
    video_secs = sum(len(smp) for _o, smp in scanner.out.get(v.index) or []) * v.delta / v.timescale
    for t, trak in zip(ref.tracks, traks):
        chunks = scanner.out.get(t.index) or []
        usable = [c for c in chunks if c[1]]
        if t.static or not usable or len(usable) != len(chunks):
            moov.children.remove(trak)
            if not t.static:
                log('  pista %d (%s) descartada: no se pudo reconstruir' % (t.index + 1, t.name))
            continue
        if t.role != 'video':
            # al cortarse la grabación, audio y metadatos pueden ir algo por delante del vídeo
            limit = math.ceil(video_secs * t.timescale / t.delta - 1e-9)
            usable = _trim_chunks(usable, limit)
        sizes = [s for _off, smp in usable for s in smp]
        spc = [len(smp) for _off, smp in usable]
        offsets = [off + shift for off, _smp in usable]
        n = len(sizes)
        stbl = trak.path(b'mdia', b'minf', b'stbl')
        new_children = [c for c in stbl.children if c.type not in SKIP_STBL]
        new_children.append(mp4.make_stts([t.delta] * n))
        if t.role == 'video':
            keys = scanner.keys[:n]
            ctts = None
            if t.ctts_pattern:
                ctts = _ctts_from_poc(t, scanner, usable, log)
                if ctts is not None:
                    new_children.append(mp4.make_ctts(t.ctts_version, ctts))
            if t.ctts_pattern and ctts is None:
                starts = [i for i in range(n) if keys[i]]
                if not starts or starts[0] != 0:
                    starts.insert(0, 0)
                ctts = []
                for gi, (a, b) in enumerate(zip(starts, starts[1:] + [n])):
                    length = b - a
                    if gi == 0 and len(t.first_gop) >= length:
                        pat = t.first_gop
                    else:
                        pat = t.gop_patterns.get(length, t.ctts_pattern)
                    ctts.extend(pat[j % len(pat)] for j in range(length))
                new_children.append(mp4.make_ctts(t.ctts_version, ctts))
            if t.sync_mode != 'all':
                idx = [i + 1 for i, kf in enumerate(keys) if kf] or [1]
                new_children.append(mp4.make_stss(idx))
        new_children.append(mp4.make_stsc(spc))
        new_children.append(mp4.make_stsz(sizes))
        new_children.append(mp4.make_stco(offsets))
        stbl.children = new_children
        media_dur = n * t.delta
        media_time = None
        if t.role == 'video' and t.out_shift_frames is not None:
            media_time = t.out_shift_frames * t.delta
        movie_dur = max(movie_dur, _apply_durations(trak, media_dur, t.timescale, movie_ts,
                                                    media_time))
        stats[t.index] = (t, n, media_dur / t.timescale)
    mp4.write_mdhd_duration(moov.child(b'mvhd'), movie_dur)
    return moov, stats


def _quick_score(broken_path, ref, probe=64 << 20):
    """Cuántos fotogramas se reconocen al inicio del clip dañado con esta referencia."""
    size = os.path.getsize(broken_path)
    with open(broken_path, 'rb') as f:
        tops = mp4.scan_top_level(f, size)
        mdat = next((t for t in tops if t.type == b'mdat'), None)
        start = mdat.data_start if mdat else 0
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            sc = Scanner(mm, start, min(size, start + probe), ref, log=lambda m: None)
            try:
                sc.run()
            except RepairError:
                return 0
            sizes = [s for _o, smp in sc.out[ref.video.index] for s in smp]
            frames = len(sizes)
            if not frames:
                return 0
            v = ref.video
            # misma familia de GOP (Intra / Long GOP) y bitrate/resolución parecidos
            kf = sum(1 for k in sc.keys[:frames] if k) / frames
            same_gop = max(0.0, 1.0 - abs(kf - v.sync_ratio))
            mb, mr = sum(sizes) / frames, sum(v.sizes) / max(1, len(v.sizes))
            same_rate = min(mb, mr) / max(mb, mr)
            score = frames * same_gop * same_rate
            if sc.first_sps is not None and v.stsd.find(sc.first_sps) >= 0:
                score += 1_000_000          # misma configuración exacta (SPS idéntico)
            return score
        finally:
            mm.close()


def pick_reference(broken_path, reference, log=print):
    """Si 'reference' es una carpeta, elige el clip sano que mejor encaja con el dañado."""
    if not os.path.isdir(reference):
        return reference
    best, best_score = None, 0
    for name in sorted(os.listdir(reference)):
        path = os.path.join(reference, name)
        if not name.upper().endswith(('.MP4', '.MOV')) or not os.path.isfile(path):
            continue
        if os.path.abspath(path) == os.path.abspath(broken_path):
            continue
        try:
            score = _quick_score(broken_path, Reference(path, log=lambda m: None))
        except (RepairError, ValueError, OSError, AttributeError, struct.error):
            continue
        if score > best_score:
            best, best_score = path, score
    if best is None:
        raise RepairError('Ningún clip de la carpeta de referencias encaja con este clip. Graba uno '
                          'con la misma cámara y configuración.')
    log('Referencia elegida automáticamente: %s%s' % (
        os.path.basename(best), ' (configuración idéntica)' if best_score >= 1_000_000 else ''))
    return best


def repair(broken_path, reference_path, out_path, log=print, progress=None, force=False):
    if os.path.abspath(broken_path) == os.path.abspath(out_path):
        raise RepairError('El archivo de salida no puede ser el mismo que el dañado.')
    reference_path = pick_reference(broken_path, reference_path, log)
    ref = Reference(reference_path, log)
    size = os.path.getsize(broken_path)
    if size == 0:
        raise RepairError('El archivo dañado está vacío (0 bytes).')
    with open(broken_path, 'rb') as f:
        tops = mp4.scan_top_level(f, size)
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            moov = next((t for t in tops if t.type == b'moov' and not t.truncated), None)
            if moov is not None and not force:
                raise RepairError('El archivo ya tiene índice (moov). Ábrelo normalmente; '
                                  'si aun así falla usa la opción "forzar".')
            mdat = next((t for t in tops if t.type == b'mdat'), None)
            if mdat is not None:
                start = mdat.data_start
                end = size if (mdat.truncated or mdat.open_ended or mdat.size <= mdat.hsize) \
                    else mdat.end
                if mdat.end < size and not mdat.truncated and end - start < (size - start) // 2:
                    # tamaño del mdat sin actualizar (típico al cortarse la grabación)
                    end = size
                head_parts = [(t.offset, t.size) for t in tops
                              if t.offset < mdat.offset and t.type not in (b'moov', b'mdat')]
                log('Clip dañado: mdat en %d, %d MB de datos' % (mdat.offset, (end - start) >> 20))
            else:
                log('Clip dañado: no hay cabecera mdat; se busca el vídeo directamente.')
                start, end = 0, size
                head_parts = None

            scanner = Scanner(mm, start, end, ref, log, progress)
            scanner.run()
            vchunks = scanner.out[ref.video.index]
            frames = sum(len(s) for _o, s in vchunks)
            if frames == 0:
                raise RepairError('No se pudo recuperar ningún fotograma.')
            if scanner.first_sps is not None and ref.video.stsd.find(scanner.first_sps) < 0:
                log('  AVISO: el SPS del clip dañado no coincide con el de la referencia. '
                    'La referencia debe tener exactamente el mismo códec/resolución/fps/bitrate. '
                    'Si el resultado no se ve bien, prueba con otra referencia.')
            if mdat is not None:
                data_start = start
            else:
                data_start = min(lst[0][0] for lst in scanner.out.values() if lst)
            data_end = scanner.data_end

            tmp = out_path + '.parcial'
            with open(tmp, 'wb') as o:
                if head_parts is None:
                    o.write(ref.ftyp_bytes)
                else:
                    for off, sz in head_parts:
                        o.write(mm[off:off + sz])
                hdr_pos = o.tell()
                new_data_start = hdr_pos + 16
                shift = new_data_start - data_start
                o.write(struct.pack('>I4sQ', 1, b'mdat', 16 + data_end - data_start))
                pos = data_start
                block = 16 << 20
                while pos < data_end:
                    n = min(block, data_end - pos)
                    o.write(mm[pos:pos + n])
                    pos += n
                moov_box, stats = build_moov(ref, scanner, shift, log)
                o.write(moov_box.to_bytes())
            os.replace(tmp, out_path)
        finally:
            mm.close()

    log('')
    log('Resultado: %s' % out_path)
    for t, n, secs in stats.values():
        log('  %s: %d muestras, %.2f s' % (t.name, n, secs))
    if scanner.skipped:
        log('  bytes ilegibles saltados: %d' % scanner.skipped)
    padding = end - scanner.data_limit
    lost = scanner.data_limit - data_end
    if padding > 0:
        log('  relleno de ceros al final (normal en Sony): %.1f MB' % (padding / 1048576))
    if lost > 0:
        log('  bytes finales no aprovechados: %d (último bloque incompleto)' % lost)
    return {'frames': frames, 'stats': stats, 'skipped': scanner.skipped,
            'tail_lost': lost, 'out': out_path}
