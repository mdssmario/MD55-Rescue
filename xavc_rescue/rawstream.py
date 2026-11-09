"""Grabaciones Sony sin cabecera ni índice: el RSV que deja la cámara al cortarse la
grabación, o los datos de un clip cuya cabecera se perdió.

Sony intercala los datos en bloques: metadatos por fotograma (rtmd) → vídeo → audio PCM. El
primer bloque empieza donde empieza el archivo (alineado a sector), así que unos metadatos rtmd
alineados marcan el posible inicio de una grabación; recorriendo los bloques se sabe dónde
termina sin necesidad de un clip de referencia.
"""
import re

from . import nal

RTMD_SIGNATURE = b'\x00\x1c\x01\x00'
KLV_KEY = b'\x06\x0e\x2b\x34'          # clave SMPTE dentro de cada muestra rtmd
WINDOW = 16 << 20
MAX_AUDIO = 8 << 20                    # audio de un bloque: como mucho unos MB de PCM
CAMERA_RE = re.compile(rb'(?:ILCE|ILME|ZV|DSC|PXW|FX)-[0-9A-Z]{1,8}')


def is_rtmd(data, i=0):
    return data[i:i + 4] == RTMD_SIGNATURE and data[i + 28:i + 32] == KLV_KEY


def _next_rtmd(data, start):
    i = data.find(RTMD_SIGNATURE, start)
    while i >= 0 and not is_rtmd(data, i):
        i = data.find(RTMD_SIGNATURE, i + 1)
    return i


def _nal_fits(data, p, kind):
    """True si en p empieza un NAL válido (aunque no quepa en la ventana leída)."""
    if p + 4 + nal.header_size(kind) > len(data):
        return False
    n = int.from_bytes(data[p:p + 4], 'big')
    b0 = data[p + 4]
    b1 = data[p + 5] if kind == nal.HEVC else 0
    return nal.header_size(kind) <= n < (1 << 28) and nal.header_ok(kind, b0, b1)


def _video(src, pos, limit, kind):
    """Recorre los NAL de vídeo desde pos. Devuelve (posición tras el vídeo, fotogramas)."""
    aud = 9 if kind == nal.AVC else 35       # delimitador de acceso: Sony pone uno por fotograma
    frames = auds = 0
    while pos < limit:
        buf = src.read(pos, min(WINDOW, limit - pos))
        p = 0
        # el vídeo de un bloque nunca pasa del siguiente rtmd: el audio que lo precede (o el
        # propio rtmd, en H.264) puede parecer un NAL válido de varios MB
        stop = _next_rtmd(buf, 0)
        stop = len(buf) if stop < 0 else stop
        for q, size, t in nal.iter_length_prefixed(buf, 0, stop, 4, kind):
            if t == aud:
                auds += 1
            elif nal.is_vcl(kind, t) and nal.first_in_picture(kind, buf, q + 4):
                frames += 1
            p = q + 4 + size
        pos += p
        # el recorrido para en un NAL inválido (empieza el audio) o al final de la ventana
        if len(buf) < WINDOW or not _nal_fits(buf, p, kind) or p == 0:
            break
    return pos, auds or frames


def extent(src, start, limit):
    """Si en start empieza una grabación Sony sin cabecera, devuelve un dict con su final
    ('end'), los fotogramas, el códec y la cámara; si no, None."""
    head = src.read(start, 1 << 20)
    if not is_rtmd(head):
        return None
    sample = _next_rtmd(head, 4)            # tamaño de una muestra rtmd
    if sample <= 0:
        return None
    model = CAMERA_RE.search(head[:65536])
    pos, end, frames, kind, chunks, audio = start, start, 0, None, 0, 0
    while pos < limit:
        n = 0
        while pos < limit and is_rtmd(src.read(pos, 32)):
            pos += sample
            n += 1
        if not n:
            break
        if kind is None:
            det = nal.detect_length_prefixed(src.read(pos, 1 << 20), 0, 1 << 20)
            if not det:
                break
            kind = det[0]
        vend, got = _video(src, pos, limit, kind)
        if not got:
            break
        frames += got
        chunks += 1
        tail = src.read(vend, min(MAX_AUDIO, limit - vend))
        j = _next_rtmd(tail, 0)
        if j < 0:
            # último bloque: el audio llega como mucho hasta donde llegó el del anterior
            end = min(limit, vend + (audio or len(tail.rstrip(b'\0'))))
            break
        audio = j
        pos = end = vend + j
    if not frames:
        return None
    return {'end': end, 'frames': frames, 'chunks': chunks, 'kind': kind,
            'camara': model.group().decode('latin1') if model else None}


def codec_label(kind):
    return 'XAVC HS (H.265)' if kind == nal.HEVC else 'XAVC S (H.264)'
