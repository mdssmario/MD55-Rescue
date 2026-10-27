"""Recorta assets/logo_original.webp (logo con fondo transparente) a su contorno y lo
guarda como assets/logo.png. La interfaz lo tiñe del color que necesite (naranja sobre
el fondo oscuro), así que da igual que el original sea negro o blanco."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

app = QGuiApplication(sys.argv)
src = QImage(os.path.join(HERE, 'assets', 'logo_original.webp'))
if src.isNull():
    sys.exit('No se pudo leer assets/logo_original.webp')
src = src.convertToFormat(QImage.Format_ARGB32)
w, h = src.width(), src.height()
left, top, right, bottom = w, h, 0, 0
for y in range(h):
    for x in range(w):
        if src.pixelColor(x, y).alpha() > 0:
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
out = src.copy(left, top, right - left + 1, bottom - top + 1)
out.save(os.path.join(HERE, 'assets', 'logo.png'))
print('assets/logo.png: %dx%d' % (out.width(), out.height()))
