"""Metadatos Sony (NonRealTimeMeta XML): modelo, códec, gamma S-Log3 / S-Cinetone, etc.

Las cámaras Sony guardan este XML dentro del propio clip y también como archivo
aparte (PRIVATE/M4ROOT/CLIP/C0001M01.XML en XAVC S/HS; XDROOT/Clip/*M01.XML en XAVC MXF).
"""
import os
import re
import xml.etree.ElementTree as ET

GAMMA_NAMES = {
    's-log3-cine': 'S-Log3 (con S-Gamut3.Cine)',
    's-log3': 'S-Log3',
    's-log2': 'S-Log2',
    's-cinetone': 'S-Cinetone',
    'rec2100-hlg': 'HLG (BT.2100)',
    'rec709': 'Rec.709 (estándar)',
    'rec709-xvycc': 'Rec.709 xvYCC',
}

SCAN_BYTES = 8 * 1024 * 1024


def describe_gamma(value):
    if not value:
        return None
    v = value.strip().lower()
    if v in GAMMA_NAMES:
        return GAMMA_NAMES[v]
    if 'cinetone' in v:
        return 'S-Cinetone'
    if 'log3' in v:
        return 'S-Log3'
    if 'log2' in v:
        return 'S-Log2'
    if 'hlg' in v:
        return 'HLG'
    return value


def describe_video_codec(value):
    """'AVC_3840_2160_HP@L51' -> 'H.264 (AVC) 3840x2160 HP@L51'."""
    if not value:
        return None
    m = re.match(r'(AVC|HEVC|MPEG2)(\d*)_(\d+)_(\d+)_?(.*)', value)
    if not m:
        return value
    name = {'AVC': 'H.264 (AVC)', 'HEVC': 'H.265 (HEVC)', 'MPEG2': 'MPEG-2'}[m.group(1)]
    profile = m.group(5).replace('_', ' ')
    if m.group(1) == 'HEVC':
        family = 'XAVC HS'
    elif 'IP@' in profile:
        family = 'XAVC S-I (All-Intra)'
    else:
        family = 'XAVC S (Long GOP)'
    rate = ' %s Mbps' % m.group(2) if m.group(2) else ''
    sub = ' 4:2:2' if '422' in profile else ''
    return '%s %sx%s%s%s — %s [%s]' % (name, m.group(3), m.group(4), rate, sub, family, profile)


def short_video_codec(value):
    """'AVC200_3840_2160_H422P@L52' -> 'XAVC S 4K 200M 4:2:2' (para tablas)."""
    if not value:
        return None
    m = re.match(r'(AVC|HEVC|MPEG2)(\d*)_(\d+)_(\d+)_?(.*)', value)
    if not m:
        return value
    width = int(m.group(3))
    res = {3840: '4K', 4096: '4K', 7680: '8K', 1920: 'HD', 1280: 'HD 720'}.get(
        width, '%sx%s' % (m.group(3), m.group(4)))
    profile = m.group(5)
    family = 'XAVC HS' if m.group(1) == 'HEVC' else 'XAVC S-I' if 'IP@' in profile else 'XAVC S'
    parts = [family, res]
    if m.group(2):
        parts.append('%sM' % m.group(2))
    if '422' in profile:
        parts.append('4:2:2')
    return ' '.join(parts)


def extract_xml(blob):
    """Busca el bloque <NonRealTimeMeta> dentro de unos bytes."""
    i = blob.find(b'<NonRealTimeMeta')
    if i < 0:
        return None
    start = blob.rfind(b'<?xml', max(0, i - 512), i)
    if start < 0:
        start = i
    end = blob.find(b'</NonRealTimeMeta>', i)
    if end < 0:
        return None
    return bytes(blob[start:end + len(b'</NonRealTimeMeta>')])


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def parse_xml(xml_bytes):
    """Devuelve un dict plano con los campos útiles para recuperación."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # a veces hay basura al final; recortamos y reintentamos
        try:
            root = ET.fromstring(xml_bytes.split(b'\0', 1)[0])
        except ET.ParseError:
            return None
    info = {'items': {}}
    for el in root.iter():
        tag = _local(el.tag)
        a = el.attrib
        if tag == 'Device':
            info['fabricante'] = a.get('manufacturer')
            info['modelo'] = a.get('modelName')
            info['serie'] = a.get('serialNo')
        elif tag == 'CreationDate':
            info['fecha'] = a.get('value')
        elif tag == 'Duration':
            info['duracion_frames'] = a.get('value')
        elif tag == 'VideoFrame':
            info['codec_video'] = a.get('videoCodec')
            info['fps_captura'] = a.get('captureFps')
            info['fps_formato'] = a.get('formatFps')
        elif tag == 'VideoLayout':
            info['resolucion'] = '%sx%s' % (a.get('pixel'), a.get('numOfVerticalLine'))
            info['aspecto'] = a.get('aspectRatio')
        elif tag == 'AudioFormat':
            info['canales_audio'] = a.get('numOfChannel')
        elif tag == 'AudioRecPort' and 'codec_audio' not in info:
            info['codec_audio'] = a.get('audioCodec')
        elif tag == 'LtcChange' and 'timecode_inicio' not in info:
            info['timecode_inicio'] = a.get('value')
        elif tag == 'LtcChangeTable':
            info['tc_fps'] = a.get('tcFps')
        elif tag == 'Item' and a.get('name'):
            info['items'][a['name']] = a.get('value')
    items = info['items']
    info['gamma'] = items.get('CaptureGammaEquation')
    info['gamut'] = items.get('CaptureColorPrimaries')
    info['matriz'] = items.get('CodingEquations')
    info['gamma_desc'] = describe_gamma(info['gamma'])
    info['codec_desc'] = describe_video_codec(info.get('codec_video'))
    return info


def find_in_file(path):
    """Busca el XML Sony al inicio y al final del clip, y luego el sidecar *M01.XML."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(min(size, SCAN_BYTES))
        xml = extract_xml(head)
        if xml is None and size > SCAN_BYTES:
            f.seek(max(0, size - SCAN_BYTES))
            xml = extract_xml(f.read())
    source = 'dentro del clip'
    if xml is None:
        side = sidecar_path(path)
        if side:
            with open(side, 'rb') as f:
                xml = extract_xml(f.read())
            source = 'archivo %s' % os.path.basename(side)
    if xml is None:
        return None
    info = parse_xml(xml)
    if info is not None:
        info['origen'] = source
        info['xml'] = xml
    return info


def sidecar_path(path):
    base, _ = os.path.splitext(path)
    for cand in (base + 'M01.XML', base + 'M01.xml', base + '.XML', base + '.xml'):
        if os.path.isfile(cand):
            return cand
    return None


def format_info(info):
    if not info:
        return ['  No se encontraron metadatos Sony (NonRealTimeMeta).']
    rows = [
        ('Cámara', ' '.join(x for x in (info.get('fabricante'), info.get('modelo')) if x)),
        ('Fecha', info.get('fecha')),
        ('Códec', info.get('codec_desc')),
        ('Resolución', info.get('resolucion')),
        ('FPS captura / formato', '%s / %s' % (info.get('fps_captura'), info.get('fps_formato'))
         if info.get('fps_formato') else None),
        ('Gamma (perfil)', '%s  [valor: %s]' % (info['gamma_desc'], info['gamma'])
         if info.get('gamma') else None),
        ('Gamut', info.get('gamut')),
        ('Matriz', info.get('matriz')),
        ('Audio', '%s canal(es) %s' % (info.get('canales_audio'), info.get('codec_audio') or '')
         if info.get('canales_audio') else None),
        ('Timecode inicio', info.get('timecode_inicio')),
        ('Duración (frames)', info.get('duracion_frames')),
        ('Origen metadatos', info.get('origen')),
    ]
    return ['  %-22s %s' % (k + ':', v) for k, v in rows if v]
