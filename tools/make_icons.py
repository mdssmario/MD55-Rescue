"""Genera assets/icon.png, icon.ico (Windows) e icon.icns (macOS) a partir del icono de la app."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
from xavc_rescue.qtapp import app_icon  # noqa: E402

out = os.path.join(HERE, 'assets')
os.makedirs(out, exist_ok=True)
pm = app_icon().pixmap(256, 256)
pm.save(os.path.join(out, 'icon.png'))
pm.save(os.path.join(out, 'icon.ico'))
if sys.platform == 'darwin':
    pm.toImage().scaled(512, 512).save(os.path.join(out, 'icon.icns'))
print('iconos en', out)
