"""Resumen rápido de un clip para mostrarlo en la tabla de resultados."""
import os

from . import mp4, sonymeta


def _empty():
    return {'duracion': None, 'fps': None, 'codec': None, 'perfil': None, 'fecha': None,
            'camara': None, 'resolucion': None}


def _fill(info, meta):
    """Completa info con los metadatos Sony (NonRealTimeMeta)."""
    if not meta:
        return info
    info['codec'] = sonymeta.short_video_codec(meta.get('codec_video'))
    if meta.get('fps_formato'):
        info['codec'] = '%s %s' % (info['codec'] or '', meta['fps_formato'])
    gamma = sonymeta.describe_gamma(meta.get('gamma'))
    if gamma and gamma.startswith('S-Log3 (con S-Gamut3.Cine)'):
        gamma = 'S-Log3 · S-Gamut3.Cine'
    info['perfil'] = gamma
    info['fecha'] = (meta.get('fecha') or '').replace('T', ' ')[:16] or None
    info['camara'] = meta.get('modelo')
    info['resolucion'] = meta.get('resolucion')
    info['fps'] = meta.get('fps_formato')
    return info


def summary_blobs(blobs):
    """Igual que summary() pero a partir de trozos ya leídos (p.ej. de la tarjeta):
    busca el 'mvhd' para la duración y el XML de Sony para el resto."""
    info = _empty()
    for b in blobs:
        i = b.find(b'mvhd')
        if i >= 0 and info['duracion'] is None:
            try:
                ts, dur = mp4.read_mdhd(bytes(b[i + 4:i + 40]))
                if ts:
                    info['duracion'] = dur / ts
            except Exception:       # noqa: BLE001 — datos de tarjeta, pueden venir rotos
                pass
        xml = sonymeta.extract_xml(b)
        if xml:
            _fill(info, sonymeta.parse_xml(xml))
    return info


def summary(path):
    info = _empty()
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            tops = mp4.scan_top_level(f, size)
            moov = next((t for t in tops if t.type == b'moov' and not t.truncated), None)
            if moov is not None:
                f.seek(moov.data_start)
                box = mp4.Box(b'moov', children=mp4.parse_boxes(f.read(moov.size - moov.hsize)))
                ts, dur = mp4.read_mdhd(box.child(b'mvhd').payload)
                info['duracion'] = dur / ts if ts else None
    except (OSError, ValueError, AttributeError):
        pass
    try:
        _fill(info, sonymeta.find_in_file(path))
    except OSError:
        pass
    return info
