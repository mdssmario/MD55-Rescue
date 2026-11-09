"""Capturas de cada página y estado para revisar el aspecto (herramienta de desarrollo).

Sin pantalla usa la plataforma «offscreen»; en Windows/macOS se puede forzar la real con
QT_QPA_PLATFORM=windows (o cocoa) para ver las fuentes del sistema. Con MD55_SHOTS_CLIP=<clip.MP4>
la página Analizar muestra un análisis real.
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFontDatabase  # noqa: E402
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
app.setFont(uikit.app_font())
app.setStyle('Fusion')
app.setStyleSheet(uikit.stylesheet())
w = MainWindow()
w.resize(1280, 860)
w.show()
app.processEvents()


def shot(name):
    for _ in range(3):
        app.processEvents()
    w.grab().save(os.path.join(OUT, name + '.png'))
    print('ok', name)


pages = w.pages
rescue = pages.widget(0)
for t in list(rescue.drives.tiles):
    t.setParent(None)
rescue.drives.tiles, rescue.drives.current = [], None
rescue.drives.empty.setVisible(True)
shot('01_rescate_vacio')

rescue.drives.empty.setVisible(False)
rescue.drives.add({'id': 'E:', 'name': 'Sony TOUGH 128GB', 'fs': 'exFAT', 'size': 128000000000,
                   'used': 42000000000, 'removable': True, 'system': False})
rescue.drives.add({'id': 'F:', 'name': 'CFexpress A 160GB', 'fs': 'exFAT', 'size': 160000000000,
                   'used': 101000000000, 'removable': True, 'system': False}, select=False)
shot('02_rescate_tarjeta')

# vista de exploración / selección con filas de ejemplo
rescue.stack.setCurrentIndex(1)
rescue.out_dir = os.path.join(OUT, 'rescate')
rescue.source_name = 'E:'
rescue.source_size = 128000000000
rescue.found = {'video': 0, 'photo': 0, 'thumb': 0, 'video_bytes': 0, 'broken': 0}
rescue.days = {}
rescue.thumbs = []
rescue.saved_v = rescue.saved_p = rescue.repaired = rescue.verified = 0
rescue.t0 = None
rescue.table.setSortingEnabled(False)
rescue.run_title.setText('Explorando E:')
rescue.run_sub.setText('Leyendo la tarjeta sin modificar nada.')
rescue._set_mode('scan')
sample = [
    ('2026-10-29_18-10_0001.MP4', 'ILCE-9M3', 12.4, 'XAVC S-I 4K 59.94p', 'S-Log3 · S-Gamut3.Cine', 168000000,
     True, 'mp4'),
    ('2026-10-29_18-11_0002_INCOMPLETO.MP4', 'ILCE-9M3', 31.0, 'XAVC HS 4K 59.94p', 'S-Cinetone',
     490000000, False, 'mp4'),
    ('GRABACION_CORTADA_0003.RSV', 'ILCE-9M3', None, 'XAVC HS (H.265)', None, 640000000, False, 'rsv'),
    ('2026-10-29_18-12_0004.ARW', 'ILCE-9M3', None, 'ARW 24 MP', None, 62000000, True, 'raw'),
    ('2026-10-29_18-13_0005.JPG', 'ILCE-9M3', None, 'JPG 24 MP', None, 9000000, True, 'jpg'),
]
for i, (name, cam, dur, codec, perf, size, complete, kind) in enumerate(sample):
    rescue._on_event({'t': 'item', 'id': i + 1, 'name': name, 'camara': cam, 'duracion': dur,
                      'codec': codec, 'perfil': perf, 'size': size, 'complete': complete,
                      'kind': kind, 'fecha': '2026-10-29 18:%02d' % (10 + i),
                      'fecha_seg': '2026-10-29 18:%02d:00' % (10 + i),
                      'note': 'grabación cortada sin cabecera ni índice (420 fotogramas)' if kind == 'rsv' else ''})
rescue._on_progress(0.37)
shot('03_rescate_explorando')
rescue._on_event({'t': 'scan_done'})
rescue.table.selectRow(2)
shot('04_rescate_eligiendo')

rescue.f_video.setChecked(False)
rescue.f_photo.setChecked(False)
shot('05_rescate_sin_filtros')
rescue.reset_filters()

rescue.extract_count, rescue.extract_bytes = 3, 1300000000
rescue._set_mode('extract')
rescue._on_event({'t': 'phase', 'phase': 'repair', 'count': 1})
shot('06_rescate_reparando')
for i in (1, 3):
    rescue._on_saved({'id': i, 'kind': 'mp4', 'complete': True, 'path': os.path.join(rescue.out_dir, 'x.MP4')})
rescue._on_saved({'id': 3, 'kind': 'rsv', 'complete': True, 'note': 'reparado con referencia', 'verificado': True,
                  'referencia': '2026-10-29_18-10_0001.MP4', 'duracion': 7.0, 'codec': 'XAVC HS 4K 59.94p',
                  'path': os.path.join(rescue.out_dir, 'GRABACION_CORTADA_0003_REPARADO.MP4')})
rescue._on_finished(True, '')
rescue.table.selectRow(0)
shot('07_rescate_terminado')

repair = pages.widget(1)
w.nav.setCurrentRow(1)
shot('08_reparar_vacio')
repair._add([os.path.abspath(__file__), os.path.join(os.path.dirname(OUT), 'build_ffmpeg.sh')])
shot('09_reparar_lista')

analyze = pages.widget(2)
w.nav.setCurrentRow(2)
shot('10_analizar_vacio')
clip = os.environ.get('MD55_SHOTS_CLIP')
if clip and os.path.exists(clip):
    analyze.path.setText(clip)
    analyze.run()
    analyze.thread.wait()
    for _ in range(10):
        app.processEvents()
    shot('11_analizar_resultado')

tools_page = pages.widget(3)
w.nav.setCurrentRow(3)
shot('12_herramientas')
w.nav.setCurrentRow(4)
shot('13_acerca_de')
print('done')
