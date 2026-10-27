"""Interfaz de escritorio (Qt / PySide6) para Windows y macOS."""
import os
import sys
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, analyze, devices, worker
from .events import EventReader

ACCENT = '#f0a43a'

QSS = """
* { font-size: 13px; }
QMainWindow, QWidget#page { background: #15171c; color: #e6e6e6; }
QWidget { color: #e6e6e6; }
QListWidget#nav { background: #101216; border: none; padding-top: 12px; outline: 0; }
QListWidget#nav::item { padding: 12px 18px; margin: 2px 8px; border-radius: 8px; color: #b9bcc4; }
QListWidget#nav::item:selected { background: #262a33; color: #ffffff; }
QListWidget#nav::item:hover:!selected { background: #1b1e25; }
QLabel#title { font-size: 22px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { color: #9aa0ab; }
QLabel#step { font-size: 14px; font-weight: 600; color: %(accent)s; margin-top: 10px; }
QLabel#hint { color: #8b919c; font-size: 12px; }
QFrame#card { background: #1d2027; border: 1px solid #2a2e37; border-radius: 10px; }
QListWidget#drives { background: #1d2027; border: 1px solid #2a2e37; border-radius: 10px; padding: 6px; outline: 0; }
QListWidget#drives::item { padding: 12px; border-radius: 8px; margin: 2px; }
QListWidget#drives::item:selected { background: #2d2516; border: 1px solid %(accent)s; color: #ffffff; }
QLineEdit { background: #1d2027; border: 1px solid #2f343e; border-radius: 6px; padding: 7px 9px; }
QLineEdit:focus { border-color: %(accent)s; }
QPushButton { background: #262a33; border: 1px solid #343944; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #2e333d; }
QPushButton:disabled { color: #6b707a; }
QPushButton#primary { background: %(accent)s; color: #1a1306; border: none; font-weight: 600; padding: 10px 22px; }
QPushButton#primary:hover { background: #f6b85c; }
QPushButton#primary:disabled { background: #5a4a30; color: #2a2112; }
QPushButton#danger { background: #4a2326; border-color: #6b2f33; }
QProgressBar { background: #1d2027; border: 1px solid #2a2e37; border-radius: 6px; height: 14px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 5px; }
QTableWidget { background: #1a1d23; alternate-background-color: #1d2027; border: 1px solid #2a2e37; border-radius: 8px; gridline-color: #262a33; }
QHeaderView::section { background: #1d2027; color: #9aa0ab; border: none; border-bottom: 1px solid #2a2e37; padding: 6px; }
QTableWidget::item:selected { background: #2d2516; color: #ffffff; }
QPlainTextEdit { background: #101216; border: 1px solid #2a2e37; border-radius: 8px; font-family: Consolas, Menlo, monospace; font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QToolButton { border: none; color: #9aa0ab; }
""" % {'accent': ACCENT}


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%.0f %s' if unit in ('B', 'KB') else '%.1f %s') % (n, unit)
        n /= 1024.0


def human_time(s):
    if s is None:
        return '—'
    if s < 60:
        return ('%.1f s' % s).replace('.0 s', ' s')
    s = int(round(s))
    if s >= 3600:
        return '%d h %02d min' % (s // 3600, s % 3600 // 60)
    if s >= 60:
        return '%d min %02d s' % (s // 60, s % 60)
    return '%d s' % s


def default_output():
    base = os.path.join(os.path.expanduser('~'), 'Documents', 'MD55 Rescue')
    return os.path.join(base, time.strftime('Rescate_%Y-%m-%d_%H%M'))


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def asset(name):
    """Ruta de un recurso, tanto desde el código como dentro del .exe / .app."""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'assets', name)


def logo_pixmap(height, color=ACCENT):
    """Logo MD55 teñido de un color, a la altura indicada (en píxeles)."""
    src = QPixmap(asset('logo.png'))
    if src.isNull():
        return QPixmap()
    src = src.scaledToHeight(height, Qt.SmoothTransformation)
    pm = QPixmap(src.size())
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), QColor(color))
    p.end()
    return pm


def app_icon(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setBrush(QColor('#15171c'))
    p.setPen(Qt.NoPen)
    m = size // 32
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, size * 0.2, size * 0.2)
    logo = logo_pixmap(int(size * 0.68))
    if not logo.isNull():
        p.drawPixmap((size - logo.width()) // 2, (size - logo.height()) // 2, logo)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- trabajos en segundo plano

class Job(QObject):
    """Proceso del motor (quizá con permisos de Administrador) leído por eventos."""
    log = Signal(str)
    progress = Signal(float)
    clip = Signal(dict)
    finished = Signal(bool, str)

    def __init__(self, args, elevate=False, parent=None):
        super().__init__(parent)
        self.args = args
        self.elevate = elevate
        self.reader = None
        self.proc = None
        self.started_events = False
        self.done = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)

    def start(self):
        path, self.proc = worker.start(self.args, self.elevate)
        self.reader = EventReader(path)
        self.timer.start(250)

    def stop(self):
        if self.reader and not self.done:
            self.reader.request_stop()

    def _poll(self):
        for ev in self.reader.poll():
            self.started_events = True
            t = ev.get('t')
            if t == 'log':
                self.log.emit(ev['msg'])
            elif t == 'progress':
                self.progress.emit(ev['p'])
            elif t == 'clip':
                self.clip.emit(ev)
            elif t == 'done':
                self._finish(ev.get('ok', False), ev.get('msg', ''))
                return
        if self.proc is not None and self.proc.poll() is not None and not self.done:
            # el proceso terminó sin avisar: mostramos su error
            for ev in self.reader.poll():
                if ev.get('t') == 'done':
                    self._finish(ev.get('ok', False), ev.get('msg', ''))
                    return
            err = ''
            try:
                with open(self.reader.path + '.err', encoding='utf-8', errors='replace') as f:
                    err = f.read().strip().splitlines()[-1:] or ['']
                    err = err[0]
            except OSError:
                pass
            self._finish(False, err or 'El proceso terminó inesperadamente.')

    def _finish(self, ok, msg):
        self.done = True
        self.timer.stop()
        self.finished.emit(ok, msg)


class AnalyzeThread(QThread):
    result = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.result.emit(analyze.analyze(self.path))
        except Exception as e:      # noqa: BLE001
            self.result.emit('No se pudo analizar: %s' % e)


# ---------------------------------------------------------------- utilidades de interfaz

def page_header(title, subtitle):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    t = QLabel(title)
    t.setObjectName('title')
    s = QLabel(subtitle)
    s.setObjectName('subtitle')
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w


def step_label(text):
    lab = QLabel(text)
    lab.setObjectName('step')
    return lab


def hint(text):
    lab = QLabel(text)
    lab.setObjectName('hint')
    lab.setWordWrap(True)
    return lab


class PathRow(QWidget):
    def __init__(self, placeholder, mode='dir', filter_='', parent=None):
        super().__init__(parent)
        self.mode = mode
        self.filter = filter_
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        btn = QPushButton('Elegir…')
        btn.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def browse(self):
        if self.mode == 'dir':
            p = QFileDialog.getExistingDirectory(self, 'Elegir carpeta', self.edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(self, 'Elegir archivo', self.edit.text(), self.filter)
        if p:
            self.edit.setText(os.path.normpath(p))

    def text(self):
        return self.edit.text().strip()

    def setText(self, t):
        self.edit.setText(t)


CLIP_COLUMNS = ['Estado', 'Archivo', 'Duración', 'Códec', 'Perfil', 'Fecha', 'Tamaño']


def clip_table():
    t = QTableWidget(0, len(CLIP_COLUMNS))
    t.setHorizontalHeaderLabels(CLIP_COLUMNS)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(1, QHeaderView.Stretch)
    t.cellDoubleClicked.connect(lambda r, _c: open_path(t.item(r, 1).data(Qt.UserRole)))
    t.setToolTip('Doble clic para abrir el clip')
    return t


def add_clip_row(table, ev):
    if ev.get('identico_a'):
        state, color = '✔ Idéntico a ' + ev['identico_a'], '#7bd88f'
    elif ev.get('complete'):
        state, color = 'Recuperado', '#7bd88f'
    else:
        state, color = 'Incompleto', '#f0c05a'
    values = [state, os.path.basename(ev.get('path', '')), human_time(ev.get('duracion')),
              ev.get('codec') or '—', ev.get('perfil') or '—', ev.get('fecha') or '—',
              human_size(ev.get('size') or 0)]
    r = table.rowCount()
    table.insertRow(r)
    for c, v in enumerate(values):
        it = QTableWidgetItem(v)
        if c == 0:
            it.setForeground(QColor(color))
        if c == 1:
            it.setData(Qt.UserRole, ev.get('path'))
            it.setToolTip('%s\n%s' % (ev.get('path'), ev.get('note', '')))
        table.setItem(r, c, it)
    table.scrollToBottom()


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton('▸ Ver registro detallado')
        self.toggle.setCheckable(True)
        self.toggle.setFlat(True)
        self.toggle.setStyleSheet('text-align: left; border: none; background: transparent; '
                                  'color: #9aa0ab; padding: 2px 0;')
        self.toggle.toggled.connect(self._toggled)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setVisible(False)
        self.text.setMinimumHeight(140)
        lay.addWidget(self.toggle, 0, Qt.AlignLeft)
        lay.addWidget(self.text)

    def _toggled(self, on):
        self.text.setVisible(on)
        self.toggle.setText(('▾ Ocultar' if on else '▸ Ver') + ' registro detallado')

    def append(self, msg):
        self.text.appendPlainText(msg)

    def clear(self):
        self.text.clear()


# ---------------------------------------------------------------- páginas

class RescuePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        self.job = None
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 26, 32, 22)
        outer.addWidget(self.stack)
        self.stack.addWidget(self._setup_view())
        self.stack.addWidget(self._run_view())
        self.refresh_drives()

    # ---- paso 1: configuración
    def _setup_view(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(page_header('Rescatar clips de una tarjeta',
                                  'Recupera vídeos borrados o perdidos tras formatear la tarjeta. '
                                  'Solo se lee la tarjeta: nunca se escribe en ella.'))
        top = QHBoxLayout()
        top.addWidget(step_label('1. Elige la tarjeta'))
        top.addStretch(1)
        refresh = QPushButton('Actualizar')
        refresh.clicked.connect(self.refresh_drives)
        img = QPushButton('Usar imagen de disco…')
        img.clicked.connect(self.pick_image)
        top.addWidget(refresh)
        top.addWidget(img)
        lay.addLayout(top)
        self.drives = QListWidget()
        self.drives.setObjectName('drives')
        self.drives.setMinimumHeight(150)
        self.drives.itemSelectionChanged.connect(self._validate)
        lay.addWidget(self.drives)
        lay.addWidget(hint('Conecta la tarjeta con un lector o la cámara en modo USB «Mass Storage». '
                           'Si el sistema ofrece formatearla, pulsa Cancelar.'))

        lay.addWidget(step_label('2. Dónde guardar los clips recuperados'))
        self.out = PathRow('Carpeta en otro disco')
        self.out.setText(default_output())
        self.out.edit.textChanged.connect(self._validate)
        lay.addWidget(self.out)
        lay.addWidget(hint('Debe estar en otro disco, nunca en la tarjeta que se rescata.'))

        self.adv_toggle = QToolButton()
        self.adv_toggle.setText('▸ Opciones avanzadas')
        self.adv_toggle.setCheckable(True)
        lay.addWidget(self.adv_toggle, 0, Qt.AlignLeft)
        adv = QFrame()
        adv.setObjectName('card')
        g = QGridLayout(adv)
        g.setContentsMargins(14, 12, 14, 12)
        self.ref = PathRow('(opcional) carpeta con clips sanos de la misma cámara')
        self.cmp = PathRow('(opcional) carpeta con copias de los clips para verificar')
        g.addWidget(QLabel('Referencias para reparar incompletos'), 0, 0)
        g.addWidget(self.ref, 0, 1)
        g.addWidget(QLabel('Verificar contra carpeta'), 1, 0)
        g.addWidget(self.cmp, 1, 1)
        g.addWidget(hint('Con «Verificar», la búsqueda se detiene al encontrar todos esos clips y '
                         'comprueba que los recuperados son idénticos byte a byte (SHA-256).'), 2, 0, 1, 2)
        adv.setVisible(False)
        self.adv_toggle.toggled.connect(lambda on: (adv.setVisible(on), self.adv_toggle.setText(
            ('▾' if on else '▸') + ' Opciones avanzadas')))
        lay.addWidget(adv)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.go = QPushButton('Buscar clips')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        bottom.addWidget(self.go)
        lay.addLayout(bottom)
        return w

    def refresh_drives(self):
        self.drives.clear()
        for d in devices.list_drives():
            if d['system']:
                continue
            label = '%s   %s   ·   %s   ·   %s' % (d['id'], d['name'], d['fs'] or '—',
                                                  human_size(d['size']) if d['size'] else '—')
            it = QListWidgetItem(self.style().standardIcon(
                self.style().StandardPixmap.SP_DriveFDIcon if d['removable']
                else self.style().StandardPixmap.SP_DriveHDIcon), label)
            it.setData(Qt.UserRole, d)
            self.drives.addItem(it)
        if self.drives.count() == 0:
            it = QListWidgetItem('No se detecta ninguna tarjeta. Conéctala y pulsa «Actualizar».')
            it.setFlags(Qt.NoItemFlags)
            self.drives.addItem(it)
        self._validate()

    def pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Imagen de la tarjeta', '',
                                           'Imágenes de disco (*.img *.dd *.bin *.dmg);;Todos (*)')
        if not p:
            return
        p = os.path.normpath(p)
        d = {'id': p, 'name': 'Imagen de disco', 'fs': '', 'size': os.path.getsize(p),
             'removable': False, 'system': False, 'image': True}
        it = QListWidgetItem(self.style().standardIcon(self.style().StandardPixmap.SP_FileIcon),
                             '%s   ·   %s' % (os.path.basename(p), human_size(d['size'])))
        it.setData(Qt.UserRole, d)
        self.drives.addItem(it)
        self.drives.setCurrentItem(it)

    def _selected(self):
        it = self.drives.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _validate(self):
        self.go.setEnabled(bool(self._selected()) and bool(self.out.text()))

    # ---- paso 2: búsqueda y resultados
    def _run_view(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.run_title = QLabel('Buscando clips…')
        self.run_title.setObjectName('title')
        self.run_sub = QLabel('')
        self.run_sub.setObjectName('subtitle')
        lay.addWidget(self.run_title)
        lay.addWidget(self.run_sub)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        self.stats = QLabel('')
        self.stats.setObjectName('hint')
        lay.addWidget(self.stats)
        self.table = clip_table()
        lay.addWidget(self.table, 1)
        self.log = LogPanel()
        lay.addWidget(self.log)
        row = QHBoxLayout()
        self.stop_btn = QPushButton('Detener')
        self.stop_btn.setObjectName('danger')
        self.stop_btn.clicked.connect(self.stop)
        self.open_btn = QPushButton('Abrir carpeta')
        self.open_btn.clicked.connect(lambda: open_path(self.out_dir))
        self.new_btn = QPushButton('Nueva búsqueda')
        self.new_btn.setObjectName('primary')
        self.new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        row.addWidget(self.open_btn)
        row.addWidget(self.new_btn)
        lay.addLayout(row)
        return w

    def start(self):
        d = self._selected()
        self.out_dir = self.out.text()
        if not d or not self.out_dir:
            return
        try:
            os.makedirs(self.out_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, 'Carpeta de salida', str(e))
            return
        args = ['rescatar', d['id'], '-o', self.out_dir]
        if self.ref.text():
            args += ['--referencia', self.ref.text()]
        if self.cmp.text():
            args += ['--comparar', self.cmp.text()]
        elevate = devices.is_device(d['id'])
        self.source_size = d.get('size') or 0
        self.table.setRowCount(0)
        self.log.clear()
        self.found = 0
        self.bar.setValue(0)
        self.run_title.setText('Buscando clips en %s' % (d['id'] if not d.get('image')
                                                        else os.path.basename(d['id'])))
        self.run_sub.setText('Esperando permiso de Administrador…' if elevate and not devices.is_admin()
                             else 'Leyendo…')
        self.stats.setText('')
        self.stop_btn.setEnabled(True)
        self.open_btn.setEnabled(True)
        self.new_btn.setVisible(False)
        self.stack.setCurrentIndex(1)
        self.t0 = None
        self.job = Job(args, elevate, self)
        self.job.log.connect(self._on_log)
        self.job.progress.connect(self._on_progress)
        self.job.clip.connect(self._on_clip)
        self.job.finished.connect(self._on_finished)
        try:
            self.job.start()
        except PermissionError as e:
            self._on_finished(False, str(e))

    def stop(self):
        if self.job:
            self.job.stop()
            self.stop_btn.setEnabled(False)
            self.run_sub.setText('Deteniendo…')

    def _on_log(self, msg):
        if self.t0 is None:
            self.t0 = time.monotonic()
            self.run_sub.setText('Leyendo la tarjeta. Puedes seguir usando el ordenador.')
        self.log.append(msg)

    def _on_progress(self, p):
        if self.t0 is None:
            self.t0 = time.monotonic()
        self.bar.setValue(int(p * 1000))
        el = time.monotonic() - self.t0
        parts = ['%.0f %%' % (p * 100)]
        if self.source_size:
            parts.append('%s de %s' % (human_size(self.source_size * p), human_size(self.source_size)))
            if el > 3 and p > 0.002:
                parts.append('%s/s' % human_size(self.source_size * p / el))
                parts.append('quedan ~%s' % human_time(el / p * (1 - p)))
        parts.append('%d clips recuperados' % self.found)
        self.stats.setText('   ·   '.join(parts))

    def _on_clip(self, ev):
        self.found += 1
        add_clip_row(self.table, ev)

    def _on_finished(self, ok, msg):
        self.stop_btn.setEnabled(False)
        self.new_btn.setVisible(True)
        if ok:
            self.bar.setValue(1000)
            self.run_title.setText('Terminado: %d clips recuperados' % self.found)
            self.run_sub.setText('Doble clic en un clip para abrirlo. Los archivos están en %s' % self.out_dir)
        else:
            self.run_title.setText('No se pudo completar')
            self.run_sub.setText(msg)
        self._on_progress(self.bar.value() / 1000.0)


class RepairPage(QWidget):
    COLS = ['Estado', 'Clip dañado', 'Duración', 'Perfil', 'Resultado']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 22)
        lay.addWidget(page_header('Reparar clips interrumpidos',
                                  'Para clips que no abren porque la grabación se cortó (batería, '
                                  'tarjeta extraída, apagado). Reconstruye el índice sin recodificar: '
                                  'S-Log3, S-Cinetone y 10 bits se conservan intactos.'))
        top = QHBoxLayout()
        top.addWidget(step_label('1. Clips dañados'))
        top.addStretch(1)
        add = QPushButton('Añadir clips…')
        add.clicked.connect(self.add_files)
        clear = QPushButton('Quitar todos')
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        top.addWidget(add)
        top.addWidget(clear)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_result)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint('También puedes arrastrar los archivos aquí.'))

        lay.addWidget(step_label('2. Clip(s) de referencia'))
        self.ref = PathRow('Carpeta con clips sanos (se elige el compatible) o un clip concreto')
        btn_file = QPushButton('Un clip…')
        btn_file.clicked.connect(self._pick_ref_file)
        self.ref.layout().addWidget(btn_file)
        lay.addWidget(self.ref)
        lay.addWidget(hint('Clips sanos de la misma cámara con el mismo formato, resolución, fps y '
                           'bitrate. Basta con 5–30 s. Si das una carpeta, se elige automáticamente.'))
        lay.addWidget(step_label('3. Dónde guardar'))
        self.out = PathRow('(por defecto, junto a cada clip dañado)')
        lay.addWidget(self.out)
        self.log = LogPanel()
        lay.addWidget(self.log)
        row = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        row.addWidget(self.bar, 1)
        self.go = QPushButton('Reparar')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.queue = []
        self.job = None

    def _pick_ref_file(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Clip de referencia', '', 'Vídeo (*.mp4 *.MP4 *.mov)')
        if p:
            self.ref.setText(os.path.normpath(p))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self._add([u.toLocalFile() for u in e.mimeData().urls()])

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Clips dañados', '', 'Vídeo (*.mp4 *.MP4 *.mov)')
        self._add(files)

    def _add(self, files):
        for f in files:
            if not os.path.isfile(f):
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = ['Pendiente', os.path.basename(f), '—', '—', '']
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setData(Qt.UserRole, os.path.normpath(f))
                    it.setToolTip(f)
                self.table.setItem(r, c, it)

    def _open_result(self, r, _c):
        it = self.table.item(r, 4)
        path = it.data(Qt.UserRole) if it else None
        open_path(path or self.table.item(r, 1).data(Qt.UserRole))

    def start(self):
        if not self.ref.text():
            QMessageBox.information(self, 'Referencia', 'Indica una carpeta con clips sanos o un clip de referencia.')
            return
        self.queue = [r for r in range(self.table.rowCount())
                      if self.table.item(r, 0).text() in ('Pendiente', 'Error')]
        if not self.queue:
            return
        self.go.setEnabled(False)
        self.log.clear()
        self.done_count = 0
        self.total = len(self.queue)
        self._next()

    def _next(self):
        if not self.queue:
            self.go.setEnabled(True)
            self.bar.setValue(1000)
            return
        self.row = self.queue.pop(0)
        src = self.table.item(self.row, 1).data(Qt.UserRole)
        out_dir = self.out.text() or os.path.dirname(src)
        out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + '_REPARADO.MP4')
        self.table.item(self.row, 0).setText('Reparando…')
        self.log.append('── %s' % src)
        self.job = Job(['reparar', src, '--referencia', self.ref.text(), '-o', out], False, self)
        self.job.log.connect(self.log.append)
        self.job.progress.connect(lambda p: self.bar.setValue(
            int((self.done_count + p) / self.total * 1000)))
        self.job.clip.connect(self._on_clip)
        self.job.finished.connect(self._on_finished)
        self.job.start()

    def _on_clip(self, ev):
        self.table.item(self.row, 2).setText(human_time(ev.get('duracion')))
        self.table.item(self.row, 3).setText(ev.get('perfil') or '—')
        res = self.table.item(self.row, 4)
        res.setText(os.path.basename(ev['path']))
        res.setData(Qt.UserRole, ev['path'])
        res.setToolTip(ev['path'])

    def _on_finished(self, ok, msg):
        it = self.table.item(self.row, 0)
        it.setText('Reparado' if ok else 'Error')
        it.setForeground(QColor('#7bd88f' if ok else '#ff7b72'))
        if not ok:
            self.table.item(self.row, 4).setText(msg)
            self.log.append('ERROR: ' + msg)
        self.done_count += 1
        self._next()


class AnalyzePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 22)
        lay.addWidget(page_header('Analizar un clip',
                                  'Estructura del archivo, si le falta el índice, códec, fps y perfil '
                                  'de imagen (S-Log3, S-Cinetone, HLG…) leído de los metadatos de Sony.'))
        row = QHBoxLayout()
        self.path = PathRow('Clip MP4 / MXF', mode='file', filter_='Vídeo (*.mp4 *.MP4 *.mxf *.MXF *.mov)')
        row.addWidget(self.path, 1)
        go = QPushButton('Analizar')
        go.setObjectName('primary')
        go.clicked.connect(self.run)
        row.addWidget(go)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText('Arrastra aquí un clip o elígelo arriba.')
        lay.addWidget(self.text, 1)
        self.thread = None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.path.setText(os.path.normpath(urls[0].toLocalFile()))
            self.run()

    def run(self):
        if not os.path.isfile(self.path.text()):
            return
        self.text.setPlainText('Analizando…')
        self.thread = AnalyzeThread(self.path.text(), self)
        self.thread.result.connect(self.text.setPlainText)
        self.thread.start()


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 22)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(96))
        lay.addWidget(mark)
        lay.addWidget(page_header('MD55 Rescue %s' % __version__,
                                  'Recuperación de vídeo para cámaras Sony: XAVC S, XAVC HS, XAVC S-I '
                                  'y XAVC en MXF. Copia los datos originales sin recodificar.'))
        txt = QLabel(
            '<p><b>Buenas prácticas</b></p>'
            '<ul>'
            '<li>Deja de grabar en la tarjeta afectada en cuanto notes el problema.</li>'
            '<li>Guarda siempre lo recuperado en otro disco.</li>'
            '<li>Si el sistema ofrece formatear la tarjeta, pulsa Cancelar.</li>'
            '<li>Para reparar, graba un clip de referencia con la misma cámara y configuración.</li>'
            '</ul>'
            '<p><b>Límites</b>: un formateo completo (sobrescritura) no es recuperable; los clips muy '
            'fragmentados pueden salir incompletos.</p>')
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.RichText)
        lay.addWidget(txt)
        lay.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('MD55 Rescue')
        self.setWindowIcon(app_icon())
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setFixedWidth(230)
        side.setStyleSheet('background: #101216;')
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 22, 0, 16)
        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 12, 10)
        brand.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(34))
        name = QLabel('MD55 Rescue')
        name.setStyleSheet('font-size: 18px; font-weight: 700; color: #ffffff;')
        brand.addWidget(mark)
        brand.addWidget(name)
        brand.addStretch(1)
        sl.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        for name in ('Rescatar tarjeta', 'Reparar clip', 'Analizar clip', 'Acerca de'):
            self.nav.addItem(name)
        sl.addWidget(self.nav, 1)
        adm = QLabel('  Permisos: Administrador' if devices.is_admin() else
                     '  Se pedirá permiso al leer la tarjeta')
        adm.setObjectName('hint')
        adm.setStyleSheet('padding-left: 14px;')
        sl.addWidget(adm)
        self.pages = QStackedWidget()
        for p in (RescuePage(), RepairPage(), AnalyzePage(), AboutPage()):
            self.pages.addWidget(p)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        lay.addWidget(side)
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(central)


def main():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MD55Rescue')
        except (OSError, AttributeError):
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('MD55 Rescue')
    app.setStyle('Fusion')
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    return app.exec()
