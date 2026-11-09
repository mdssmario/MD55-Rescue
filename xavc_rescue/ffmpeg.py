"""ffmpeg verifica los clips reparados, saca fotogramas de vista previa y re-empaqueta MXF.

Las versiones publicadas lo llevan dentro (compilación LGPL, en bin/); desde el código fuente se
usa el del sistema si lo hay. Si no está, todo sigue funcionando sin esas comprobaciones."""
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def find(name='ffmpeg'):
    exe = name + ('.exe' if sys.platform == 'win32' else '')
    dirs = [os.path.join(_HERE, '..', 'bin'), os.path.join(_HERE, 'bin')]
    if getattr(sys, '_MEIPASS', None):
        dirs.insert(0, os.path.join(sys._MEIPASS, 'bin'))         # dentro del .exe / .app
    for d in dirs:
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return shutil.which(name)


def _run(args):
    flags = 0x08000000 if sys.platform == 'win32' else 0      # sin ventana de consola
    return subprocess.run(args, capture_output=True, text=True, errors='replace',
                          creationflags=flags)


def available():
    return find() is not None


def verify(path, log=print, seconds=None):
    """Decodifica el vídeo (entero, o sus primeros segundos) y devuelve la lista de errores;
    None si no hay ffmpeg."""
    exe = find()
    if not exe:
        log('(ffmpeg no disponible: no se verifica la decodificación)')
        return None
    log('Verificando con ffmpeg (decodificando %s)...' % (
        'los primeros %d s' % seconds if seconds else 'todo el clip'))
    limit = ['-t', str(seconds)] if seconds else []
    r = _run([exe, '-v', 'error'] + limit + ['-i', path, '-map', '0:v:0', '-f', 'null', '-'])
    errors = [l for l in r.stderr.splitlines() if l.strip()]
    if r.returncode == 0 and not errors:
        log('  verificación OK: se decodifica sin errores.')
    else:
        log('  %d mensajes de error al decodificar. Primeros:' % len(errors))
        for l in errors[:8]:
            log('    ' + l)
    return errors


def frame(path, out_jpg, at=1.0, width=480):
    """Guarda un fotograma del clip como JPG (vista previa). True si se ha creado."""
    exe = find()
    if not exe:
        return False
    r = _run([exe, '-y', '-v', 'error', '-ss', str(at), '-i', path, '-frames:v', '1',
              '-vf', 'scale=%d:-2' % width, '-q:v', '4', out_jpg])
    if (r.returncode != 0 or not os.path.exists(out_jpg)) and at:
        return frame(path, out_jpg, 0, width)                     # clip más corto que «at»
    return r.returncode == 0 and os.path.exists(out_jpg)


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
