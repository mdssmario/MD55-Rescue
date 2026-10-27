"""XAVC en MXF (FX9, FX6 en algunos modos, Venice, PXW-Z...): diagnóstico y extracción
de la esencia de vídeo/audio cuando el archivo quedó sin cerrar (sin footer)."""
import mmap
import os
import struct
import wave

PP_PREFIX = bytes.fromhex('060e2b34020501010d01020101')       # partition pack (13 bytes)
RIP_KEY = bytes.fromhex('060e2b34020501010d01020101110100')
ESSENCE_PREFIX = bytes.fromhex('060e2b34010201010d010301')    # elemento de esencia GC
UL_PREFIX = bytes.fromhex('060e2b34')

STATUS = {1: 'abierta e incompleta', 2: 'cerrada e incompleta',
          3: 'abierta y completa', 4: 'cerrada y completa'}
KIND = {2: 'cabecera', 3: 'cuerpo', 4: 'pie (footer)'}


def is_mxf(head):
    i = head.find(PP_PREFIX)
    return 0 <= i < 65536 and len(head) > i + 13 and head[i + 13] == 2


def read_klv(mm, pos):
    """(clave, tamaño cabecera, longitud) o None si no hay un KLV válido."""
    if pos + 17 > len(mm) or mm[pos:pos + 4] != UL_PREFIX:
        return None
    b = mm[pos + 16]
    if b < 0x80:
        return bytes(mm[pos:pos + 16]), 17, b
    n = b & 0x7F
    if n == 0 or n > 8 or pos + 17 + n > len(mm):
        return None
    return bytes(mm[pos:pos + 16]), 17 + n, int.from_bytes(mm[pos + 17:pos + 17 + n], 'big')


def partition(mm, pos):
    klv = read_klv(mm, pos)
    if not klv or klv[0][:13] != PP_PREFIX:
        return None
    key, hl, length = klv
    v = mm[pos + hl:pos + hl + 64]
    if len(v) < 48:
        return None
    major, minor, kag, this, prev, footer, hbc, ibc = struct.unpack('>HHIQQQQQ', v[:48])
    return {'kind': key[13], 'status': key[14], 'this': this, 'footer': footer,
            'header_bytes': hbc, 'kag': kag, 'size': hl + length}


def _find_rational(mm, tag, limit, lo, hi):
    pat = struct.pack('>HH', tag, 8)
    i = mm.find(pat, 0, limit)
    while i >= 0:
        num, den = struct.unpack('>II', mm[i + 4:i + 12])
        if den and lo <= num / den <= hi:
            return num, den
        i = mm.find(pat, i + 1, limit)
    return None


def _find_u32(mm, tag, limit, lo, hi):
    pat = struct.pack('>HH', tag, 4)
    i = mm.find(pat, 0, limit)
    while i >= 0:
        v = struct.unpack('>I', mm[i + 4:i + 8])[0]
        if lo <= v <= hi:
            return v
        i = mm.find(pat, i + 1, limit)
    return None


def walk(mm, start=0, on_item=None):
    """Recorre KLVs; resincroniza en la siguiente clave SMPTE si hay basura."""
    pos = start
    end = len(mm)
    stats = {'picture': 0, 'sound': 0, 'data': 0, 'other': 0, 'skipped': 0,
             'partitions': [], 'rip': False, 'last_good': start}
    while pos < end:
        klv = read_klv(mm, pos)
        if klv is None or pos + klv[1] + klv[2] > end:
            if klv is not None:
                break           # el último KLV está cortado
            nxt = mm.find(UL_PREFIX, pos + 1)
            if nxt < 0:
                break
            stats['skipped'] += nxt - pos
            pos = nxt
            continue
        key, hl, length = klv
        if key[:13] == PP_PREFIX and key[13] in (2, 3, 4):
            stats['partitions'].append((pos, key[13], key[14]))
        elif key == RIP_KEY:
            stats['rip'] = True
        elif key[:12] == ESSENCE_PREFIX:
            item = key[12]
            if item == 0x15:
                stats['picture'] += 1
            elif item == 0x16:
                stats['sound'] += 1
            elif item == 0x17:
                stats['data'] += 1
            else:
                stats['other'] += 1
            if on_item:
                on_item(key, pos + hl, length)
        pos += hl + length
        stats['last_good'] = pos
    return stats


def analyze(path):
    lines = ['Contenedor: MXF (XAVC / XDCAM)']
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            start = mm.find(PP_PREFIX, 0, 65536)
            hp = partition(mm, start)
            if hp:
                lines.append('  partición de cabecera: %s' % STATUS.get(hp['status'], '?'))
                if hp['footer']:
                    fp = partition(mm, start + hp['footer'])
                    lines.append('  footer declarado en %d: %s' % (
                        hp['footer'], 'presente' if fp else 'NO ENCONTRADO'))
                else:
                    lines.append('  sin footer declarado (la grabación no se cerró)')
            fps = _find_rational(mm, 0x3001, min(len(mm), 1 << 20), 10, 121)
            if fps:
                lines.append('  edit rate: %d/%d (%.3f fps)' % (fps[0], fps[1], fps[0] / fps[1]))
            st = walk(mm, start)
            lines.append('  elementos de vídeo: %d   audio: %d   datos: %d' % (
                st['picture'], st['sound'], st['data']))
            lines.append('  particiones: %s' % ', '.join(
                '%s@%d' % (KIND.get(k, k), p) for p, k, _s in st['partitions'][:8]))
            if st['skipped']:
                lines.append('  bytes no reconocibles saltados: %d' % st['skipped'])
            lines.append('')
            lines.append('Diagnóstico:')
            if hp and hp['status'] in (3, 4) and st['rip']:
                lines.append('  • Estructura MXF completa.')
            else:
                lines.append('  • MXF sin cerrar o truncado: usa "Extraer MXF" para sacar el vídeo y el '
                             'audio y volver a empaquetarlos (con ffmpeg instalado se hace solo).')
        finally:
            mm.close()
    return lines


def _write_wav(path, pcm_le, channels, bits, rate):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(pcm_le)


def _aes3_to_pcm(payload, channels):
    """SMPTE 331M: cabecera de 4 bytes + 8 palabras de 32 bits por muestra."""
    n = struct.unpack_from('<H', payload, 1)[0]
    body = payload[4:4 + n * 32]
    try:
        import numpy as np
        w = np.frombuffer(body, dtype='<u4').reshape(-1, 8)[:, :channels]
        v = (w >> 4) & 0xFFFFFF
        out = np.empty(v.shape + (3,), dtype=np.uint8)
        out[..., 0] = v & 0xFF
        out[..., 1] = (v >> 8) & 0xFF
        out[..., 2] = (v >> 16) & 0xFF
        return out.tobytes()
    except ImportError:
        out = bytearray()
        for s in range(n):
            for c in range(channels):
                w = struct.unpack_from('<I', body, (s * 8 + c) * 4)[0]
                out += ((w >> 4) & 0xFFFFFF).to_bytes(3, 'little')
        return bytes(out)


def extract(path, out_dir, log=print):
    """Extrae el vídeo (Annex B .h264/.h265) y el audio (.wav) de un MXF, aunque esté sin cerrar."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            start = max(0, mm.find(PP_PREFIX, 0, 65536))
            hdr_limit = min(len(mm), 4 << 20)
            fps = _find_rational(mm, 0x3001, hdr_limit, 10, 121)
            bits = _find_u32(mm, 0x3D01, hdr_limit, 8, 32) or 24
            rate = _find_rational(mm, 0x3D03, hdr_limit, 8000, 192000)
            rate = int(rate[0] / rate[1]) if rate else 48000
            vpath = None
            vfile = None
            audio = {}

            def on_item(key, pos, length):
                nonlocal vfile, vpath
                item, etype, enum = key[12], key[14], key[15]
                data = mm[pos:pos + length]
                if item == 0x15:
                    if vfile is None:
                        i = data.find(b'\x00\x00\x01')
                        h = data[i + 3] if 0 <= i < len(data) - 4 else 0
                        ext = '.h265' if (h >> 1) & 0x3F in (32, 33, 34, 35, 39) and not h & 0x80 \
                            and data[i + 4] == 1 else '.h264'
                        vpath = os.path.join(out_dir, base + '_video' + ext)
                        vfile = open(vpath, 'wb')
                    vfile.write(data)
                elif item == 0x16:
                    audio.setdefault((etype, enum), bytearray()).extend(
                        _aes3_to_pcm(data, 8) if etype in (0x03, 0x04) else data)

            st = walk(mm, start, on_item)
        finally:
            if vfile:
                vfile.close()
            mm.close()

    log('MXF: %d fotogramas de vídeo, %d elementos de audio' % (st['picture'], st['sound']))
    if vpath:
        log('  vídeo: %s' % vpath)
    wavs = []
    for i, ((etype, enum), pcm) in enumerate(sorted(audio.items())):
        ch = 8 if etype in (0x03, 0x04) else 1
        b = 24 if etype in (0x03, 0x04) else bits
        frame = ch * b // 8
        pcm = bytes(pcm[:len(pcm) // frame * frame])
        wp = os.path.join(out_dir, '%s_audio%d.wav' % (base, i + 1))
        _write_wav(wp, pcm, ch, b, rate)
        wavs.append(wp)
        log('  audio: %s (%d canal(es), %d bits, %d Hz)' % (wp, ch, b, rate))
    return {'video': vpath, 'audio': wavs, 'fps': fps, 'stats': st}
