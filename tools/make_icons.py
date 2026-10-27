"""Genera assets/icon.png, icon.ico (Windows) e icon.icns (macOS) a partir del icono de la app.

Solo crea los que faltan (usa --force para regenerarlos). Debe ejecutarse con pantalla
normal, no con QT_QPA_PLATFORM=offscreen: en ese modo no hay fuentes y el texto del
icono sale como cuadros vacíos.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

out = os.path.join(HERE, 'assets')
wanted = ['icon.png', 'icon.ico'] + (['icon.icns'] if sys.platform == 'darwin' else [])
missing = [n for n in wanted if '--force' in sys.argv or not os.path.exists(os.path.join(out, n))]
if not missing:
    print('iconos ya generados en', out)
    sys.exit(0)

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
from xavc_rescue.qtapp import app_icon  # noqa: E402

os.makedirs(out, exist_ok=True)
pm = app_icon().pixmap(256, 256)
for n in missing:
    img = pm.toImage().scaled(512, 512) if n.endswith('.icns') else pm
    img.save(os.path.join(out, n))
print('generados:', ', '.join(missing))
