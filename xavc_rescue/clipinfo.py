"""Resumen rápido de un clip para mostrarlo en la tabla de resultados."""
import os

from . import mp4, sonymeta


def summary(path):
    info = {'duracion': None, 'fps': None, 'codec': None, 'perfil': None, 'fecha': None,
            'camara': None, 'resolucion': None}
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
    meta = None
    try:
        meta = sonymeta.find_in_file(path)
    except OSError:
        pass
    if meta:
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
