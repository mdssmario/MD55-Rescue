"""Lanza el motor en un proceso aparte (con permisos de Administrador si hace falta leer
la tarjeta) y devuelve la ruta del archivo de eventos que la interfaz va leyendo."""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from . import devices


def _base_command():
    if getattr(sys, 'frozen', False):          # aplicación empaquetada (.exe / .app)
        return [sys.executable]
    return [sys.executable, '-m', 'xavc_rescue']


def _project_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def temp_dir():
    d = os.path.join(tempfile.gettempdir(), 'xavc_rescue')
    os.makedirs(d, exist_ok=True)
    return d


def new_events_path():
    return os.path.join(temp_dir(), 'eventos_%d_%d.jsonl' % (os.getpid(), int(time.time() * 1000)))


def clean_temp(max_age=2 * 24 * 3600):
    """Borra los eventos y las vistas previas de ejecuciones anteriores.

    Se deja lo reciente por si hay otra copia de la aplicación abierta trabajando.
    """
    d = temp_dir()
    now = time.time()
    for name in os.listdir(d):
        path = os.path.join(d, name)
        try:
            if now - os.path.getmtime(path) < max_age:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass


def start(args, elevate=False):
    """Ejecuta 'xavc_rescue <args> --eventos <archivo>'. Devuelve (archivo, proceso o None)."""
    events = new_events_path()
    open(events, 'w').close()
    cmd = _base_command() + list(args) + ['--eventos', events]
    cwd = _project_dir()
    if elevate and not devices.is_admin():
        if sys.platform == 'win32':
            _start_windows_admin(cmd, cwd)
            return events, None
        if sys.platform == 'darwin':
            return events, _start_mac_admin(cmd, cwd)
    flags = 0x08000000 if sys.platform == 'win32' else 0       # sin ventana de consola
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=open(events + '.err', 'w'), creationflags=flags)
    return events, proc


def _start_windows_admin(cmd, cwd):
    import ctypes
    params = subprocess.list2cmdline(cmd[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', cmd[0], params, cwd, 0)
    if rc <= 32:
        raise PermissionError('Windows no concedió permisos de Administrador (código %d).' % rc)


def _start_mac_admin(cmd, cwd):
    shell = 'cd %s && %s > /dev/null 2>&1 &' % (shlex.quote(cwd), ' '.join(shlex.quote(c) for c in cmd))
    script = 'do shell script "%s" with administrator privileges' % (
        shell.replace('\\', '\\\\').replace('"', '\\"'))
    proc = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise PermissionError('macOS no concedió permisos de administrador.')
    return None
