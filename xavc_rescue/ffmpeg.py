"""ffmpeg es opcional: sirve para verificar el resultado y re-empaquetar MXF."""
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def find(name='ffmpeg'):
    exe = name + ('.exe' if sys.platform == 'win32' else '')
    for d in (os.path.join(_HERE, '..', 'bin'), os.path.join(_HERE, 'bin')):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return shutil.which(name)


def _run(args):
    flags = 0x08000000 if sys.platform == 'win32' else 0      # sin ventana de consola
    return subprocess.run(args, capture_output=True, text=True, errors='replace',
                          creationflags=flags)


def verify(path, log=print):
    """Decodifica el vídeo entero y cuenta errores."""
    exe = find()
    if not exe:
        log('(ffmpeg no instalado: no se verifica la decodificación)')
        return None
    log('Verificando con ffmpeg (decodificando todo el clip)...')
    r = _run([exe, '-v', 'error', '-i', path, '-map', '0:v:0', '-f', 'null', '-'])
    errors = [l for l in r.stderr.splitlines() if l.strip()]
    if r.returncode == 0 and not errors:
        log('  verificación OK: se decodifica sin errores.')
    else:
        log('  %d mensajes de error al decodificar. Primeros:' % len(errors))
        for l in errors[:8]:
            log('    ' + l)
    return errors


def mux_mxf_parts(video, wavs, fps, out, log=print):
    exe = find()
    if not exe or not video:
        return False
    args = [exe, '-y', '-v', 'error', '-fflags', '+genpts']
    if fps:
        args += ['-r', '%d/%d' % fps]
    args += ['-i', video]
    for w in wavs:
        args += ['-i', w]
    args += ['-map', '0:v']
    for i in range(len(wavs)):
        args += ['-map', '%d:a' % (i + 1)]
    args += ['-c:v', 'copy', '-c:a', 'pcm_s24le', out]
    r = _run(args)
    if r.returncode == 0:
        log('  re-empaquetado: %s' % out)
        return True
    log('  ffmpeg no pudo re-empaquetar: %s' % r.stderr.strip()[:400])
    return False


def remux(src, out, log=print):
    exe = find()
    if not exe:
        return False
    r = _run([exe, '-y', '-v', 'error', '-i', src, '-map', '0', '-c', 'copy', out])
    if r.returncode == 0 and os.path.getsize(out) > 0:
        log('  ffmpeg re-empaquetó el MXF directamente: %s' % out)
        return True
    return False
