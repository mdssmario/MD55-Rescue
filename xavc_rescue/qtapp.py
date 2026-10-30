"""Interfaz de escritorio (Qt / PySide6) para Windows y macOS."""
import datetime
import os
import shutil
import sys
import time

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__, analyze, devices, uikit, worker
from .carve import PHOTO_KINDS
from .events import EventReader
from .uikit import (ACCENT, BAD, DIM, FAINT, MUTED, OK, VIDEO, WARN, WHITE, ActionCard, Disclosure,
                    DriveList, EmptyState, esc, human_size, plural, section, svg_pixmap, tint)

SORT_ROLE = Qt.UserRole + 3
VIDEO_FILTER = 'Vídeo (*.mp4 *.MP4 *.mov *.MOV)'
PREVIEW_W, PREVIEW_H = 200, 112

CAMERA_NAMES = {
    'ILCE-1': 'A1', 'ILCE-1M2': 'A1 II', 'ILCE-9M2': 'A9 II', 'ILCE-9M3': 'A9 III',
    'ILCE-7M3': 'A7 III', 'ILCE-7M4': 'A7 IV', 'ILCE-7SM2': 'A7S II', 'ILCE-7SM3': 'A7S III',
    'ILCE-7RM3': 'A7R III', 'ILCE-7RM4': 'A7R IV', 'ILCE-7RM4A': 'A7R IVA', 'ILCE-7RM5': 'A7R V',
    'ILCE-7C': 'A7C', 'ILCE-7CM2': 'A7C II', 'ILCE-7CR': 'A7CR', 'ILCE-6700': 'A6700',
    'ILME-FX3': 'FX3', 'ILME-FX3A': 'FX3', 'ILME-FX30': 'FX30', 'ILME-FX6': 'FX6', 'ILME-FX6V': 'FX6',
    'ZV-E1': 'ZV-E1', 'ZV-E10M2': 'ZV-E10 II',
}
MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


def camera_name(model):
    return CAMERA_NAMES.get((model or '').strip(), (model or '').strip() or '—')


def day_label(day):
    try:
        y, m, d = day.split('-')
        return '%d %s %s' % (int(d), MONTHS[int(m) - 1], y)
    except (ValueError, IndexError):
        return day


def parse_when(stamp):
    try:
        return datetime.datetime.strptime((stamp or '')[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


class SortItem(QTableWidgetItem):
    """Celda que ordena por un valor propio (tamaño, duración, fecha) si lo tiene."""

    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


def human_time(s):
    if s is None:
        return '—'
    if s < 60:
        return ('%.1f s' % s).replace('.0 s', ' s')
    s = int(round(s))
    if s >= 3600:
        return '%d h %02d min' % (s // 3600, s % 3600 // 60)
    return '%d min %02d s' % (s // 60, s % 60)


def settings():
    """Preferencias simples (tamaño de ventana, última carpeta usada)."""
    return QSettings('MD55 Rescue', 'MD55 Rescue')


def default_output():
    """Carpeta propuesta: la última usada (o Documentos) y dentro una con la fecha."""
    base = settings().value('salida/base') or os.path.join(os.path.expanduser('~'), 'Documents',
                                                           'MD55 Rescue')
    return os.path.join(str(base), time.strftime('Rescate_%Y-%m-%d_%H%M'))


def open_path(path):
    if path:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def asset(name):
    """Ruta de un recurso, tanto desde el código como dentro del .exe / .app."""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'assets', name)


def logo_pixmap(height, color=ACCENT, ratio=2):
    """Logo MD55 teñido de un color, a la altura indicada (en píxeles)."""
    src = QPixmap(asset('logo.png'))
    if src.isNull():
        return QPixmap()
    src = src.scaledToHeight(height * ratio, Qt.SmoothTransformation)
    src.setDevicePixelRatio(ratio)
    return tint(src, color)


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
    logo = logo_pixmap(int(size * 0.68), ratio=1)
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
    result = Signal(dict)
    event = Signal(dict)          # otros eventos: item, scan_done, phase…
    finished = Signal(bool, str)

    def __init__(self, args, elevate=False, parent=None):
        super().__init__(parent)
        self.args = args
        self.elevate = elevate
        self.reader = None
        self.proc = None
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

    def send_selection(self, ids, **options):
        if self.reader and not self.done:
            self.reader.send_selection(ids, **options)

    def _dispatch(self, events):
        """Reparte los eventos leídos. True si ha llegado el final."""
        for ev in events:
            t = ev.get('t')
            if t == 'log':
                self.log.emit(ev['msg'])
            elif t == 'progress':
                self.progress.emit(ev['p'])
            elif t == 'clip':
                self.clip.emit(ev)
            elif t == 'result':
                self.result.emit(ev.get('data') or {})
            elif t == 'done':
                self._finish(ev.get('ok', False), ev.get('msg', ''))
                return True
            else:
                self.event.emit(ev)
        return False

    def _poll(self):
        if self._dispatch(self.reader.poll()):
            return
        if self.proc is not None and self.proc.poll() is not None and not self.done:
            # el proceso terminó: atender todo lo que escribió antes de salir
            if self._dispatch(self.reader.poll()):
                return
            err = ''
            try:
                with open(self.reader.path + '.err', encoding='utf-8', errors='replace') as f:
                    lines = f.read().strip().splitlines()
                    err = lines[-1] if lines else ''
            except OSError:
                pass
            self._finish(False, err or 'El proceso terminó inesperadamente.')

    def _finish(self, ok, msg):
        self.done = True
        self.timer.stop()
        self.finished.emit(ok, msg)

    @property
    def running(self):
        return self.reader is not None and not self.done


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


# ---------------------------------------------------------------- piezas comunes

def page_header(title, subtitle):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(4)
    t = QLabel(title)
    t.setObjectName('title')
    s = QLabel(subtitle)
    s.setObjectName('subtitle')
    s.setWordWrap(True)
    lay.addWidget(t)
    lay.addWidget(s)
    return w


def hint(text):
    lab = QLabel(text)
    lab.setObjectName('hint')
    lab.setWordWrap(True)
    return lab


def data_table(cols):
    """Tabla de solo lectura con el aspecto común de la aplicación."""
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.horizontalHeader().setHighlightSections(False)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(34)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.ElideRight)
    return t


def accept_file_drops(widget, on_files):
    """Permite soltar archivos sobre un widget; on_files recibe la lista de rutas."""
    widget.setAcceptDrops(True)

    def enter(e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def drop(e):
        on_files([os.path.normpath(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])

    widget.dragEnterEvent = enter
    widget.dropEvent = drop


def rich(title, *lines):
    """Bloque de texto de las fichas: título en blanco y líneas (texto, color) debajo."""
    out = '<span style="color:%s;font-size:14px;font-weight:600">%s</span>' % (WHITE, title)
    for text, color in lines:
        out += '<br><span style="color:%s">%s</span>' % (color, text) if text else '<br>'
    return out


def card(*widgets, margins=(16, 14, 16, 14), spacing=8):
    f = QFrame()
    f.setObjectName('card')
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return f


def page_scaffold(widget):
    """Página con desplazamiento vertical por si la ventana es pequeña."""
    widget.setObjectName('page')
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    inner.setObjectName('page')
    scroll.setWidget(inner)
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(36, 28, 36, 24)
    lay.setSpacing(14)
    return lay


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


class LogPanel(QWidget):
    """Registro plegable, con copiar y guardar para adjuntarlo si hay que pedir ayuda."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setMinimumHeight(150)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        self.toggle = Disclosure('Registro detallado', self.text)
        head.addWidget(self.toggle)
        head.addStretch(1)
        self.copy_btn = QPushButton('Copiar')
        self.save_btn = QPushButton('Guardar…')
        for b, tip in ((self.copy_btn, 'Copia el registro al portapapeles'),
                       (self.save_btn, 'Guarda el registro en un archivo de texto')):
            b.setObjectName('link')
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setVisible(False)
            head.addWidget(b)
        lay.addLayout(head)
        lay.addWidget(self.text)
        self.copy_btn.clicked.connect(self.copy)
        self.save_btn.clicked.connect(self.save)
        self.toggle.toggled.connect(self._show_actions)

    def append(self, msg):
        self.text.appendPlainText(msg)

    def clear(self):
        self.text.clear()

    def _show_actions(self, on):
        for b in (self.copy_btn, self.save_btn):
            b.setVisible(on)

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Guardar el registro', 'md55-registro.txt',
                                              'Texto (*.txt);;Todos (*)')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.text.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, 'Guardar el registro', 'No se pudo guardar: %s' % e)


class ProgressPanel(QWidget):
    """Barra + línea de datos; oculta mientras no hay nada en marcha."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.stats = QLabel('')
        self.stats.setObjectName('hint')
        lay.addWidget(self.bar)
        lay.addWidget(self.stats)
        self.setVisible(False)

    def set(self, p, text=None):
        self.setVisible(True)
        self.bar.setValue(int(p * 1000))
        if text is not None:
            self.stats.setText(text)


def status_item(text, color):
    it = QTableWidgetItem('●  ' + text)
    it.setForeground(QColor(color))
    it.setToolTip(text)
    return it


# ---------------------------------------------------------------- rescatar tarjeta

class RescuePage(QWidget):
    COLS = ['', 'Archivo', 'Cámara', 'Duración', 'Formato', 'Perfil', 'Tamaño', 'Estado']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        self.job = None
        self.mode = None
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        setup = QWidget()
        self._setup_view(page_scaffold(setup))
        self.stack.addWidget(setup)
        self.stack.addWidget(self._run_view())
        self.refresh_drives()

    # ---- configuración
    def _setup_view(self, lay):
        lay.addWidget(page_header('Rescatar vídeos y fotos',
                                  'Primero se explora la tarjeta y se muestra todo lo que se puede recuperar; '
                                  'después eliges qué guardar. En la tarjeta nunca se escribe nada.'))
        head = QHBoxLayout()
        head.addWidget(section('1 · Tarjeta'))
        head.addStretch(1)
        refresh = QPushButton('Actualizar')
        refresh.clicked.connect(self.refresh_drives)
        img = QPushButton('Usar imagen de disco…')
        img.clicked.connect(self.pick_image)
        head.addWidget(refresh)
        head.addWidget(img)
        lay.addLayout(head)
        self.drives = DriveList(self._validate)
        lay.addWidget(self.drives)
        lay.addWidget(hint('Conecta la tarjeta con un lector o la cámara en modo USB «Mass Storage». '
                           'Si el sistema ofrece formatearla, pulsa Cancelar.'))

        lay.addWidget(section('2 · Qué buscar'))
        kinds = QHBoxLayout()
        kinds.setSpacing(24)
        self.want_video = QCheckBox('Vídeo  ·  XAVC S / HS / S-I, MXF')
        self.want_photo = QCheckBox('Fotos  ·  RAW (ARW), HEIF, JPG')
        for cb in (self.want_video, self.want_photo):
            cb.setChecked(True)
            cb.toggled.connect(self._validate)
            kinds.addWidget(cb)
        kinds.addStretch(1)
        lay.addWidget(card(kinds))

        lay.addWidget(section('3 · Dónde guardar'))
        self.out = PathRow('Carpeta en otro disco')
        self.out.setText(default_output())
        self.out.edit.textChanged.connect(self._validate)
        lay.addWidget(card(self.out, hint('Debe estar en otro disco, nunca en la tarjeta que se rescata.')))

        self.ref = PathRow('(opcional) carpeta con clips sanos de la misma cámara')
        self.cmp = PathRow('(opcional) carpeta con copias de los archivos para verificar')
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.addWidget(QLabel('Referencias para reparar incompletos'), 0, 0)
        g.addWidget(self.ref, 0, 1)
        g.addWidget(QLabel('Verificar contra carpeta'), 1, 0)
        g.addWidget(self.cmp, 1, 1)
        adv = card(g, hint('Con «Verificar», la búsqueda se detiene al encontrar todos esos archivos y '
                           'comprueba que los recuperados son idénticos byte a byte (SHA-256).'))
        lay.addWidget(Disclosure('Opciones avanzadas', adv), 0, Qt.AlignLeft)
        lay.addWidget(adv)
        lay.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        bottom.addStretch(1)
        self.go_hint = hint('')
        bottom.addWidget(self.go_hint)
        self.go = QPushButton('Explorar tarjeta')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        bottom.addWidget(self.go)
        lay.addLayout(bottom)
        self._validate()

    def refresh_drives(self):
        self.drives.set_drives([d for d in devices.list_drives() if not d['system']],
                               'Conéctala y pulsa «Actualizar», o usa una imagen de disco (.img).')

    def pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Imagen de la tarjeta', '',
                                           'Imágenes de disco (*.img *.dd *.bin *.dmg);;Todos (*)')
        if p:
            p = os.path.normpath(p)
            self.drives.add({'id': p, 'name': 'Imagen de disco', 'fs': '', 'size': os.path.getsize(p),
                             'removable': False, 'system': False, 'image': True})

    def _validate(self):
        if not hasattr(self, 'go'):         # aún construyendo la página
            return
        if not self.drives.selected():
            why = 'Elige una tarjeta o una imagen de disco.'
        elif not (self.want_video.isChecked() or self.want_photo.isChecked()):
            why = 'Marca vídeo, fotos o ambos.'
        elif not self.out.text():
            why = 'Indica dónde guardar lo recuperado.'
        else:
            why = ''
        self.go_hint.setText(why)
        self.go.setEnabled(not why)

    # ---- exploración, selección y recuperación
    C_CHECK, C_NAME, C_CAM, C_DUR, C_FMT, C_PROF, C_SIZE, C_STATE = range(8)

    def _run_view(self):
        w = QWidget()
        w.setObjectName('page')
        lay = QVBoxLayout(w)
        lay.setContentsMargins(36, 28, 36, 24)
        lay.setSpacing(12)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.run_title = QLabel('Explorando…')
        self.run_title.setObjectName('title')
        self.run_sub = QLabel('')
        self.run_sub.setObjectName('subtitle')
        self.run_sub.setWordWrap(True)
        self.run_sub.setTextFormat(Qt.PlainText)   # los mensajes llevan rutas con < y &
        titles.addWidget(self.run_title)
        titles.addWidget(self.run_sub)
        head.addLayout(titles, 1)
        self.stop_btn = QPushButton('Detener exploración')
        self.stop_btn.setObjectName('danger')
        self.stop_btn.clicked.connect(self.stop)
        self.cancel_btn = QPushButton('Cancelar')
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = QPushButton('Abrir carpeta')
        self.open_btn.clicked.connect(lambda: open_path(self.out_dir))
        self.new_btn = QPushButton('Nueva búsqueda')
        self.new_btn.setObjectName('primary')
        self.new_btn.clicked.connect(self.new_search)
        for b in (self.stop_btn, self.cancel_btn, self.open_btn, self.new_btn):
            head.addWidget(b, 0, Qt.AlignTop)
        lay.addLayout(head)
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)

        # filtros y selección
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(16)
        self.f_video = QCheckBox('Vídeos')
        self.f_photo = QCheckBox('Fotos')
        self.f_thumbs = QCheckBox('Miniaturas')
        self.f_incomplete = QCheckBox('Incompletos')
        for cb, on in ((self.f_video, True), (self.f_photo, True), (self.f_thumbs, False),
                       (self.f_incomplete, True)):
            cb.setChecked(on)
            cb.toggled.connect(self._apply_filter)
            bar.addWidget(cb)
        self.f_thumbs.setToolTip('JPG pequeños que la cámara crea para cada clip (no son fotos)')
        self.f_date = QComboBox()
        self.f_date.setMinimumWidth(190)
        self.f_date.addItem('Todas las fechas', '')
        self.f_date.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.f_date)
        bar.addStretch(1)
        self.sel_all = QPushButton('Seleccionar visibles')
        self.sel_all.clicked.connect(lambda: self._check_visible(True))
        self.sel_none = QPushButton('Ninguno')
        self.sel_none.clicked.connect(lambda: self._check_visible(False))
        bar.addWidget(self.sel_all)
        bar.addWidget(self.sel_none)
        self.filter_bar = QWidget()
        self.filter_bar.setLayout(bar)
        lay.addWidget(self.filter_bar)

        self.table = data_table(self.COLS)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setIconSize(QSize(18, 18))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        h = self.table.horizontalHeader()
        h.setMinimumSectionSize(34)
        h.setSortIndicatorShown(True)
        for col, width in ((self.C_CHECK, 62), (self.C_CAM, 72), (self.C_DUR, 84), (self.C_FMT, 168),
                           (self.C_PROF, 168), (self.C_SIZE, 80), (self.C_STATE, 122)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)
        h.setSectionResizeMode(self.C_NAME, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemSelectionChanged.connect(self._show_preview)
        self.table.itemChanged.connect(self._selection_changed)
        lay.addWidget(self.table, 1)

        # aviso cuando un filtro esconde todo, para no confundirlo con una búsqueda sin resultados
        self.empty_filter = QWidget()
        fl = QHBoxLayout(self.empty_filter)
        fl.setContentsMargins(2, 0, 0, 0)
        fl.setSpacing(10)
        nobody = QLabel('Ningún archivo coincide con los filtros.')
        nobody.setObjectName('hint')
        reset = QPushButton('Quitar filtros')
        reset.setObjectName('link')
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self.reset_filters)
        fl.addWidget(nobody)
        fl.addWidget(reset)
        fl.addStretch(1)
        self.empty_filter.setVisible(False)
        lay.addWidget(self.empty_filter)

        # vista previa (foto, o la miniatura de la cámara en los vídeos), debajo de la tabla
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview.setObjectName('preview')
        self.preview_info = QLabel()
        self.preview_info.setWordWrap(True)
        self.preview_info.setTextFormat(Qt.RichText)
        prow = QHBoxLayout()
        prow.setSpacing(16)
        prow.addWidget(self.preview)
        prow.addWidget(self.preview_info, 1, Qt.AlignTop)
        self.preview_card = card(prow, margins=(10, 10, 16, 10))
        self.preview_card.setVisible(False)
        lay.addWidget(self.preview_card)

        foot = QHBoxLayout()
        foot.setSpacing(14)
        self.log = LogPanel()
        foot.addWidget(self.log, 1, Qt.AlignTop)
        self.by_date = QCheckBox('Separar en carpetas por fecha')
        self.by_date.setToolTip('Guarda cada archivo en una subcarpeta AAAA-MM-DD según la fecha de la cámara')
        self.sel_label = QLabel('')
        self.sel_label.setObjectName('hint')
        self.recover_btn = QPushButton('Recuperar seleccionados')
        self.recover_btn.setObjectName('primary')
        self.recover_btn.clicked.connect(self.recover)
        actions = QWidget()
        al = QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(14)
        for wdg in (self.by_date, self.sel_label, self.recover_btn):
            al.addWidget(wdg)
        foot.addWidget(actions, 0, Qt.AlignTop)
        lay.addLayout(foot)
        return w

    def _set_mode(self, mode):
        """scan: explorando · choose: eligiendo · extract: recuperando · done: terminado."""
        self.mode = mode
        self.stop_btn.setVisible(mode == 'scan')
        self.stop_btn.setEnabled(True)
        self.cancel_btn.setVisible(mode in ('scan', 'choose'))
        self.open_btn.setVisible(mode in ('extract', 'done'))
        self.new_btn.setVisible(mode == 'done')
        self.filter_bar.setVisible(mode in ('scan', 'choose'))
        for wdg in (self.recover_btn, self.sel_label, self.by_date):
            wdg.setVisible(mode in ('scan', 'choose'))
        self._update_filter_state()
        # ordenar por columnas una vez terminada la exploración (mientras, por orden de hallazgo);
        # por defecto, cronológico
        sortable = mode in ('choose', 'done')
        if sortable and not self.table.isSortingEnabled():
            self.table.setSortingEnabled(True)
            self.table.sortItems(self.C_NAME, Qt.AscendingOrder)
        elif not sortable:
            self.table.setSortingEnabled(False)
        self._selection_changed()

    def start(self):
        d = self.drives.selected()
        self.out_dir = self.out.text()
        if not d or not self.out_dir:
            return
        try:
            os.makedirs(self.out_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, 'Carpeta de salida', str(e))
            return
        settings().setValue('salida/base', os.path.dirname(self.out_dir) or self.out_dir)
        self.preview_dir = worker.new_events_path() + '_previas'
        args = ['explorar', d['id'], '-o', self.out_dir, '--previas', self.preview_dir]
        if self.ref.text():
            args += ['--referencia', self.ref.text()]
        if self.cmp.text():
            args += ['--comparar', self.cmp.text()]
        args += ['--tipos', ','.join(t for t, cb in (('video', self.want_video), ('foto', self.want_photo))
                                     if cb.isChecked())]
        elevate = devices.is_device(d['id'])
        self.source_size = d.get('size') or 0
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self.f_date.blockSignals(True)
        self.f_date.clear()
        self.f_date.addItem('Todas las fechas', '')
        self.f_date.blockSignals(False)
        self.days = {}
        self.thumbs = []           # (instante, ruta de vista previa) de las miniaturas
        self.preview_card.setVisible(False)
        self.log.clear()
        self.progress.setVisible(False)
        self.found = {'video': 0, 'photo': 0, 'thumb': 0, 'video_bytes': 0}
        self.saved_v = self.saved_p = 0
        self.source_name = d['id'] if not d.get('image') else os.path.basename(d['id'])
        self.run_title.setText('Explorando %s' % self.source_name)
        self.run_sub.setText('Esperando permiso de Administrador…' if elevate and not devices.is_admin()
                             else 'Preparando…')
        self.stack.setCurrentIndex(1)
        self.visible_count = 0
        self.empty_filter.setVisible(False)
        self._set_mode('scan')
        self.t0 = None
        self.job = Job(args, elevate, self)
        self.job.log.connect(self._on_log)
        self.job.progress.connect(self._on_progress)
        self.job.event.connect(self._on_event)
        self.job.clip.connect(self._on_saved)
        self.job.finished.connect(self._on_finished)
        try:
            self.job.start()
        except PermissionError as e:
            self._on_finished(False, str(e))

    def stop(self):
        """Detiene la exploración: lo encontrado hasta ahora se puede recuperar."""
        if self.job:
            self.job.stop()
            self.stop_btn.setEnabled(False)
            self.run_sub.setText('Deteniendo la exploración…')

    def cancel(self):
        if self.job and self.job.running:
            self.job.stop()
        self.stack.setCurrentIndex(0)

    def new_search(self):
        """Vuelve a la configuración con las unidades al día y, si se usó la carpeta propuesta, otra nueva."""
        if os.path.dirname(self.out.text()) == os.path.dirname(default_output()):
            self.out.setText(default_output())
        self.refresh_drives()
        self.stack.setCurrentIndex(0)

    def _on_log(self, msg):
        if self.t0 is None:
            self.t0 = time.monotonic()
            if self.mode == 'scan':
                self.run_sub.setText('Leyendo la tarjeta sin modificar nada. Puedes ir marcando lo que '
                                     'quieras recuperar; «Detener exploración» permite elegir ya.')
        self.log.append(msg)

    def _on_progress(self, p):
        if self.t0 is None:
            self.t0 = time.monotonic()
        el = time.monotonic() - self.t0
        total = self.source_size if self.mode == 'scan' else self.extract_bytes
        parts = ['%.0f %%' % (p * 100)]
        if total:
            parts.append('%s de %s' % (human_size(total * p), human_size(total)))
            if el > 3 and 0.002 < p < 1:
                parts.append('%s/s' % human_size(total * p / el))
                parts.append('quedan ~%s' % human_time(el / p * (1 - p)))
        parts.append(self._found_text() if self.mode == 'scan' else
                     '%d de %d recuperados' % (self.saved_v + self.saved_p, self.extract_count))
        self.progress.set(p, '   ·   '.join(parts))

    def _found_text(self):
        f = self.found
        parts = ['%s (%s)' % (plural(f['video'], 'vídeo'), human_size(f['video_bytes'])),
                 plural(f['photo'], 'foto')]
        if f['thumb']:
            parts.append(plural(f['thumb'], 'miniatura'))
        return ' · '.join(parts)

    # ---- eventos del motor
    def _on_event(self, ev):
        t = ev.get('t')
        if t == 'item':
            self._add_item(ev)
        elif t == 'scan_done':
            self.run_title.setText('Encontrado en %s' % self.source_name)
            self.run_sub.setText('%s. Marca lo que quieras recuperar y pulsa «Recuperar seleccionados». '
                                 'Todavía no se ha guardado nada.' % self._found_text())
            self.progress.set(1.0, '100 %   ·   ' + self._found_text())
            self._refresh_days()
            self._set_mode('choose')
        elif t == 'phase' and ev.get('phase') == 'extract':
            self.t0 = time.monotonic()
            self.progress.set(0.0, '')
            self._set_mode('extract')

    def _add_item(self, ev):
        photo = ev.get('kind') in PHOTO_KINDS
        thumb = bool(ev.get('thumb'))
        if thumb:
            self.found['thumb'] += 1
            if ev.get('fecha_seg') and ev.get('preview'):
                self.thumbs.append((ev['fecha_seg'], ev['preview']))
        elif photo:
            self.found['photo'] += 1
        else:
            self.found['video'] += 1
            self.found['video_bytes'] += ev.get('size') or 0
        day = (ev.get('fecha') or '')[:10]
        if not thumb:
            if day not in self.days:
                self.days[day] = 0
                self._refresh_days()
            self.days[day] += 1
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        check = SortItem()
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if ev.get('complete') and not thumb else Qt.Unchecked)
        icon = 'video' if not photo else 'image' if thumb else 'photo'
        check.setIcon(QIcon(svg_pixmap(icon, VIDEO if not photo else MUTED if thumb else ACCENT, 18)))
        check.setData(Qt.UserRole, ev)
        check.setData(SORT_ROLE, 0 if not photo else 2 if thumb else 1)
        check.setToolTip('Vídeo' if not photo else 'Miniatura de la cámara' if thumb else 'Foto')
        name = SortItem(ev.get('name', ''))
        name.setToolTip(ev.get('note', ''))
        name.setData(SORT_ROLE, ev.get('fecha_seg') or ev.get('name', ''))
        cam = SortItem(camera_name(ev.get('camara')))
        cam.setToolTip(ev.get('camara') or '')
        dur = SortItem(human_time(ev.get('duracion')) if not photo else '—')
        dur.setData(SORT_ROLE, ev.get('duracion') or 0)
        size = SortItem(human_size(ev.get('size') or 0))
        size.setData(SORT_ROLE, ev.get('size') or 0)
        if thumb:
            state = status_item('Miniatura', FAINT)
        else:
            state = status_item('Encontrado' if ev.get('complete') else 'Incompleto',
                                MUTED if ev.get('complete') else WARN)
        cells = [check, name, cam, dur, SortItem(ev.get('codec') or '—'), SortItem(ev.get('perfil') or '—'),
                 size, state]
        for c, it in enumerate(cells):
            if c and not it.toolTip():
                it.setToolTip(it.text())        # por si la columna corta el texto
            self.table.setItem(r, c, it)
        self._apply_filter_row(r)
        self.table.blockSignals(False)
        if not self.table.isRowHidden(r):
            self.empty_filter.setVisible(False)
        self._selection_changed()

    def _refresh_days(self):
        current = self.f_date.currentData()
        self.f_date.blockSignals(True)
        self.f_date.clear()
        self.f_date.addItem('Todas las fechas', '')
        for day in sorted(self.days, reverse=True):
            label = day_label(day) if day else 'Sin fecha'
            self.f_date.addItem('%s · %d' % (label, self.days[day]), day or '-')
        idx = self.f_date.findData(current)
        self.f_date.setCurrentIndex(max(0, idx))
        self.f_date.blockSignals(False)

    def _row_of(self, item_id):
        for r in range(self.table.rowCount()):
            ev = self.table.item(r, self.C_CHECK).data(Qt.UserRole)
            if ev and ev.get('id') == item_id:
                return r
        return None

    def _apply_filter_row(self, r):
        ev = self.table.item(r, self.C_CHECK).data(Qt.UserRole)
        photo = ev.get('kind') in PHOTO_KINDS
        thumb = bool(ev.get('thumb'))
        day = self.f_date.currentData()
        hidden = ((thumb and not self.f_thumbs.isChecked())
                  or (photo and not thumb and not self.f_photo.isChecked())
                  or (not photo and not self.f_video.isChecked())
                  or (not ev.get('complete') and not self.f_incomplete.isChecked())
                  or (day and (ev.get('fecha') or '')[:10] != ('' if day == '-' else day)))
        self.table.setRowHidden(r, bool(hidden))

    def _apply_filter(self, *_):
        for r in range(self.table.rowCount()):
            self._apply_filter_row(r)
        self._update_filter_state()
        self._selection_changed()

    def _update_filter_state(self, *_):
        """Cuenta lo visible y avisa si los filtros lo esconden todo."""
        self.visible_count = sum(1 for r in range(self.table.rowCount())
                                 if not self.table.isRowHidden(r))
        show = (self.table.rowCount() > 0 and self.visible_count == 0
                and getattr(self, 'mode', None) in ('scan', 'choose'))
        self.empty_filter.setVisible(show)
        for b in (self.sel_all, self.sel_none):
            b.setEnabled(self.visible_count > 0)

    def reset_filters(self):
        """Vuelve a los filtros iniciales para ver todo lo encontrado."""
        for cb, on in ((self.f_video, True), (self.f_photo, True), (self.f_thumbs, False),
                       (self.f_incomplete, True)):
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self.f_date.blockSignals(True)
        self.f_date.setCurrentIndex(0)
        self.f_date.blockSignals(False)
        self._apply_filter()

    def _check_visible(self, on):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, self.C_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False)
        self._selection_changed()

    def _chosen(self):
        """Elementos marcados y visibles (lo oculto por un filtro no se recupera)."""
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, self.C_CHECK)
            if it and it.checkState() == Qt.Checked and not self.table.isRowHidden(r):
                out.append(it.data(Qt.UserRole))
        return out

    def _selection_changed(self, *_):
        if not hasattr(self, 'recover_btn') or getattr(self, 'mode', None) not in ('scan', 'choose'):
            return
        chosen = self._chosen()
        self.sel_label.setText('%d seleccionados · %s' % (len(chosen),
                                                          human_size(sum(e.get('size') or 0 for e in chosen))))
        self.recover_btn.setEnabled(self.mode == 'choose' and bool(chosen))
        self.recover_btn.setToolTip('' if self.mode == 'choose' else
                                    'Disponible al terminar la exploración (o pulsa «Detener exploración»).')

    def recover(self):
        chosen = self._chosen()
        if not chosen or not self.job:
            return
        need = sum(e.get('size') or 0 for e in chosen)
        try:
            free = shutil.disk_usage(self.out_dir).free
        except OSError:
            free = None
        if free is not None and free < need + 64 * 1024 * 1024:
            QMessageBox.warning(self, 'Sin espacio suficiente',
                                'Lo seleccionado ocupa %s y en el destino solo hay %s libres.\n\n'
                                'Elige menos archivos o cambia la carpeta de destino (Nueva búsqueda).'
                                % (human_size(need), human_size(free)))
            return
        ids = {e['id'] for e in chosen}
        self.extract_count = len(chosen)
        self.extract_bytes = need
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, self.C_CHECK)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            ev = it.data(Qt.UserRole)
            if ev['id'] in ids:
                self.table.setItem(r, self.C_STATE, status_item('Pendiente', MUTED))
            else:
                self.table.setItem(r, self.C_STATE, status_item('Omitido', FAINT))
                self.table.setRowHidden(r, True)
        self.table.blockSignals(False)
        self.run_title.setText('Recuperando %s' % plural(len(chosen), 'archivo'))
        self.run_sub.setText('Guardando en %s%s' % (self.out_dir, ' (una carpeta por fecha)'
                                                   if self.by_date.isChecked() else ''))
        self.job.send_selection(sorted(ids), por_fecha=self.by_date.isChecked())
        self._set_mode('extract')

    def _on_saved(self, ev):
        r = self._row_of(ev.get('id'))
        if r is None:
            return
        if ev.get('kind') in PHOTO_KINDS:
            self.saved_p += 1
        else:
            self.saved_v += 1
        if ev.get('identico_a'):
            state, color = 'Idéntico a ' + ev['identico_a'], OK
        elif ev.get('note', '').startswith('reparado'):
            state, color = 'Reparado', OK
        else:
            state, color = ('Recuperado', OK) if ev.get('complete') else ('Guardado incompleto', WARN)
        it = self.table.item(r, self.C_CHECK)
        it.setData(Qt.UserRole, dict(it.data(Qt.UserRole), path=ev.get('path')))
        self.table.item(r, self.C_NAME).setText(os.path.basename(ev.get('path', '')))
        self.table.item(r, self.C_NAME).setToolTip(ev.get('path', ''))
        self.table.setItem(r, self.C_STATE, status_item(state, color))
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() == r:
            self._show_preview()

    def _open_row(self, r, _c):
        ev = self.table.item(r, self.C_CHECK).data(Qt.UserRole) or {}
        if ev.get('path'):
            open_path(ev['path'])

    def _thumb_for(self, ev):
        """Miniatura de la cámara de un clip: la de hora más cercana (±3 s) al inicio del clip."""
        when = parse_when(ev.get('fecha_seg'))
        if when is None:
            return None
        best = None
        for stamp, path in self.thumbs:
            t = parse_when(stamp)
            gap = abs((t - when).total_seconds()) if t is not None else None
            if gap is not None and gap <= 3 and (best is None or gap < best[0]):
                best = (gap, path)
        return best[1] if best else None

    def _show_preview(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        ev = self.table.item(rows[0].row(), self.C_CHECK).data(Qt.UserRole) or {}
        photo = ev.get('kind') in PHOTO_KINDS
        source = ev.get('preview') if photo else self._thumb_for(ev)
        pm = QPixmap(source) if source and os.path.exists(source) else None
        if pm is None or pm.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText('Sin vista previa')
        else:
            ratio = self.devicePixelRatioF()
            pm = pm.scaled(int(PREVIEW_W * ratio), int(PREVIEW_H * ratio), Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
            pm.setDevicePixelRatio(ratio)
            self.preview.setPixmap(pm)
        cam = camera_name(ev.get('camara'))
        details = [ev.get('codec') or '—',
                   None if photo else human_time(ev.get('duracion')) if ev.get('duracion') else None,
                   ev.get('perfil'), cam if cam != '—' else None,
                   ev.get('fecha_seg') or ev.get('fecha'), human_size(ev.get('size') or 0)]
        where = ev.get('path') or ('Miniatura que la cámara crea para un clip; no es una foto.'
                                   if ev.get('thumb') else 'Aún no recuperado')
        note = '' if photo else ('Imagen: miniatura de la cámara' if source else
                                 'Este clip no tiene miniatura de la cámara en la tarjeta')
        if not ev.get('complete') and ev.get('note'):
            note = 'Incompleto: %s' % ev['note']
        self.preview_info.setText(rich(esc(os.path.basename(ev.get('path') or ev.get('name', ''))),
                                       (esc(' · '.join(x for x in details if x)), MUTED),
                                       ('', None), (esc(where), MUTED), (esc(note), DIM)))
        self.preview_card.setVisible(True)

    def _on_finished(self, ok, msg):
        mode = getattr(self, 'mode', 'scan')
        if mode in ('scan', 'choose') and ok:
            self.stack.setCurrentIndex(0)       # cancelado antes de recuperar
            return
        self._set_mode('done')
        if ok:
            got = [plural(n, w) for n, w in ((self.saved_v, 'vídeo'), (self.saved_p, 'foto')) if n]
            ending = ('a' if not self.saved_v else 'o') + ('' if self.saved_v + self.saved_p == 1 else 's')
            self.run_title.setText('Terminado: %s recuperad%s' % (' y '.join(got), ending) if got
                                   else 'Terminado: no se recuperó nada')
            self.run_sub.setText('Doble clic en un archivo para abrirlo. Están en %s' % self.out_dir)
            self.progress.set(1.0, '100 %')
        else:
            self.run_title.setText('No se pudo completar')
            self.run_sub.setText(msg)


# ---------------------------------------------------------------- reparar clip

class RepairPage(QWidget):
    COLS = ['Estado', 'Clip dañado', 'Duración', 'Perfil', 'Resultado']

    def __init__(self, parent=None):
        super().__init__(parent)
        accept_file_drops(self, self._add)
        lay = page_scaffold(self)
        lay.addWidget(page_header('Reparar clips interrumpidos',
                                  'Para clips que no abren porque la grabación se cortó (batería, tarjeta '
                                  'extraída, apagado). Reconstruye el índice sin recodificar: S-Log3, '
                                  'S-Cinetone y 10 bits se conservan intactos.'))
        head = QHBoxLayout()
        head.addWidget(section('1 · Clips dañados'))
        head.addStretch(1)
        add = QPushButton('Añadir clips…')
        add.clicked.connect(self.add_files)
        self.clear_btn = QPushButton('Quitar todos')
        self.clear_btn.clicked.connect(lambda: self.table.setRowCount(0))
        head.addWidget(add)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)
        self.table = data_table(self.COLS)
        self.table.setMinimumHeight(180)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_result)
        drop = EmptyState('repair', 'Arrastra aquí los clips que no abren',
                          'o usa «Añadir clips…». Se reparan uno tras otro con la misma referencia.')
        drop.setMinimumHeight(200)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(drop)
        self.list_stack.addWidget(self.table)
        self.table.model().rowsInserted.connect(self._update_empty)
        self.table.model().rowsRemoved.connect(self._update_empty)
        self.table.model().modelReset.connect(self._update_empty)
        lay.addWidget(self.list_stack, 1)

        lay.addWidget(section('2 · Referencia'))
        self.ref = PathRow('Carpeta con clips sanos (se elige el compatible) o un clip concreto')
        self.ref.edit.textChanged.connect(self._update_empty)
        btn_file = QPushButton('Un clip…')
        btn_file.clicked.connect(self._pick_ref_file)
        self.ref.layout().addWidget(btn_file)
        lay.addWidget(card(self.ref, hint('Clips sanos de la misma cámara con el mismo formato, resolución, '
                                          'fps y bitrate. Basta con 5–30 s. Si das una carpeta, se elige '
                                          'automáticamente.')))
        lay.addWidget(section('3 · Dónde guardar'))
        self.out = PathRow('(por defecto, junto a cada clip dañado)')
        lay.addWidget(card(self.out))
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)
        row = QHBoxLayout()
        self.log = LogPanel()
        row.addWidget(self.log, 1, Qt.AlignTop)
        self.go_hint = hint('')
        row.addWidget(self.go_hint, 0, Qt.AlignVCenter)
        self.go = QPushButton('Reparar')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        row.addWidget(self.go, 0, Qt.AlignTop)
        lay.addLayout(row)
        self.queue = []
        self.job = None
        self._update_empty()

    def _pending(self):
        return [r for r in range(self.table.rowCount())
                if self.table.item(r, 0) and self.table.item(r, 0).text().endswith(('Pendiente', 'Error'))]

    def _update_empty(self, *_):
        self.list_stack.setCurrentIndex(1 if self.table.rowCount() else 0)
        busy = bool(self.job and self.job.running)
        why = ('' if busy else 'Añade los clips dañados.' if not self._pending()
               else 'Indica la referencia.' if not self.ref.text() else '')
        self.go_hint.setText(why)
        self.go.setEnabled(not busy and not why)

    def _pick_ref_file(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Clip de referencia', '', VIDEO_FILTER)
        if p:
            self.ref.setText(os.path.normpath(p))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Clips dañados', '', VIDEO_FILTER)
        self._add(files)

    def _add(self, files):
        known = {self.table.item(r, 1).data(Qt.UserRole) for r in range(self.table.rowCount())}
        for f in files:
            f = os.path.normpath(f)
            if not os.path.isfile(f) or f in known:
                continue
            known.add(f)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, status_item('Pendiente', MUTED))
            name = QTableWidgetItem(os.path.basename(f))
            name.setData(Qt.UserRole, f)
            name.setToolTip(f)
            self.table.setItem(r, 1, name)
            for c in (2, 3, 4):
                self.table.setItem(r, c, QTableWidgetItem('—' if c < 4 else ''))
        self._update_empty()

    def _open_result(self, r, _c):
        it = self.table.item(r, 4)
        open_path((it.data(Qt.UserRole) if it else None) or self.table.item(r, 1).data(Qt.UserRole))

    def start(self):
        if not self.ref.text():
            QMessageBox.information(self, 'Referencia', 'Indica una carpeta con clips sanos o un clip de referencia.')
            return
        self.queue = self._pending()
        if not self.queue:
            return
        self.go.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.log.clear()
        self.done_count = 0
        self.total = len(self.queue)
        self._next()

    def _next(self):
        if not self.queue:
            self.job = None
            self.clear_btn.setEnabled(True)
            self._update_empty()
            self.progress.set(1.0, 'Terminado: %s' % plural(self.total, 'clip'))
            return
        self.row = self.queue.pop(0)
        src = self.table.item(self.row, 1).data(Qt.UserRole)
        out_dir = self.out.text() or os.path.dirname(src)
        out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + '_REPARADO.MP4')
        self.table.setItem(self.row, 0, status_item('Reparando…', ACCENT))
        self.log.append('── %s' % src)
        self.job = Job(['reparar', src, '--referencia', self.ref.text(), '-o', out], False, self)
        self.job.log.connect(self.log.append)
        self.job.progress.connect(lambda p: self.progress.set(
            (self.done_count + p) / self.total, 'Clip %d de %d' % (self.done_count + 1, self.total)))
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
        self.table.setItem(self.row, 0, status_item('Reparado' if ok else 'Error', OK if ok else BAD))
        if not ok:
            self.table.item(self.row, 4).setText(msg)
            self.log.append('ERROR: ' + msg)
        self.done_count += 1
        self._next()


# ---------------------------------------------------------------- analizar

class AnalyzePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        accept_file_drops(self, self._dropped)
        lay = page_scaffold(self)
        lay.addWidget(page_header('Analizar un clip',
                                  'Estructura del archivo, si le falta el índice, códec, fps y perfil de '
                                  'imagen (S-Log3, S-Cinetone, HLG…) leído de los metadatos de Sony.'))
        row = QHBoxLayout()
        self.path = PathRow('Clip MP4 / MXF', mode='file', filter_='Vídeo (*.mp4 *.MP4 *.mxf *.MXF *.mov)')
        row.addWidget(self.path, 1)
        self.go = QPushButton('Analizar')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.run)
        self.path.edit.textChanged.connect(lambda t: self.go.setEnabled(os.path.isfile(t.strip())))
        self.path.edit.returnPressed.connect(self.run)
        self.go.setEnabled(False)
        row.addWidget(self.go)
        lay.addWidget(card(row))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(420)
        empty = EmptyState('analyze', 'Arrastra aquí un clip',
                           'o elígelo arriba. Solo se lee: el archivo no se modifica.')
        self.body = QStackedWidget()
        self.body.addWidget(empty)
        self.body.addWidget(self.text)
        lay.addWidget(self.body, 1)
        self.thread = None

    def _dropped(self, paths):
        if paths:
            self.path.setText(paths[0])
            self.run()

    def run(self):
        if not os.path.isfile(self.path.text()) or (self.thread and self.thread.isRunning()):
            return
        self.body.setCurrentIndex(1)
        self.text.setPlainText('Analizando %s…' % os.path.basename(self.path.text()))
        self.go.setEnabled(False)
        self.thread = AnalyzeThread(self.path.text(), self)
        self.thread.result.connect(self.text.setPlainText)
        self.thread.finished.connect(lambda: self.go.setEnabled(True))
        self.thread.start()


# ---------------------------------------------------------------- herramientas de tarjeta

class CardToolsPage(QWidget):
    """Información, salud, pruebas y reparación de la tarjeta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job = None
        self.result_text = None
        lay = page_scaffold(self)
        lay.addWidget(page_header('Herramientas de tarjeta',
                                  'Comprueba si una tarjeta es fiable (falsa, sectores dañados, velocidad '
                                  'real), su vida útil si la informa, y repara el formato o la partición.'))
        warn = QFrame()
        warn.setObjectName('warn')
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(14, 10, 14, 10)
        wi = QLabel('⚠')
        wi.setStyleSheet('font-size: 20px; color: #ffb4b4;')
        wt = QLabel('<b>La prueba de capacidad, la reparación y el formateo escriben en la tarjeta.</b> '
                    'Después, recuperar los vídeos y fotos borrados de esa tarjeta será imposible. '
                    'Si hay algo que rescatar, usa antes «Rescatar tarjeta».')
        wt.setWordWrap(True)
        wt.setStyleSheet('color: #ffcfcf;')
        wl.addWidget(wi, 0, Qt.AlignTop)
        wl.addWidget(wt, 1)
        lay.addWidget(warn)

        head = QHBoxLayout()
        head.addWidget(section('Tarjeta'))
        head.addStretch(1)
        refresh = QPushButton('Actualizar')
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        lay.addLayout(head)
        self.drives = DriveList(self._selected_changed)
        lay.addWidget(self.drives)
        self.info = QLabel('Elige una tarjeta para ver su información.')
        self.info.setObjectName('hint')
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info_card = card(self.info)
        lay.addWidget(self.info_card)

        lay.addWidget(section('Herramientas'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.cards = [
            ActionCard('Información y vida útil', 'Modelo, particiones, formato y desgaste si la tarjeta '
                       'o el lector lo informan (CFexpress suele hacerlo; SD por USB casi nunca).',
                       'No borra', OK, 'Consultar', self.do_info),
            ActionCard('Prueba de lectura completa', 'Lee toda la tarjeta buscando sectores ilegibles y '
                       'zonas lentas. Pide permiso de Administrador.', 'No borra', OK, 'Iniciar',
                       self.do_surface),
            ActionCard('Capacidad y velocidad real', 'Llena el espacio libre y lo verifica sin caché: '
                       'detecta tarjetas falsas, datos corruptos y la clase real (V30/V60/V90).',
                       'Sobrescribe lo borrado', WARN, 'Iniciar', self.do_capacity),
            ActionCard('Reparar sistema de archivos', 'Corrige errores del sistema de archivos (chkdsk / '
                       'diskutil) sin borrar los archivos visibles.', 'Modifica la tarjeta', WARN,
                       'Reparar', self.do_fsrepair),
            ActionCard('Formatear y rehacer partición', 'Deja la tarjeta como nueva: MBR, alineación a 4 MB '
                       'y FAT32 / exFAT como pide la SD Association.', 'Borra todo', BAD, 'Formatear…',
                       self.do_format, danger=True),
        ]
        for i, c in enumerate(self.cards):
            grid.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid)

        self.status = QLabel('')
        self.status.setObjectName('cardTitle')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)   # los mensajes de error llevan rutas
        self.progress = ProgressPanel()
        stop_row = QHBoxLayout()
        stop_row.addWidget(self.status, 1)
        self.stop_btn = QPushButton('Detener')
        self.stop_btn.setObjectName('danger')
        self.stop_btn.clicked.connect(lambda: self.job and self.job.stop())
        stop_row.addWidget(self.stop_btn)
        self.log = LogPanel()
        self.run_card = card(stop_row, self.progress, self.log)
        self.run_card.setVisible(False)
        lay.addWidget(self.run_card)
        lay.addStretch(1)
        self.refresh()

    # ---- selección
    def refresh(self):
        self.drives.set_drives([d for d in devices.list_drives() if not d['system'] and d['removable']],
                               'Solo se muestran unidades extraíbles (tarjetas y lectores), nunca discos '
                               'ni SSD externos. Conecta la tarjeta y pulsa «Actualizar».')

    def _sel(self):
        return self.drives.selected()

    def _busy(self):
        return bool(self.job and self.job.running)

    def _set_enabled(self):
        for c in self.cards:
            c.button.setEnabled(bool(self._sel()) and not self._busy())

    def _selected_changed(self):
        self._set_enabled()
        self.info_card.setVisible(bool(self._sel()))
        if self._sel() and not self._busy():
            self.do_info()

    # ---- acciones
    def _run(self, args, title, elevate=False):
        if self._busy():
            return
        self.current = title
        self.result_text = None
        self.log.clear()
        self.progress.setVisible(False)
        self.status.setText(title + ('  ·  esperando permiso de Administrador…'
                                     if elevate and not devices.is_admin() else '…'))
        self.stop_btn.setVisible(True)
        self.run_card.setVisible(args[0] != 'tarjeta')
        self.job = Job(args, elevate, self)
        self.job.log.connect(self.log.append)
        self.job.progress.connect(lambda p: self.progress.set(p, '%.0f %%' % (p * 100)))
        self.job.result.connect(self._on_result)
        self.job.finished.connect(self._on_finished)
        self._set_enabled()
        try:
            self.job.start()
        except PermissionError as e:
            self._on_finished(False, str(e))
        self._set_enabled()

    def do_info(self):
        self.info.setText('Leyendo información de %s…' % self._sel()['id'])
        self._run(['tarjeta', self._sel()['id']], 'Información')

    def do_surface(self):
        self._run(['probar-lectura', self._sel()['id']], 'Prueba de lectura completa', elevate=True)

    def _disclaimer(self, title, what):
        """Aviso que hay que aceptar marcando la casilla. True si el usuario acepta."""
        d = self._sel()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText('<b>%s escribe en %s (%s, %s).</b>' % (what, d['id'], d['name'],
                                                         human_size(d['size'] or 0)))
        box.setInformativeText(
            'Después de usar esta herramienta, la RECUPERACIÓN de los vídeos y fotos borrados de esta '
            'tarjeta será IMPOSIBLE, también con otros programas.\n\n'
            'Si la tarjeta tiene algo que quieras rescatar, cancela y usa primero «Rescatar tarjeta».')
        check = QCheckBox('Entiendo que después no se podrá recuperar nada borrado de esta tarjeta')
        box.setCheckBox(check)
        go = box.addButton('Continuar', QMessageBox.AcceptRole)
        box.addButton('Cancelar', QMessageBox.RejectRole)
        go.setEnabled(False)
        check.toggled.connect(go.setEnabled)
        box.exec()
        return box.clickedButton() is go and check.isChecked()

    def do_capacity(self):
        d = self._sel()
        if self._disclaimer('Prueba de capacidad y velocidad',
                            'La prueba llena todo el espacio libre con datos de prueba y'):
            self._run(['probar-capacidad', d['id'], '--acepto'], 'Prueba de capacidad y velocidad')

    def do_fsrepair(self):
        if self._disclaimer('Reparar sistema de archivos', 'La reparación modifica la estructura y'):
            self._run(['reparar-fs', self._sel()['id'], '--acepto'], 'Reparación del sistema de archivos',
                      elevate=True)

    def do_format(self):
        d = self._sel()
        if not self._disclaimer('Formatear y rehacer partición', 'El formateo BORRA TODO y'):
            return
        text, ok = QInputDialog.getText(
            self, 'Formatear y rehacer partición',
            'Esto BORRA TODO lo que hay en %s (%s, %s) y no se puede deshacer.\n'
            'Escribe %s para confirmar:' % (d['id'], d['name'], human_size(d['size'] or 0), d['id']))
        if ok and text.strip().upper().rstrip('\\') == d['id'].upper().rstrip('\\'):
            self._run(['formatear', d['id'], '--confirmar', text.strip(), '--acepto'], 'Formateo',
                      elevate=True)
        elif ok:
            QMessageBox.information(self, 'Formatear', 'La confirmación no coincide: no se ha borrado nada.')

    # ---- resultados
    def _on_result(self, data):
        if 'tarjeta' in data:
            c, h = data['tarjeta'], data['salud']
            parts = ', '.join('%s a %s MB' % (p.get('letter') or p.get('n'), (p.get('offset') or 0) // 1048576)
                              for p in c.get('partitions', []))
            used = (c['volsize'] or 0) - (c['free'] or 0) if c.get('volsize') else None
            line = ' · '.join(x for x in (
                c.get('bus'), ' '.join(x for x in (c.get('style'), c.get('fs')) if x),
                'partición %s' % (parts or '—'),
                '%s usados de %s' % (human_size(used), human_size(c['volsize'])) if used is not None else None)
                if x)
            self.info.setText(rich('%s &nbsp;%s &nbsp;<span style="color:%s;font-weight:400">%s</span>'
                                   % (esc(c['id']), esc(c['model']), MUTED,
                                      human_size(c['size'] or 0)),
                                   (esc(line), MUTED),
                                   ('<b>Vida útil:</b> %s' % esc(h['conclusion']), '#e6e6e6')))
        elif 'veredicto' in data:
            v = data['veredicto']
            text = {'correcta': '✔ Tarjeta correcta', 'falsa': '✖ TARJETA FALSA',
                    'errores': '✖ Tarjeta con DATOS CORRUPTOS', 'incompleta': 'Prueba incompleta'}[v]
            self.result_text = '%s · escritura %.0f MB/s (mín. %.0f) · lectura %.0f MB/s · clase real: %s' % (
                text, data['escritura_media'], data['escritura_minima'], data['lectura_media'],
                data.get('clase') or 'ninguna')
        elif 'zonas_ilegibles' in data:
            bad = data['zonas_ilegibles']
            self.result_text = ('✖ %d zonas ilegibles (%s)' % (len(bad), human_size(data['bytes_ilegibles']))
                                if bad else '✔ Sin sectores dañados') + \
                ' · lectura media %.0f MB/s, mínima %.0f MB/s' % (data.get('lectura_media', 0),
                                                                   data.get('lectura_min', 0))
        elif 'fs' in data:
            self.result_text = '✔ Formateada en %s (clúster %s). Formatéala también en la cámara.' % (
                data['fs'], data['cluster'])

    def _on_finished(self, ok, msg):
        self.stop_btn.setVisible(False)
        self._set_enabled()
        summary, self.result_text = self.result_text, None
        if self.current == 'Información':
            if not ok:
                self.info.setText('No se pudo leer la información: %s' % msg)
            return
        if ok:
            self.progress.set(1.0, '100 %')
            self.status.setText('%s: %s' % (self.current, summary) if summary else self.current + ': hecho.')
        else:
            self.status.setText('%s: no se pudo completar. %s' % (self.current, msg))


# ---------------------------------------------------------------- acerca de

class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = page_scaffold(self)
        top = QHBoxLayout()
        top.setSpacing(20)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(84))
        top.addWidget(mark, 0, Qt.AlignTop)
        top.addWidget(page_header('MD55 Rescue %s' % __version__,
                                  'Recuperación de vídeo y fotos para cámaras Sony: XAVC S / HS / S-I, MXF, '
                                  'RAW (ARW), HEIF y JPG. Copia los datos originales sin recodificar.'), 1)
        lay.addLayout(top)
        tips = QLabel(
            '<p style="color:#fff"><b>Buenas prácticas</b></p><ul style="color:#c9ccd3">'
            '<li>Deja de grabar en la tarjeta afectada en cuanto notes el problema.</li>'
            '<li>Guarda siempre lo recuperado en otro disco.</li>'
            '<li>Si el sistema ofrece formatear la tarjeta, pulsa Cancelar.</li>'
            '<li>Cuantas menos veces conectes la tarjeta antes de rescatarla, mejor: el sistema escribe '
            'en ella al conectarla.</li>'
            '<li>Para reparar un clip, graba uno de referencia con la misma cámara y configuración.</li></ul>'
            '<p style="color:#fff"><b>Límites</b></p><p style="color:#c9ccd3">Un formateo completo '
            '(sobrescritura) no es recuperable. Lo que se haya sobrescrito después de borrar o formatear '
            'tampoco. Los archivos muy fragmentados pueden salir incompletos.</p>')
        tips.setWordWrap(True)
        tips.setTextFormat(Qt.RichText)
        lay.addWidget(card(tips, margins=(20, 8, 20, 12)))
        lay.addStretch(1)


# ---------------------------------------------------------------- ventana

class MainWindow(QMainWindow):
    PAGES = [('rescue', 'Rescatar tarjeta'), ('repair', 'Reparar clip'), ('analyze', 'Analizar clip'),
             ('tools', 'Herramientas de tarjeta'), ('about', 'Acerca de')]

    def __init__(self):
        super().__init__()
        self.setWindowTitle('MD55 Rescue')
        self.setWindowIcon(app_icon())
        self.resize(1200, 820)
        self.setMinimumSize(980, 660)
        geometry = settings().value('ventana/geometria')
        if geometry:
            self.restoreGeometry(geometry)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setObjectName('side')
        side.setFixedWidth(236)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 24, 0, 18)
        sl.setSpacing(14)
        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 12, 8)
        brand.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(32))
        name = QLabel('MD55 Rescue')
        name.setStyleSheet('font-size: 17px; font-weight: 700; color: #ffffff;')
        brand.addWidget(mark)
        brand.addWidget(name)
        brand.addStretch(1)
        sl.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setIconSize(QSize(20, 20))
        for i, (key, label) in enumerate(self.PAGES):
            item = QListWidgetItem(uikit.nav_icon(key), '  ' + label)
            item.setToolTip('%s  ·  Ctrl+%d' % (label, i + 1))
            self.nav.addItem(item)
            short = QShortcut(QKeySequence('Ctrl+%d' % (i + 1)), self)
            short.activated.connect(lambda row=i: self.nav.setCurrentRow(row))
        sl.addWidget(self.nav, 1)
        perm = QLabel(('●  Con permisos de Administrador' if devices.is_admin() else
                       '●  Pedirá permiso al leer la tarjeta'))
        perm.setObjectName('hint')
        perm.setContentsMargins(22, 0, 0, 0)
        ver = QLabel('Versión %s' % __version__)
        ver.setObjectName('hint')
        ver.setContentsMargins(22, 0, 0, 0)
        sl.addWidget(perm)
        sl.addWidget(ver)
        self.pages = QStackedWidget()
        for p in (RescuePage(), RepairPage(), AnalyzePage(), CardToolsPage(), AboutPage()):
            self.pages.addWidget(p)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        lay.addWidget(side)
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(central)

    def closeEvent(self, e):
        settings().setValue('ventana/geometria', self.saveGeometry())
        # que ningún proceso (p.ej. uno con permisos esperando la selección) quede en marcha
        for i in range(self.pages.count()):
            job = getattr(self.pages.widget(i), 'job', None)
            if job is not None and job.running:
                job.stop()
        super().closeEvent(e)


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
    app.setStyleSheet(uikit.stylesheet())
    try:
        worker.clean_temp()             # eventos y vistas previas de días anteriores
    except OSError:
        pass
    w = MainWindow()
    w.show()
    return app.exec()
