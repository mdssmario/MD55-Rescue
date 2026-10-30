"""Diagnóstico de un clip Sony: estructura, daños y perfil de imagen."""
import os
import re
import struct

from . import mp4, mxf, nal, sonymeta

RTMD_SIGNATURE = b'\x00\x1c\x01\x00'


def _zero_ratio(f, start, end, probes=64, block=4096):
    if end - start < block:
        return 0.0
    zeros = 0
    for i in range(probes):
        f.seek(start + (end - start - block) * i // max(1, probes - 1))
        if not f.read(block).strip(b'\0'):
            zeros += 1
    return zeros / probes


def _data_limit(f, start, end, step=1 << 20):
    pos = end
    while pos > start:
        a = max(start, pos - step)
        f.seek(a)
        stripped = f.read(pos - a).rstrip(b'\0')
        if stripped:
            return a + len(stripped)
        pos = a
    return start


def _raw_stream(data):
    """Pistas de que un archivo sin cajas es el mdat de una grabación Sony sin cerrar."""
    found = []
    if data[:4] == RTMD_SIGNATURE:
        found.append('empieza por los metadatos por fotograma de Sony (rtmd)')
    model = re.search(rb'(ILCE|ILME|ZV|DSC|PXW|FX)-?[0-9A-Z]{1,8}', data[:65536])
    if model and found:
        found.append('cámara: %s' % model.group().decode('latin1'))
    # cada fotograma empieza por un delimitador de acceso (AUD): H.264 «00 00 00 02 09», H.265 «… 03 46 01»
    for m in re.finditer(rb'\x00\x00\x00\x02\x09|\x00\x00\x00\x03\x46\x01', data[:1 << 20]):
        start = m.start()
        det = nal.detect_length_prefixed(data, start, len(data))
        if det:
            kind, _cnt, _types = det
            found.append('vídeo %s a partir del byte %d' % (
                'H.264 / XAVC S' if kind == nal.AVC else 'H.265 / XAVC HS', start))
            break
    return found if len(found) > 1 or (found and 'vídeo' in found[0]) else []


def analyze(path):
    lines = []
    size = os.path.getsize(path)
    lines.append('Archivo: %s' % path)
    lines.append('Tamaño: %s bytes (%.1f MB)' % (format(size, ','), size / 1048576))
    with open(path, 'rb') as f:
        head = f.read(64)
    if mxf.is_mxf(head):
        lines += mxf.analyze(path)
        lines.append('')
        lines.append('Metadatos Sony:')
        lines += sonymeta.format_info(sonymeta.find_in_file(path))
        return '\n'.join(lines)

    verdict = []
    with open(path, 'rb') as f:
        tops = mp4.scan_top_level(f, size)
        if not tops:
            f.seek(0)
            peek = f.read(4 << 20)
            raw = _raw_stream(peek)
            if raw:
                lines.append('Grabación sin cabecera ni índice%s: los datos de vídeo empiezan en el byte 0.'
                             % (' (archivo RSV de Sony)' if path.upper().endswith('.RSV') else ''))
                lines += ['  ' + x for x in raw]
                verdict.append('GRABACIÓN INTERRUMPIDA: la cámara no llegó a cerrar el archivo (batería, '
                               'tarjeta extraída). Los fotogramas están; usa "Reparar" con un clip de '
                               'referencia grabado con la misma cámara y configuración.')
            else:
                lines.append('No se reconoce ninguna estructura MP4/MXF al inicio del archivo.')
                lines.append('Puede que el inicio esté dañado o que sea un fragmento recuperado.')
                verdict.append('Prueba "Reparar" con un clip de referencia: busca el vídeo aunque no haya '
                               'cabeceras.')
        else:
            lines.append('Cajas de primer nivel:')
            for t in tops:
                extra = ''
                if t.type == b'ftyp':
                    f.seek(t.data_start)
                    d = f.read(min(t.size - t.hsize, 64))
                    brands = [d[i:i + 4].decode('latin1') for i in range(0, len(d), 4) if i != 4]
                    extra = '  marca: %s' % ', '.join(brands)
                    if b'XAVC' in d:
                        extra += '  (XAVC de Sony)'
                if t.truncated:
                    extra += '  ← CORTADA: el archivo termina antes (%d bytes de %d)' % (
                        size - t.offset, t.size)
                if t.open_ended:
                    extra += '  ← tamaño 0 (sin cerrar)'
                lines.append('  %-5s offset %-14d tamaño %-14d%s' % (
                    t.type.decode('latin1'), t.offset, t.size, extra))
            last = tops[-1]
            if last.end < size and not last.truncated and not last.open_ended:
                lines.append('  (%d bytes sin estructura tras la última caja)' % (size - last.end))

        moov_tb = next((t for t in tops if t.type == b'moov' and not t.truncated), None)
        mdat_tb = next((t for t in tops if t.type == b'mdat'), None)

        if moov_tb is not None:
            f.seek(moov_tb.data_start)
            try:
                moov = mp4.Box(b'moov', children=mp4.parse_boxes(f.read(moov_tb.size - moov_tb.hsize)))
                lines.append('')
                lines.append('Índice (moov) presente:')
                mts, mdur = mp4.read_mdhd(moov.child(b'mvhd').payload)
                lines.append('  duración: %.2f s' % (mdur / mts if mts else 0))
                for i, trak in enumerate(c for c in moov.children if c.type == b'trak'):
                    mdia = trak.child(b'mdia')
                    h = mp4.read_hdlr(mdia.child(b'hdlr').payload).decode('latin1')
                    ts, dur = mp4.read_mdhd(mdia.child(b'mdhd').payload)
                    stbl = mdia.path(b'minf', b'stbl')
                    cc = mp4.stsd_fourcc(stbl.child(b'stsd').payload).decode('latin1', 'replace')
                    stsz = stbl.child(b'stsz')
                    n = struct.unpack_from('>I', stsz.payload, 8)[0] if stsz else 0
                    lines.append('  pista %d: %s %s, %d muestras, %.2f s' % (
                        i + 1, h, cc, n, dur / ts if ts else 0))
                    if n == 0 and h == 'vide':
                        verdict.append('El índice existe pero está vacío: hay que reconstruirlo (Reparar con --forzar).')
                if not verdict:
                    verdict.append('La estructura parece correcta. Si no reproduce, el daño está en los datos '
                                   '(sectores ilegibles o sobrescritos), no en el índice.')
            except (ValueError, struct.error, AttributeError) as e:
                lines.append('Índice (moov) presente pero ilegible: %s' % e)
                verdict.append('Índice dañado: usa Reparar con un clip de referencia y la opción forzar.')
        elif mdat_tb is not None:
            verdict.append('FALTA EL ÍNDICE (moov): la grabación se interrumpió (batería, tarjeta extraída, '
                           'apagado). Los datos de vídeo están; usa "Reparar" con un clip de referencia '
                           'grabado con la misma cámara y la misma configuración.')

        if mdat_tb is not None:
            start = mdat_tb.data_start
            end = min(size, mdat_tb.end)
            limit = _data_limit(f, start, end)
            zr = _zero_ratio(f, start, limit)
            lines.append('')
            lines.append('Datos (mdat): %.1f MB disponibles' % ((end - start) / 1048576))
            if end - limit > 4096:
                lines.append('  relleno de ceros al final: %.1f MB (normal en Sony: reserva en '
                             'bloques de 64 MB)' % ((end - limit) / 1048576))
            if zr > 0.5:
                verdict.append('El %.0f %% de los datos muestreados son ceros: probablemente la tarjeta se '
                               'formateó a bajo nivel o los sectores no se llegaron a escribir.' % (zr * 100))
            elif zr > 0.05:
                lines.append('  aviso: %.0f %% de bloques a cero (zonas vacías/sobrescritas)' % (zr * 100))
            f.seek(start)
            peek = f.read(1 << 20)
            det = nal.detect_length_prefixed(peek, 0, len(peek))
            if det:
                kind, _cnt, types = det
                names = nal.AVC_NAMES if kind == nal.AVC else nal.HEVC_NAMES
                lines.append('  inicio del vídeo: %s, NAL %s' % (
                    'H.264 / XAVC S' if kind == nal.AVC else 'H.265 / XAVC HS',
                    ' '.join(names.get(t, str(t)) for t in types[:8])))
            else:
                lines.append('  el primer bloque del mdat no es vídeo (normal si empieza por audio/metadatos)')

    lines.append('')
    lines.append('Metadatos Sony:')
    lines += sonymeta.format_info(sonymeta.find_in_file(path))
    lines.append('')
    lines.append('Diagnóstico:')
    for v in verdict or ['Sin problemas estructurales detectados.']:
        lines.append('  • ' + v)
    return '\n'.join(lines)
