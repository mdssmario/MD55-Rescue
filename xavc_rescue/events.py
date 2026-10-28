"""Canal de eventos entre el proceso que trabaja (a veces con permisos de Administrador)
y la interfaz: un archivo de líneas JSON. Para detenerlo basta con crear '<archivo>.stop',
así la interfaz puede cancelar sin necesitar permisos sobre el proceso elevado."""
import json
import os
import time


class EventFile:
    def __init__(self, path):
        self.path = path
        self.stop_path = path + '.stop'
        self.f = open(path, 'a', encoding='utf-8', buffering=1)
        self._last_p = -1.0
        self._last_t = 0.0

    def _write(self, obj):
        self.f.write(json.dumps(obj, ensure_ascii=False) + '\n')

    def log(self, msg):
        self._write({'t': 'log', 'msg': str(msg)})

    def progress(self, p):
        now = time.monotonic()
        if p >= 1 or p - self._last_p >= 0.001 or now - self._last_t > 2:
            self._last_p, self._last_t = p, now
            self._write({'t': 'progress', 'p': round(p, 5)})

    def clip(self, info):
        self._write(dict(info, t='clip'))

    def result(self, info):
        self._write({'t': 'result', 'data': info})

    def event(self, event_type, /, **data):
        self._write(dict(data, t=event_type))

    def wait_selection(self, timeout=12 * 3600):
        """Espera a que la interfaz escriba '<archivo>.seleccion' ({"ids": [...], opciones}).
        Devuelve ese diccionario, o None si se pide parar o se agota el tiempo."""
        sel_path = self.path + '.seleccion'
        try:
            os.remove(self.stop_path)       # el «Detener» de la exploración ya se atendió
        except OSError:
            pass
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.stop_requested():
                return None
            if os.path.exists(sel_path):
                try:
                    with open(sel_path, encoding='utf-8') as f:
                        return json.load(f)
                except (OSError, ValueError):
                    pass            # aún se está escribiendo
            time.sleep(0.4)
        return None

    def stop_requested(self):
        return os.path.exists(self.stop_path)

    def done(self, ok=True, msg=''):
        self._write({'t': 'done', 'ok': ok, 'msg': msg})
        self.f.close()


class EventReader:
    """Lee incrementalmente el archivo de eventos (lo usa la interfaz)."""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.buf = ''

    def poll(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except FileNotFoundError:
            return []
        self.buf += data
        *lines, self.buf = self.buf.split('\n')
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def request_stop(self):
        with open(self.path + '.stop', 'w') as f:
            f.write('stop')

    def send_selection(self, ids, **options):
        tmp = self.path + '.seleccion.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dict(options, ids=list(ids)), f)
        os.replace(tmp, self.path + '.seleccion')
