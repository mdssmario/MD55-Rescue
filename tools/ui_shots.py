"""Capturas offscreen de cada página para revisar el aspecto (herramienta de desarrollo)."""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xavc_rescue import uikit  # noqa: E402
from xavc_rescue.qtapp import MainWindow  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_shots')
os.makedirs(OUT, exist_ok=True)

app = QApplication.instance() or QApplication(sys.argv)
for name in ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf'):          # sin fuentes del sistema
    path = os.path.join(os.environ.get('MD55_FONTS', '/tmp/syslibs/usr/share/fonts/truetype/ttf-dejavu'), name)
    if os.path.exists(path):
        QFontDatabase.addApplicationFont(path)
app.setFont(QFont('Sans Serif', 10))
app.setStyle('Fusion')
app.setStyleSheet(uikit.stylesheet())
w = MainWindow()
w.resize(1200, 820)
w.show()
app.processEvents()


def shot(name):
    app.processEvents()
    w.grab().save(os.path.join(OUT, name + '.png'))
    print('ok', name)


pages = w.pages
rescue = pages.widget(0)

shot('01_rescate_vacio')

rescue.drives.add({'id': 'E:', 'name': 'Sony SF-M 128GB', 'fs': 'exFAT', 'size': 128000000000,
                   'used': 42000000000, 'removable': True, 'system': False})
shot('02_rescate_tarjeta')

# vista de exploración / selección con filas de ejemplo
rescue.stack.setCurrentIndex(1)
rescue.out_dir = os.path.join(OUT, 'rescate')
rescue.source_name = 'E:'
rescue.found = {'video': 0, 'photo': 0, 'thumb': 0, 'video_bytes': 0}
rescue.days = {}
rescue.thumbs = []
rescue.saved_v = rescue.saved_p = 0
rescue.table.setSortingEnabled(False)
sample = [
    ('C0001.MP4', 'ILCE-9M3', 12.4, 'XAVC S-I 4K 59.94p', 'S-Log3/S-Gamut3.Cine', 168000000, True, 'video'),
    ('C0002.MP4', 'ILCE-9M3', 31.0, 'XAVC HS 4K 59.94p', 'S-Cinetone', 490000000, False, 'video'),
    ('DSC01234.ARW', 'ILCE-9M3', None, 'ARW 14 bits', None, 62000000, True, 'photo'),
]
for i, (name, cam, dur, codec, perf, size, complete, kind) in enumerate(sample):
    rescue.found['video' if kind == 'video' else 'photo'] += 1
    rescue._on_event({'t': 'item', 'id': i, 'name': name, 'camara': cam, 'duracion': dur,
                      'codec': codec, 'perfil': perf, 'size': size, 'complete': complete,
                      'kind': kind, 'fecha': '2026-10-29 18:%02d:00' % (10 + i),
                      'fecha_seg': '2026-10-29 18:%02d:00' % (10 + i)})
rescue._on_event({'t': 'scan_done'})
rescue.table.selectRow(0)
shot('03_rescate_lista')

rescue.f_video.setChecked(False)
rescue.f_photo.setChecked(False)
shot('03b_rescate_sin_filtros')
rescue.reset_filters()

for i in range(1, 5):
    w.nav.setCurrentRow(i)
    shot('%02d_%s' % (i + 3, MainWindow.PAGES[i][0]))
print('done')
