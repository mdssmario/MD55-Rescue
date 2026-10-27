"""Reconstrucción de clips Sony tal como quedan repartidos en la tarjeta.

En la tarjeta (exFAT) la cámara no guarda el clip en un solo bloque contiguo:

    [ftyp/uuid/free + cabecera mdat]  ← donde está la firma 'ftyp'
    [hueco de 1–1.5 MB]               ← clústeres de otros archivos (o vacíos)
    [datos del mdat ......]
    [moov + meta]                     ← índice y XML (S-Log3...), al final de su reserva

Tras un formateo rápido la tabla de clústeres se pierde, pero las piezas siguen ahí.
Este módulo empareja cada 'moov' huérfano con su cabecera comprobando que los tamaños de
fotograma del índice encajan exactamente con las unidades NAL de los datos, localiza el
desplazamiento del hueco y vuelve a montar el archivo original.
"""
import struct

from . import mp4, nal

TRAILING_BOXES = {b'meta', b'uuid', b'free', b'skip', b'udta'}
MAX_GAP = 64 << 20
VERIFY_READ = 64 << 10


class OrphanMoov:
    """'moov' (+ cajas que le siguen) encontrado suelto en la tarjeta."""

    def __init__(self, pos, moov_bytes, tail_bytes):
        self.pos = pos
        self.moov_bytes = moov_bytes
        self.tail_bytes = tail_bytes
        self.used = False
        moov = mp4.Box(b'moov', children=mp4.parse_boxes(moov_bytes, 8))
        self.video = None
        self.data_end = 0
        self.data_min = None
        for trak in moov.all(b'trak'):
            stbl = trak.path(b'mdia', b'minf', b'stbl')
            if stbl is None or stbl.child(b'stsd') is None:
                continue
            stsd = stbl.child(b'stsd').payload
            co = stbl.child(b'co64') or stbl.child(b'stco')
            stsz = stbl.child(b'stsz')
            stsc = stbl.child(b'stsc')
            if co is None or stsz is None or stsc is None:
                continue
            offs = mp4.read_stco(co.payload, co.type == b'co64')
            _fixed, sizes = mp4.read_stsz(stsz.payload)
            entries = mp4.read_stsc(stsc.payload)
            spc = []
            for i, (first, n, _d) in enumerate(entries):
                last = entries[i + 1][0] if i + 1 < len(entries) else len(offs) + 1
                spc += [n] * (last - first)
            s = 0
            firsts = []
            for off, n in zip(offs, spc):
                firsts.append(s)
                end = off + sum(sizes[s:s + n])
                self.data_end = max(self.data_end, end)
                self.data_min = off if self.data_min is None else min(self.data_min, off)
                s += n
            fourcc = mp4.stsd_fourcc(stsd)
            kind = nal.CODEC_KIND.get(fourcc)
            if kind and self.video is None and offs:
                self.video = (kind, mp4.stsd_length_size(stsd), offs, sizes, firsts)
                self.video_fourcc = fourcc
                i = stsd.find(b'avcC')
                self.avc_profile_level = (stsd[i + 5], stsd[i + 7]) if i >= 0 else None

    @property
    def frames(self):
        return len(self.video[3]) if self.video else 0


def read_orphan_moov(src, pos):
    h = src.read(pos, 16)
    size = struct.unpack('>I', h[:4])[0]
    if size < 16 or size > (64 << 20):
        return None
    moov_bytes = src.read(pos, size)
    tail = b''
    p = pos + size
    while True:
        h = src.read(p, 8)
        if len(h) < 8:
            break
        bsize, btype = struct.unpack('>I4s', h)
        if btype not in TRAILING_BOXES or bsize < 8 or bsize > (16 << 20):
            break
        tail += src.read(p, bsize)
        p += bsize
    try:
        om = OrphanMoov(pos, moov_bytes, tail)
    except (ValueError, struct.error, AttributeError):
        return None
    return om if om.video else None


def clip_header(src, start):
    """(offset del mdat, tamaño de cabecera, tamaño declarado) de la cabecera en start."""
    pos = start
    for _ in range(16):
        h = src.read(pos, 16)
        if len(h) < 16:
            return None
        size, btype = struct.unpack('>I4s', h[:8])
        hs = 8
        if not mp4.is_box_type(btype):
            return None
        if size == 1:
            size = struct.unpack('>Q', h[8:16])[0]
            hs = 16
        if btype == b'mdat':
            return pos - start, hs, size
        if size < hs or size > (64 << 20):
            return None
        pos += size
    return None


def _sample_exact(buf, off, size, kind, ls):
    if off + size > len(buf):
        return False
    end = None
    for p, n, _t in nal.iter_length_prefixed(buf, off, off + size, ls, kind):
        end = p + ls + n
    return end == off + size


def _chunk_plausible(buf, size, kind, ls):
    """Primer fotograma de un bloque: cadena NAL válida hasta su tamaño o hasta el final
    de lo leído (el último NAL puede quedar cortado por la ventana, no por basura)."""
    q = 0
    count = 0
    for p, n, _t in nal.iter_length_prefixed(buf, 0, min(len(buf), size), ls, kind):
        q = p + ls + n
        count += 1
    if count == 0:
        return False
    if q == size:
        return True
    hs = nal.header_size(kind)
    if q + ls + hs > len(buf):
        return True
    n = int.from_bytes(buf[q:q + ls], 'big')
    b1 = buf[q + ls + 1] if hs == 2 else 0
    return nal.header_ok(kind, buf[q + ls], b1) and len(buf) < q + ls + n <= size


def locate(src, start, mdat_off, om, log=None):
    """Busca el desplazamiento de los datos del clip que empieza en start para el moov om.

    Devuelve el desplazamiento (múltiplo de 512) o None si el moov no es de este clip.
    """
    kind, ls, offs, sizes, firsts = om.video
    first_off, first_size = offs[0], sizes[firsts[0]]
    if first_off < mdat_off:
        return None
    window = src.read(start + first_off, MAX_GAP + first_size)
    shift = None
    for d in range(0, MAX_GAP, 512):
        if d + ls + 2 > len(window):
            break
        n = int.from_bytes(window[d:d + ls], 'big')
        if n < 2 or n > first_size:
            continue
        if _sample_exact(window, d, first_size, kind, ls):
            shift = d
            break
    if shift is None:
        return None
    # comprobar el resto de bloques de vídeo con el mismo desplazamiento
    step = max(1, len(offs) // 300)
    for i in range(0, len(offs), step):
        size = sizes[firsts[i]]
        buf = src.read(start + offs[i] + shift, min(size, VERIFY_READ))
        if not _chunk_plausible(buf, size, kind, ls):
            if log:
                log('    el clip en 0x%X está fragmentado (bloque %d de %d no encaja)' % (
                    start, i + 1, len(offs)))
            return None
    return shift


SONY_ALLOC = 64 << 20


def _header_fits(header, om):
    """¿La caja PROF de Sony de esta cabecera describe el vídeo de este índice?
    True / False, o None si no se puede saber."""
    i = header.find(b'VPRF')
    if i < 0 or i + 20 > len(header):
        return None
    fourcc = header[i + 12:i + 16]
    if fourcc != getattr(om, 'video_fourcc', fourcc):
        return False
    if fourcc.startswith(b'avc') and getattr(om, 'avc_profile_level', None):
        return (header[i + 17], header[i + 19]) == om.avc_profile_level
    return True


def final_header(src, start, mdat_off, om):
    """Cabecera correcta del clip (ftyp/uuid/free hasta el mdat).

    La A9 III reescribe la cabecera definitiva de cada clip en el bloque que sigue a su
    índice; la que aparece delante de los datos suele ser de otro clip. Se elige la que
    coincide con el códec/perfil/nivel del índice.
    """
    own = src.read(start, mdat_off)
    after_pos = om.pos + max(0x40000, -(-(len(om.moov_bytes) + len(om.tail_bytes)) // 0x40000) * 0x40000)
    after = None
    if src.read(after_pos + 4, 8)[:4] == b'ftyp':
        hdr = clip_header(src, after_pos)
        if hdr and hdr[0] == mdat_off:
            after = src.read(after_pos, mdat_off)
    if after is not None and _header_fits(after, om):
        return after, 'cabecera final tras el índice'
    if _header_fits(own, om) is False and after is not None and _header_fits(after, om) is not False:
        return after, 'cabecera final tras el índice'
    return own, 'cabecera inicial'


def rebuild(src, start, mdat_off, mdat_hs, mdat_decl, shift, om, out_path, block=8 << 20):
    """Escribe el clip original: cabecera + datos (saltando el hueco) + moov + meta.

    Sony reserva el mdat en bloques de 64 MB y rellena con ceros lo que sobra; la cabecera
    que queda en la tarjeta puede declarar la reserva provisional (mayor), así que el tamaño
    se recalcula como el múltiplo de 64 MB que contiene los datos, que es el que deja la
    cámara al cerrar el archivo. Se copian los datos hasta el último útil (según el índice)
    y se repone el relleno de ceros.
    """
    header, origin = final_header(src, start, mdat_off, om)
    payload_needed = om.data_end - (mdat_off + mdat_hs)
    if header[8:12] == b'XAVC':
        size = -(-payload_needed // SONY_ALLOC) * SONY_ALLOC + mdat_hs
    else:
        size = mdat_decl if mdat_decl >= payload_needed + mdat_hs else payload_needed + mdat_hs
    with open(out_path, 'wb') as o:
        o.write(header + (struct.pack('>I4sQ', 1, b'mdat', size) if mdat_hs == 16
                          else struct.pack('>I4s', size, b'mdat')))
        pos = start + mdat_off + mdat_hs + shift
        end = start + om.data_end + shift
        while pos < end:
            chunk = src.read(pos, min(block, end - pos))
            if not chunk:
                break
            o.write(chunk)
            pos += len(chunk)
        pad = mdat_off + size - o.tell()
        while pad > 0:
            n = min(pad, block)
            o.write(b'\0' * n)
            pad -= n
        o.write(om.moov_bytes)
        o.write(om.tail_bytes)
    return size, origin
