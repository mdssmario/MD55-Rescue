"""Interfaz de escritorio (Qt / PySide6) para Windows y macOS."""
import datetime
import os
import shutil
import sys
import time

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__, analyze, clipinfo, devices, ffmpeg, uikit, worker
from .carve import PHOTO_KINDS
from .events import EventReader
from .uikit import (ACCENT, BAD, CUT, DIM, FAINT, MUTED, OK, PILL_ROLE, TEXT, VIDEO, WARN, ActionCard,
                    Banner, ChoiceCard, Chip, Disclosure, DriveList, EmptyState, InfoGrid, PillDelegate,
                    StatTile, esc, human_size, icon, icon_tile, load_thumb, plural, section, svg_pixmap,
                    thumb_placeholder, tint)

SORT_ROLE = Qt.UserRole + 3
VIDEO_FILTER = 'Vídeo (*.mp4 *.MP4 *.mov *.MOV)'
# RSV: el archivo que deja la cámara al cortarse la grabación (sin cabecera ni índice)
DAMAGED_FILTER = 'Clips dañados (*.mp4 *.MP4 *.mov *.MOV *.rsv *.RSV);;Todos (*)'
PREVIEW_W, PREVIEW_H = 256, 144
THUMB_W, THUMB_H = 64, 36
ROW_H = 50

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
    """Analiza un clip y, si hay ffmpeg, saca un fotograma para la ficha."""
    result = Signal(dict)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        out = {'path': self.path}
        try:
            out['text'] = analyze.analyze(self.path)
            out['info'] = clipinfo.summary(self.path)
            frame = os.path.join(worker.temp_dir(), 'analisis_%d.jpg' % int(time.time() * 1000))
            out['frame'] = frame if ffmpeg.frame(self.path, frame, width=640) else None
        except Exception as e:      # noqa: BLE001
            out['text'] = 'No se pudo analizar: %s' % e
        self.result.emit(out)


# ---------------------------------------------------------------- piezas comunes

def page_header(title, subtitle, icon_name=None):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    lay.setSpacing(16)
    if icon_name:
        lay.addWidget(icon_tile(icon_name, 52), 0, Qt.AlignTop)
    words = QVBoxLayout()
    words.setSpacing(4)
    t = QLabel(title)
    t.setObjectName('title')
    s = QLabel(subtitle)
    s.setObjectName('subtitle')
    s.setWordWrap(True)
    words.addWidget(t)
    words.addWidget(s)
    lay.addLayout(words, 1)
    w.title, w.subtitle = t, s
    return w


def hint(text):
    lab = QLabel(text)
    lab.setObjectName('hint')
    lab.setWordWrap(True)
    return lab


def button(text, icon_name=None, kind=None, color=None):
    """Botón con icono opcional; kind: primary, danger, ghost."""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        b.setIcon(icon(icon_name, color or ('#1a1306' if kind == 'primary' else
                                            '#ffd6d6' if kind == 'danger' else TEXT), 16))
        b.setIconSize(QSize(16, 16))
    return b


def data_table(cols):
    """Tabla de solo lectura con el aspecto común de la aplicación."""
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels([c.upper() for c in cols])
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.horizontalHeader().setHighlightSections(False)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(ROW_H)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.ElideMiddle)        # que se vea el número y la extensión del archivo
    t.setFocusPolicy(Qt.NoFocus)
    t.setIconSize(QSize(THUMB_W, THUMB_H))
    return t


def accept_file_drops(widget, on_files, on_hover=None):
    """Permite soltar archivos sobre un widget; on_files recibe la lista de rutas y
    on_hover(True/False) avisa mientras se arrastran encima."""
    widget.setAcceptDrops(True)

    def enter(e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if on_hover:
                on_hover(True)

    def leave(_e):
        if on_hover:
            on_hover(False)

    def drop(e):
        if on_hover:
            on_hover(False)
        on_files([os.path.normpath(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])

    widget.dragEnterEvent = enter
    widget.dragLeaveEvent = leave
    widget.dropEvent = drop


def card(*widgets, margins=(18, 16, 18, 16), spacing=10, name='card'):
    f = QFrame()
    f.setObjectName(name)
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
    lay.setContentsMargins(40, 32, 40, 28)
    lay.setSpacing(14)
    return lay


class PathRow(QWidget):
    def __init__(self, placeholder, mode='dir', filter_='', parent=None):
        super().__init__(parent)
        self.mode = mode
        self.filter = filter_
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        btn = button('Elegir…', 'folder')
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
        lay.setSpacing(6)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setMinimumHeight(160)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(12)
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


class ProgressPanel(QFrame):
    """Tarjeta con porcentaje grande, barra y línea de datos; oculta mientras no hay nada en marcha."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.pct = QLabel('0 %')
        self.pct.setObjectName('statValue')
        self.phase = QLabel('')
        self.phase.setObjectName('cardTitle')
        top.addWidget(self.phase, 1, Qt.AlignBottom)
        top.addWidget(self.pct, 0, Qt.AlignBottom)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.stats = QLabel('')
        self.stats.setObjectName('hint')
        lay.addLayout(top)
        lay.addWidget(self.bar)
        lay.addWidget(self.stats)
        self.setVisible(False)

    def set(self, p, text=None, phase=None):
        self.setVisible(True)
        if p is None:                       # sin porcentaje conocido: barra animada
            self.bar.setRange(0, 0)
            self.pct.setText('')
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(p * 1000))
            self.pct.setText('%.0f %%' % (p * 100))
        if text is not None:
            self.stats.setText(text)
        if phase is not None:
            self.phase.setText(phase)


def status_item(text, color, sort_key=None):
    """Celda de estado: se dibuja como etiqueta de color (PillDelegate)."""
    it = SortItem(text)
    it.setData(PILL_ROLE, (text, color))
    it.setToolTip(text)
    if sort_key is not None:
        it.setData(SORT_ROLE, sort_key)
    return it


_THUMB_CACHE = {}


def thumb_for(path, icon_name, color, w=THUMB_W, h=THUMB_H):
    """Miniatura de una imagen (o, si no la hay, un recuadro con el icono del tipo)."""
    key = (path, icon_name, w, h)
    if key not in _THUMB_CACHE:
        _THUMB_CACHE[key] = (load_thumb(path, w, h) if path else None) or \
            thumb_placeholder(icon_name, color, w, h)
    return _THUMB_CACHE[key]


def thumb_icon(path, icon_name, color):
    """Icono de celda con la miniatura; igual al seleccionar la fila (sin el tinte de Qt)."""
    pm = thumb_for(path, icon_name, color)
    ic = QIcon()
    for mode in (QIcon.Normal, QIcon.Selected, QIcon.Active):
        ic.addPixmap(pm, mode)
    return ic


def kind_look(ev):
    """(icono, color, nombre) según el tipo de elemento encontrado."""
    if ev.get('kind') == 'rsv':
        return 'cut', CUT, 'Grabación cortada'
    if ev.get('thumb'):
        return 'image', MUTED, 'Miniatura de la cámara'
    if ev.get('kind') in PHOTO_KINDS:
        return 'photo', ACCENT, 'Foto'
    return 'video', VIDEO, 'Vídeo'


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
                                  'Primero se explora la tarjeta y se muestra todo lo que se puede '
                                  'recuperar; después eliges qué guardar. En la tarjeta nunca se escribe '
                                  'nada.', 'rescue'))
        head = QHBoxLayout()
        head.addWidget(section('1 · Tarjeta'), 1)
        refresh = button('Actualizar', 'refresh', 'ghost')
        refresh.clicked.connect(self.refresh_drives)
        img = button('Usar imagen de disco…', 'disk', 'ghost')
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
        kinds.setSpacing(12)
        self.want_video = ChoiceCard('video', 'Vídeo', 'XAVC S, HS y S-I, MXF y grabaciones cortadas '
                                     '(batería, tarjeta extraída).', color=VIDEO)
        self.want_photo = ChoiceCard('photo', 'Fotos', 'RAW (ARW), HEIF y JPG, con vista previa.')
        for cb in (self.want_video, self.want_photo):
            cb.toggled.connect(self._validate)
            kinds.addWidget(cb, 1)
        lay.addLayout(kinds)

        lay.addWidget(section('3 · Dónde guardar'))
        self.out = PathRow('Carpeta en otro disco')
        self.out.setText(default_output())
        self.out.edit.textChanged.connect(self._validate)
        lay.addWidget(card(self.out, hint('Debe estar en otro disco, nunca en la tarjeta que se rescata.')))

        self.ref = PathRow('(opcional) carpeta con clips sanos de la misma cámara')
        self.cmp = PathRow('(opcional) carpeta con copias de los archivos para verificar')
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(10)
        g.addWidget(QLabel('Referencias para reparar'), 0, 0)
        g.addWidget(self.ref, 0, 1)
        g.addWidget(QLabel('Verificar contra carpeta'), 1, 0)
        g.addWidget(self.cmp, 1, 1)
        adv = card(g, hint('Sin referencias, los clips incompletos y las grabaciones cortadas se reparan '
                           'con clips sanos de la propia tarjeta. Con «Verificar», la búsqueda se detiene '
                           'al encontrar todos esos archivos y comprueba que los recuperados son '
                           'idénticos byte a byte (SHA-256).'))
        lay.addWidget(Disclosure('Opciones avanzadas', adv), 0, Qt.AlignLeft)
        lay.addWidget(adv)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.summary = QLabel('')
        self.summary.setObjectName('hint')
        self.summary.setWordWrap(True)
        bottom.addWidget(self.summary, 1)
        self.go_hint = QLabel('')
        self.go_hint.setStyleSheet('color: %s;' % WARN)
        bottom.addWidget(self.go_hint)
        self.go = button('Explorar tarjeta', 'play', 'primary')
        self.go.setMinimumWidth(190)
        self.go.clicked.connect(self.start)
        bottom.addWidget(self.go)
        lay.addWidget(card(bottom, margins=(18, 12, 12, 12), name='footer'))
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
        d = self.drives.selected()
        if not d:
            why = 'Elige una tarjeta o una imagen de disco.'
        elif not (self.want_video.isChecked() or self.want_photo.isChecked()):
            why = 'Marca vídeo, fotos o ambos.'
        elif not self.out.text():
            why = 'Indica dónde guardar lo recuperado.'
        else:
            why = ''
        self.go_hint.setText(why)
        self.go.setEnabled(not why)
        if d:
            what = ' y '.join(w for w, cb in (('vídeo', self.want_video), ('fotos', self.want_photo))
                              if cb.isChecked()) or 'nada'
            name = os.path.basename(d['id']) if d.get('image') else d['id']
            self.summary.setText('Se buscará %s en %s%s, sin modificarla.' % (
                what, name, ' (%s)' % human_size(d['size']) if d.get('size') else ''))
        else:
            self.summary.setText('')

    # ---- exploración, selección y recuperación
    C_CHECK, C_NAME, C_CAM, C_DUR, C_FMT, C_PROF, C_SIZE, C_STATE = range(8)

    def _run_view(self):
        w = QWidget()
        w.setObjectName('page')
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 30, 40, 24)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.run_header = page_header('Explorando…', '', 'rescue')
        self.run_title, self.run_sub = self.run_header.title, self.run_header.subtitle
        self.run_sub.setTextFormat(Qt.PlainText)   # los mensajes llevan rutas con < y &
        head.addWidget(self.run_header, 1)
        self.stop_btn = button('Detener exploración', 'stop', 'danger')
        self.stop_btn.clicked.connect(self.stop)
        self.cancel_btn = button('Cancelar', None, 'ghost')
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = button('Abrir carpeta', 'folder')
        self.open_btn.clicked.connect(lambda: open_path(self.out_dir))
        self.new_btn = button('Nueva búsqueda', 'refresh', 'primary')
        self.new_btn.clicked.connect(self.new_search)
        for b in (self.stop_btn, self.cancel_btn, self.open_btn, self.new_btn):
            head.addWidget(b, 0, Qt.AlignTop)
        lay.addLayout(head)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.st_video = StatTile('video', 'Vídeos', VIDEO)
        self.st_photo = StatTile('photo', 'Fotos', ACCENT)
        self.st_cut = StatTile('cut', 'Cortados o incompletos', CUT)
        self.st_size = StatTile('download', 'Seleccionado', OK)
        for t in (self.st_video, self.st_photo, self.st_cut, self.st_size):
            stats.addWidget(t, 1)
        lay.addLayout(stats)
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)
        self.banner = Banner()
        self.banner_open = self.banner.add_button(button('Abrir carpeta', 'folder'))
        self.banner_open.clicked.connect(lambda: open_path(self.out_dir))
        self.banner.setVisible(False)
        lay.addWidget(self.banner)

        # filtros y selección
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.f_video = Chip('Vídeos')
        self.f_photo = Chip('Fotos')
        self.f_thumbs = Chip('Miniaturas', False)
        self.f_incomplete = Chip('Incompletos')
        for cb in (self.f_video, self.f_photo, self.f_thumbs, self.f_incomplete):
            cb.toggled.connect(self._apply_filter)
            bar.addWidget(cb)
        self.f_thumbs.setToolTip('JPG pequeños que la cámara crea para cada clip (no son fotos)')
        self.f_incomplete.setToolTip('Clips sin índice y grabaciones cortadas: se reparan al recuperarlos')
        self.f_date = QComboBox()
        self.f_date.setMinimumWidth(180)
        self.f_date.addItem('Todas las fechas', '')
        self.f_date.currentIndexChanged.connect(self._apply_filter)
        bar.addSpacing(6)
        bar.addWidget(self.f_date)
        bar.addStretch(1)
        self.sel_all = button('Marcar visibles', 'check', 'ghost')
        self.sel_all.clicked.connect(lambda: self._check_visible(True))
        self.sel_none = button('Ninguno', None, 'ghost')
        self.sel_none.clicked.connect(lambda: self._check_visible(False))
        bar.addWidget(self.sel_all)
        bar.addWidget(self.sel_none)
        self.filter_bar = QWidget()
        self.filter_bar.setLayout(bar)
        lay.addWidget(self.filter_bar)

        self.table = data_table(self.COLS)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setItemDelegateForColumn(self.C_STATE, PillDelegate(self.table))
        h = self.table.horizontalHeader()
        h.setMinimumSectionSize(40)
        h.setSortIndicatorShown(True)
        for col, width in ((self.C_CHECK, 44), (self.C_CAM, 70), (self.C_DUR, 76), (self.C_FMT, 150),
                           (self.C_PROF, 150), (self.C_SIZE, 80), (self.C_STATE, 170)):
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

        # ficha del elemento elegido: vista previa y datos
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview.setObjectName('preview')
        self.detail_title = QLabel()
        self.detail_title.setObjectName('cardTitle')
        self.detail_title.setTextFormat(Qt.PlainText)
        self.detail_badge = QLabel()
        self.details = InfoGrid(columns=2)
        self.detail_note = QLabel()
        self.detail_note.setObjectName('hint')
        self.detail_note.setWordWrap(True)
        self.detail_note.setTextFormat(Qt.PlainText)
        self.detail_open = button('Abrir', 'play')
        self.detail_open.clicked.connect(lambda: open_path(self._selected_ev().get('path')))
        self.detail_folder = button('Mostrar en la carpeta', 'folder', 'ghost')
        self.detail_folder.clicked.connect(
            lambda: open_path(os.path.dirname(self._selected_ev().get('path') or '')))
        info = QVBoxLayout()
        info.setSpacing(8)
        trow = QHBoxLayout()
        trow.setSpacing(10)
        trow.addWidget(self.detail_title, 1)
        trow.addWidget(self.detail_badge)
        info.addLayout(trow)
        info.addWidget(self.details)
        info.addWidget(self.detail_note)
        info.addStretch(1)
        brow = QHBoxLayout()
        brow.setSpacing(8)
        brow.addWidget(self.detail_open)
        brow.addWidget(self.detail_folder)
        brow.addStretch(1)
        info.addLayout(brow)
        prow = QHBoxLayout()
        prow.setSpacing(18)
        prow.addWidget(self.preview, 0, Qt.AlignTop)
        prow.addLayout(info, 1)
        self.preview_card = card(prow, margins=(12, 12, 18, 12))
        self.preview_card.setVisible(False)
        lay.addWidget(self.preview_card)

        self.log = LogPanel()
        lay.addWidget(self.log)
        foot = QHBoxLayout()
        foot.setSpacing(16)
        self.by_date = QCheckBox('Separar en carpetas por fecha')
        self.by_date.setToolTip('Guarda cada archivo en una subcarpeta AAAA-MM-DD según la fecha de la cámara')
        self.sel_label = QLabel('')
        self.sel_label.setObjectName('cardTitle')
        self.recover_btn = button('Recuperar seleccionados', 'download', 'primary')
        self.recover_btn.setMinimumWidth(230)
        self.recover_btn.clicked.connect(self.recover)
        foot.addWidget(self.by_date)
        foot.addStretch(1)
        foot.addWidget(self.sel_label)
        foot.addWidget(self.recover_btn)
        self.footer = card(foot, margins=(18, 10, 10, 10), name='footer')
        lay.addWidget(self.footer)
        return w

    def _set_mode(self, mode):
        """scan: explorando · choose: eligiendo · extract: recuperando · done: terminado."""
        self.mode = mode
        self.stop_btn.setVisible(mode == 'scan')
        self.stop_btn.setEnabled(True)
        self.cancel_btn.setVisible(mode in ('scan', 'choose'))
        self.open_btn.setVisible(mode == 'extract')
        self.new_btn.setVisible(mode == 'done')
        self.filter_bar.setVisible(mode in ('scan', 'choose'))
        self.footer.setVisible(mode in ('scan', 'choose'))
        self.progress.setVisible(mode != 'done' and self.progress.isVisible())
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
        self.banner.setVisible(False)
        self.log.clear()
        self.progress.setVisible(False)
        self.found = {'video': 0, 'photo': 0, 'thumb': 0, 'video_bytes': 0, 'broken': 0}
        self.saved_v = self.saved_p = self.repaired = self.verified = 0
        self._refresh_stats()
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
        if self.mode not in ('scan', 'extract'):
            return
        el = time.monotonic() - self.t0
        total = self.source_size if self.mode == 'scan' else self.extract_bytes
        parts = []
        if total:
            parts.append('%s de %s' % (human_size(total * p), human_size(total)))
            if el > 3 and 0.002 < p < 1:
                parts.append('%s/s' % human_size(total * p / el))
                parts.append('quedan ~%s' % human_time(el / p * (1 - p)))
        if self.mode == 'extract':
            parts.append('%d de %d recuperados' % (self.saved_v + self.saved_p, self.extract_count))
        self.progress.set(p, '   ·   '.join(parts),
                          'Leyendo la tarjeta' if self.mode == 'scan' else 'Guardando lo elegido')

    def _found_text(self):
        f = self.found
        parts = ['%s (%s)' % (plural(f['video'], 'vídeo'), human_size(f['video_bytes'])),
                 plural(f['photo'], 'foto')]
        if f['broken']:
            parts.append('%d cortados o incompletos' % f['broken'])
        return ' · '.join(parts)

    def _refresh_stats(self):
        f = self.found
        self.st_video.set(f['video'])
        self.st_photo.set(f['photo'])
        self.st_cut.set(f['broken'])
        chosen = self._chosen() if self.table.rowCount() else []
        self.st_size.set(human_size(sum(e.get('size') or 0 for e in chosen)),
                         'Seleccionado · %s' % plural(len(chosen), 'archivo'))
        counts = {'video': 0, 'photo': 0, 'thumb': 0, 'broken': 0}
        for r in range(self.table.rowCount()):
            ev = self.table.item(r, self.C_CHECK).data(Qt.UserRole)
            photo = ev.get('kind') in PHOTO_KINDS
            key = 'thumb' if ev.get('thumb') else 'photo' if photo else 'video'
            counts[key] += 1
            if not ev.get('complete') and not ev.get('thumb'):
                counts['broken'] += 1
        self.f_video.set_count(counts['video'])
        self.f_photo.set_count(counts['photo'])
        self.f_thumbs.set_count(counts['thumb'])
        self.f_incomplete.set_count(counts['broken'])

    # ---- eventos del motor
    def _on_event(self, ev):
        t = ev.get('t')
        if t == 'item':
            self._add_item(ev)
        elif t == 'scan_done':
            self.run_title.setText('Encontrado en %s' % self.source_name)
            self.run_sub.setText('Marca lo que quieras recuperar y pulsa «Recuperar seleccionados». '
                                 'Todavía no se ha guardado nada.')
            self.progress.setVisible(False)
            self._refresh_days()
            self._set_mode('choose')
        elif t == 'phase' and ev.get('phase') == 'extract':
            self.t0 = time.monotonic()
            self.progress.set(0.0, '', 'Guardando lo elegido')
            self._set_mode('extract')
        elif t == 'phase' and ev.get('phase') == 'repair':
            self.progress.set(None, 'Se usan como referencia clips sanos de la tarjeta (o los indicados) '
                              'y se comprueba que el resultado se reproduce.',
                              'Reparando %s' % plural(ev.get('count') or 0, 'clip'))
            self.run_sub.setText('Reparando los clips incompletos y las grabaciones cortadas…')

    def _add_item(self, ev):
        photo = ev.get('kind') in PHOTO_KINDS
        thumb = bool(ev.get('thumb'))
        broken = not ev.get('complete') and not thumb
        if thumb:
            self.found['thumb'] += 1
            if ev.get('fecha_seg') and ev.get('preview'):
                self.thumbs.append((ev['fecha_seg'], ev['preview']))
                self._attach_camera_thumb(ev)
        elif photo:
            self.found['photo'] += 1
        else:
            self.found['video'] += 1
            self.found['video_bytes'] += ev.get('size') or 0
        if broken:
            self.found['broken'] += 1
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
        # lo completo y las grabaciones cortadas (lo que se quiere salvar tras quitar la batería)
        wanted = (ev.get('complete') and not thumb) or ev.get('kind') == 'rsv'
        check.setCheckState(Qt.Checked if wanted else Qt.Unchecked)
        icon_name, color, kind_name = kind_look(ev)
        check.setData(Qt.UserRole, ev)
        check.setData(SORT_ROLE, 0 if not photo else 2 if thumb else 1)
        check.setToolTip(kind_name)
        name = SortItem(ev.get('name', ''))
        name.setToolTip(ev.get('note', '') or kind_name)
        name.setData(SORT_ROLE, ev.get('fecha_seg') or ev.get('name', ''))
        source = ev.get('preview') if photo else self._thumb_for(ev)
        name.setIcon(thumb_icon(source, icon_name, color))
        cam = SortItem(camera_name(ev.get('camara')))
        cam.setToolTip(ev.get('camara') or '')
        dur = SortItem(human_time(ev.get('duracion')) if not photo else '—')
        dur.setData(SORT_ROLE, ev.get('duracion') or 0)
        size = SortItem(human_size(ev.get('size') or 0))
        size.setData(SORT_ROLE, ev.get('size') or 0)
        if thumb:
            state = status_item('Miniatura', FAINT, 9)
        elif ev.get('kind') == 'rsv':
            state = status_item('Grabación cortada', CUT, 2)
        else:
            state = status_item('Encontrado' if ev.get('complete') else 'Incompleto',
                                MUTED if ev.get('complete') else WARN, 1 if ev.get('complete') else 3)
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

    def _attach_camera_thumb(self, thumb_ev):
        """Una miniatura de la cámara recién encontrada: se la pone a su clip si ya está en la tabla."""
        when = parse_when(thumb_ev.get('fecha_seg'))
        if when is None:
            return
        for r in range(self.table.rowCount()):
            ev = self.table.item(r, self.C_CHECK).data(Qt.UserRole)
            if ev.get('kind') in PHOTO_KINDS or ev.get('kind') == 'rsv':
                continue
            t = parse_when(ev.get('fecha_seg'))
            if t is not None and abs((t - when).total_seconds()) <= 3:
                self.table.item(r, self.C_NAME).setIcon(
                    thumb_icon(thumb_ev['preview'], 'video', VIDEO))

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
        self.sel_label.setText('%s · %s' % (plural(len(chosen), 'seleccionado'),
                                            human_size(sum(e.get('size') or 0 for e in chosen))))
        self.recover_btn.setEnabled(self.mode == 'choose' and bool(chosen))
        self.recover_btn.setToolTip('' if self.mode == 'choose' else
                                    'Disponible al terminar la exploración (o pulsa «Detener exploración»).')
        self._refresh_stats()

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
                self.table.setItem(r, self.C_STATE, status_item('Pendiente', MUTED, 5))
            else:
                self.table.setItem(r, self.C_STATE, status_item('Omitido', FAINT, 9))
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
        repaired = ev.get('note', '').startswith('reparado')
        if repaired:
            self.repaired += 1
            self.verified += 1 if ev.get('verificado') else 0
        elif ev.get('kind') in PHOTO_KINDS:
            self.saved_p += 1
        else:
            self.saved_v += 1
        if ev.get('identico_a'):
            state, color = 'Idéntico a ' + ev['identico_a'], OK
        elif repaired:
            state, color = {True: ('Reparado y verificado', OK), False: ('Reparado con errores', WARN),
                            None: ('Reparado', OK)}[ev.get('verificado')]
        else:
            state, color = ('Recuperado', OK) if ev.get('complete') else ('Guardado sin reparar', WARN)
        it = self.table.item(r, self.C_CHECK)
        old = it.data(Qt.UserRole)
        it.setData(Qt.UserRole, dict(old, path=ev.get('path'), complete=ev.get('complete'),
                                     verificado=ev.get('verificado'), referencia=ev.get('referencia'),
                                     preview=ev.get('preview') or old.get('preview'),
                                     duracion=ev.get('duracion') or old.get('duracion'),
                                     codec=ev.get('codec') or old.get('codec'),
                                     perfil=ev.get('perfil') or old.get('perfil')))
        name = self.table.item(r, self.C_NAME)
        name.setText(os.path.basename(ev.get('path', '')))
        name.setToolTip(ev.get('path', ''))
        if ev.get('preview') and ev.get('kind') not in PHOTO_KINDS:
            name.setIcon(thumb_icon(ev['preview'], 'video', VIDEO))
        if repaired:
            self.table.item(r, self.C_DUR).setText(human_time(ev.get('duracion')))
            self.table.item(r, self.C_FMT).setText(ev.get('codec') or old.get('codec') or '—')
        self.table.setItem(r, self.C_STATE, status_item(state, color, 0))
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

    def _selected_ev(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return {}
        return self.table.item(rows[0].row(), self.C_CHECK).data(Qt.UserRole) or {}

    def _show_preview(self):
        ev = self._selected_ev()
        if not ev:
            return
        photo = ev.get('kind') in PHOTO_KINDS
        icon_name, color, kind_name = kind_look(ev)
        source = ev.get('preview') if (photo or ev.get('path')) else self._thumb_for(ev)
        source = source or (None if photo else self._thumb_for(ev))
        pm = load_thumb(source, PREVIEW_W, PREVIEW_H) if source else None
        self.preview.setPixmap(pm or thumb_placeholder(icon_name, color, PREVIEW_W, PREVIEW_H))
        self.detail_title.setText(os.path.basename(ev.get('path') or ev.get('name', '')))
        self.detail_badge.setText(kind_name)
        self.detail_badge.setStyleSheet('color: %s; font-weight: 700; font-size: 11px;' % color)
        cam = camera_name(ev.get('camara'))
        self.details.set_rows([
            ('Formato', ev.get('codec')), ('Duración', None if photo else
                                           human_time(ev.get('duracion')) if ev.get('duracion') else None),
            ('Perfil', ev.get('perfil')), ('Cámara', cam if cam != '—' else None),
            ('Fecha', ev.get('fecha_seg') or ev.get('fecha')), ('Tamaño', human_size(ev.get('size') or 0)),
            ('Referencia', ev.get('referencia')),
        ])
        if ev.get('path'):
            note = ev['path']
        elif ev.get('thumb'):
            note = 'Miniatura que la cámara crea para un clip; no es una foto.'
        elif ev.get('kind') == 'rsv':
            note = ('Grabación que se cortó (batería, tarjeta extraída): no tiene cabecera ni índice. Al '
                    'recuperarla se repara con un clip sano de la misma tarjeta.')
        elif not ev.get('complete'):
            note = 'Incompleto: %s. Se intentará reparar al recuperarlo.' % ev.get('note', '')
        else:
            note = 'Aún no recuperado.' + ('' if photo or source else
                                          ' Este clip no tiene miniatura de la cámara en la tarjeta.')
        self.detail_note.setText(note)
        self.detail_open.setVisible(bool(ev.get('path')))
        self.detail_folder.setVisible(bool(ev.get('path')))
        self.preview_card.setVisible(True)

    def _on_finished(self, ok, msg):
        mode = getattr(self, 'mode', 'scan')
        if mode in ('scan', 'choose') and ok:
            self.stack.setCurrentIndex(0)       # cancelado antes de recuperar
            return
        self._set_mode('done')
        self.progress.setVisible(False)
        self.banner.setVisible(True)
        if ok:
            got = [plural(n, w) for n, w in ((self.saved_v, 'vídeo'), (self.saved_p, 'foto')) if n]
            ending = ('a' if not self.saved_v else 'o') + ('' if self.saved_v + self.saved_p == 1 else 's')
            title = ('%s recuperad%s' % (' y '.join(got), ending)) if got else 'No se recuperó nada'
            extra = ''
            if self.repaired:
                extra = ' %s; %d verificado%s con ffmpeg.' % (
                    plural(self.repaired, 'clip reparado', 'clips reparados'), self.verified,
                    '' if self.verified == 1 else 's')
            self.run_title.setText('Terminado')
            self.run_sub.setText('Doble clic en un archivo para abrirlo.')
            self.banner.set('ok' if got else 'warn', title, 'Están en %s.%s' % (self.out_dir, extra))
            self.banner_open.setVisible(True)
        else:
            self.run_title.setText('No se pudo completar')
            self.run_sub.setText('')
            self.banner.set('bad', 'La operación se detuvo', msg)
            self.banner_open.setVisible(bool(getattr(self, 'out_dir', None)))


# ---------------------------------------------------------------- reparar clip

class RepairPage(QWidget):
    COLS = ['Clip dañado', 'Duración', 'Formato', 'Resultado', 'Estado']
    R_NAME, R_DUR, R_FMT, R_RESULT, R_STATE = range(5)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = page_scaffold(self)
        lay.addWidget(page_header('Reparar clips interrumpidos',
                                  'Para clips que no abren porque la grabación se cortó (batería, tarjeta '
                                  'extraída, apagado), también los RSV. Reconstruye el índice sin '
                                  'recodificar: S-Log3, S-Cinetone y 10 bits se conservan intactos.', 'repair'))
        head = QHBoxLayout()
        head.addWidget(section('1 · Clips dañados'), 1)
        add = button('Añadir clips…', 'video', 'ghost')
        add.clicked.connect(self.add_files)
        self.clear_btn = button('Quitar todos', None, 'ghost')
        self.clear_btn.clicked.connect(lambda: self.table.setRowCount(0))
        head.addWidget(add)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)
        self.table = data_table(self.COLS)
        self.table.setMinimumHeight(200)
        self.table.setItemDelegateForColumn(self.R_STATE, PillDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(self.R_NAME, QHeaderView.Stretch)
        for col, width in ((self.R_DUR, 90), (self.R_FMT, 190), (self.R_RESULT, 240), (self.R_STATE, 190)):
            self.table.setColumnWidth(col, width)
        self.table.cellDoubleClicked.connect(self._open_result)
        self.drop = EmptyState('repair', 'Arrastra aquí los clips que no abren',
                               'MP4 o RSV (el archivo que deja la cámara si se corta la grabación), o usa '
                               '«Añadir clips…». Se reparan uno tras otro con la misma referencia.')
        self.drop.setMinimumHeight(220)
        accept_file_drops(self, self._add, self.drop.set_drag)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.drop)
        self.list_stack.addWidget(self.table)
        self.table.model().rowsInserted.connect(self._update_empty)
        self.table.model().rowsRemoved.connect(self._update_empty)
        self.table.model().modelReset.connect(self._update_empty)
        lay.addWidget(self.list_stack, 1)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(section('2 · Referencia'))
        self.ref = PathRow('Clips sanos: carpeta o archivo')
        self.ref.edit.textChanged.connect(self._update_empty)
        btn_file = button('Un clip…', 'video')
        btn_file.clicked.connect(self._pick_ref_file)
        self.ref.layout().addWidget(btn_file)
        left.addWidget(card(self.ref, hint('Clips sanos de la misma cámara con el mismo formato, resolución, '
                                           'fps y bitrate. Basta con 5–30 s. Si das una carpeta, se elige '
                                           'el que encaja.')), 1)
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(section('3 · Dónde guardar'))
        self.out = PathRow('(por defecto, junto a cada clip dañado)')
        right.addWidget(card(self.out, hint('Cada clip se guarda como «nombre_REPARADO.MP4» y se comprueba '
                                            'que se reproduce.' if ffmpeg.available() else
                                            'Cada clip se guarda como «nombre_REPARADO.MP4».')), 1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lay.addLayout(cols)
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)
        self.log = LogPanel()
        lay.addWidget(self.log)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.summary = QLabel('')
        self.summary.setObjectName('hint')
        row.addWidget(self.summary, 1)
        self.go_hint = QLabel('')
        self.go_hint.setStyleSheet('color: %s;' % WARN)
        row.addWidget(self.go_hint)
        self.go = button('Reparar', 'repair', 'primary')
        self.go.setMinimumWidth(170)
        self.go.clicked.connect(self.start)
        row.addWidget(self.go)
        lay.addWidget(card(row, margins=(18, 12, 12, 12), name='footer'))
        self.queue = []
        self.job = None
        self._update_empty()

    def _state(self, r):
        it = self.table.item(r, self.R_STATE)
        return it.text() if it else ''

    def _pending(self):
        return [r for r in range(self.table.rowCount()) if self._state(r) in ('Pendiente', 'Error')]

    def _update_empty(self, *_):
        self.list_stack.setCurrentIndex(1 if self.table.rowCount() else 0)
        busy = bool(self.job and self.job.running)
        pending = self._pending()
        why = ('' if busy else 'Añade los clips dañados.' if not pending
               else 'Indica la referencia.' if not self.ref.text() else '')
        self.go_hint.setText(why)
        self.go.setEnabled(not busy and not why)
        self.summary.setText('%s por reparar' % plural(len(pending), 'clip') if pending and not busy else '')

    def _pick_ref_file(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Clip de referencia', '', VIDEO_FILTER)
        if p:
            self.ref.setText(os.path.normpath(p))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Clips dañados', '', DAMAGED_FILTER)
        self._add(files)

    def _add(self, files):
        known = {self.table.item(r, self.R_NAME).data(Qt.UserRole) for r in range(self.table.rowCount())}
        for f in files:
            f = os.path.normpath(f)
            if not os.path.isfile(f) or f in known:
                continue
            known.add(f)
            r = self.table.rowCount()
            self.table.insertRow(r)
            cut = f.upper().endswith('.RSV')
            name = QTableWidgetItem(os.path.basename(f))
            name.setData(Qt.UserRole, f)
            name.setToolTip(f)
            name.setIcon(thumb_icon(None, 'cut' if cut else 'video', CUT if cut else VIDEO))
            self.table.setItem(r, self.R_NAME, name)
            for c in (self.R_DUR, self.R_FMT, self.R_RESULT):
                self.table.setItem(r, c, QTableWidgetItem('—' if c != self.R_RESULT else ''))
            self.table.setItem(r, self.R_STATE, status_item('Pendiente', MUTED))
        self._update_empty()

    def _open_result(self, r, _c):
        it = self.table.item(r, self.R_RESULT)
        open_path((it.data(Qt.UserRole) if it else None) or self.table.item(r, self.R_NAME).data(Qt.UserRole))

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
        self.ok_count = 0
        self.total = len(self.queue)
        self._next()

    def _next(self):
        if not self.queue:
            self.job = None
            self.clear_btn.setEnabled(True)
            self._update_empty()
            self.progress.set(1.0, 'Doble clic en un clip para abrirlo.',
                              'Terminado: %d de %s reparados' % (self.ok_count, plural(self.total, 'clip')))
            return
        self.row = self.queue.pop(0)
        src = self.table.item(self.row, self.R_NAME).data(Qt.UserRole)
        out_dir = self.out.text() or os.path.dirname(src)
        out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + '_REPARADO.MP4')
        self.table.setItem(self.row, self.R_STATE, status_item('Reparando…', ACCENT))
        self.log.append('── %s' % src)
        self.job = Job(['reparar', src, '--referencia', self.ref.text(), '-o', out], False, self)
        self.job.log.connect(self.log.append)
        self.job.progress.connect(lambda p: self.progress.set(
            (self.done_count + p) / self.total, os.path.basename(src),
            'Clip %d de %d' % (self.done_count + 1, self.total)))
        self.job.clip.connect(self._on_clip)
        self.job.finished.connect(self._on_finished)
        self.job.start()

    def _on_clip(self, ev):
        self.table.item(self.row, self.R_DUR).setText(human_time(ev.get('duracion')))
        self.table.item(self.row, self.R_FMT).setText(' · '.join(x for x in (ev.get('codec'), ev.get('perfil'))
                                                                 if x) or '—')
        res = self.table.item(self.row, self.R_RESULT)
        res.setText(os.path.basename(ev['path']))
        res.setData(Qt.UserRole, ev['path'])
        res.setToolTip(ev['path'])
        res.setData(Qt.UserRole + 1, ev.get('verificado'))
        if ev.get('preview'):
            self.table.item(self.row, self.R_NAME).setIcon(thumb_icon(ev['preview'], 'video', VIDEO))

    def _on_finished(self, ok, msg):
        if ok:
            self.ok_count += 1
            verified = self.table.item(self.row, self.R_RESULT).data(Qt.UserRole + 1)
            text, color = {True: ('Reparado y verificado', OK), False: ('Reparado con errores', WARN),
                           None: ('Reparado', OK)}[verified]
        else:
            text, color = 'Error', BAD
            self.table.item(self.row, self.R_RESULT).setText(msg)
            self.table.item(self.row, self.R_RESULT).setToolTip(msg)
            self.log.append('ERROR: ' + msg)
        self.table.setItem(self.row, self.R_STATE, status_item(text, color))
        self.done_count += 1
        self._next()


# ---------------------------------------------------------------- analizar

class AnalyzePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = page_scaffold(self)
        lay.addWidget(page_header('Analizar un clip',
                                  'Estructura del archivo, si le falta el índice, códec, fps y perfil de '
                                  'imagen (S-Log3, S-Cinetone, HLG…) leído de los metadatos de Sony.', 'analyze'))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.path = PathRow('Clip MP4 / MXF / RSV', mode='file',
                            filter_='Vídeo (*.mp4 *.MP4 *.mxf *.MXF *.mov *.MOV *.rsv *.RSV);;Todos (*)')
        row.addWidget(self.path, 1)
        self.go = button('Analizar', 'analyze', 'primary')
        self.go.clicked.connect(self.run)
        self.path.edit.textChanged.connect(lambda t: self.go.setEnabled(os.path.isfile(t.strip())))
        self.path.edit.returnPressed.connect(self.run)
        self.go.setEnabled(False)
        row.addWidget(self.go)
        lay.addWidget(card(row, margins=(14, 12, 12, 12)))

        # ficha: fotograma, datos y diagnóstico
        self.frame = QLabel()
        self.frame.setObjectName('preview')
        self.frame.setFixedSize(320, 180)
        self.frame.setAlignment(Qt.AlignCenter)
        self.name = QLabel()
        self.name.setObjectName('cardTitle')
        self.name.setTextFormat(Qt.PlainText)
        self.facts = InfoGrid(columns=2)
        self.verdict = Banner('ok')
        info = QVBoxLayout()
        info.setSpacing(10)
        info.addWidget(self.name)
        info.addWidget(self.facts)
        info.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(18)
        top.addWidget(self.frame, 0, Qt.AlignTop)
        top.addLayout(info, 1)
        self.summary = card(top, self.verdict, margins=(12, 12, 18, 14), spacing=12)
        self.summary.setVisible(False)
        lay.addWidget(self.summary)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(320)
        self.empty = EmptyState('analyze', 'Arrastra aquí un clip',
                                'o elígelo arriba. Solo se lee: el archivo no se modifica.')
        self.body = QStackedWidget()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.text)
        accept_file_drops(self, self._dropped, self.empty.set_drag)
        self.report_title = section('Informe técnico')
        self.report_title.setVisible(False)
        lay.addWidget(self.report_title)
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
        self.report_title.setVisible(True)
        self.summary.setVisible(False)
        self.text.setPlainText('Analizando %s…' % os.path.basename(self.path.text()))
        self.go.setEnabled(False)
        self.thread = AnalyzeThread(self.path.text(), self)
        self.thread.result.connect(self._show)
        self.thread.finished.connect(lambda: self.go.setEnabled(True))
        self.thread.start()

    def _show(self, res):
        text = res.get('text', '')
        self.text.setPlainText(text)
        info = res.get('info') or {}
        self.name.setText(os.path.basename(res['path']))
        pm = load_thumb(res.get('frame'), 320, 180)
        self.frame.setPixmap(pm or thumb_placeholder('video', VIDEO, 320, 180))
        try:
            size = human_size(os.path.getsize(res['path']))
        except OSError:
            size = None
        cam = camera_name(info.get('camara'))
        self.facts.set_rows([('Formato', info.get('codec')), ('Perfil', info.get('perfil')),
                             ('Duración', human_time(info.get('duracion')) if info.get('duracion') else None),
                             ('Cámara', cam if cam != '—' else None), ('Fecha', info.get('fecha')),
                             ('Resolución', info.get('resolucion')), ('Tamaño', size)])
        diag = [ln.strip(' •') for ln in text.split('Diagnóstico:', 1)[-1].splitlines() if ln.strip()] \
            if 'Diagnóstico:' in text else []
        healthy = any(k in ' '.join(diag) for k in ('Sin problemas', 'parece correcta'))
        if diag:
            self.verdict.set('ok' if healthy else 'warn',
                             'El archivo está bien' if healthy else 'Necesita reparación',
                             ' '.join(diag))
        else:
            self.verdict.set('warn', 'Resultado', text.splitlines()[-1] if text else '')
        self.summary.setVisible(True)


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
                                  'real), su vida útil si la informa, y repara el formato o la partición.',
                                  'tools'))
        lay.addWidget(Banner('bad', 'La prueba de capacidad, la reparación y el formateo escriben en la '
                                    'tarjeta.',
                             'Después, recuperar los vídeos y fotos borrados de esa tarjeta será imposible. '
                             'Si hay algo que rescatar, usa antes «Rescatar tarjeta».'))

        head = QHBoxLayout()
        head.addWidget(section('Tarjeta'), 1)
        refresh = button('Actualizar', 'refresh', 'ghost')
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        lay.addLayout(head)
        self.drives = DriveList(self._selected_changed)
        lay.addWidget(self.drives)
        self.info = QLabel('Elige una tarjeta para ver su información.')
        self.info.setObjectName('hint')
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        irow = QHBoxLayout()
        irow.setSpacing(14)
        irow.addWidget(icon_tile('card', 40), 0, Qt.AlignTop)
        irow.addWidget(self.info, 1)
        self.info_card = card(irow)
        lay.addWidget(self.info_card)

        lay.addWidget(section('Herramientas'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.cards = [
            ActionCard('Información y vida útil', 'Modelo, particiones, formato y desgaste si la tarjeta '
                       'o el lector lo informan (CFexpress suele hacerlo; SD por USB casi nunca).',
                       'No borra', OK, 'Consultar', self.do_info, icon_name='about'),
            ActionCard('Prueba de lectura completa', 'Lee toda la tarjeta buscando sectores ilegibles y '
                       'zonas lentas. Pide permiso de Administrador.', 'No borra', OK, 'Iniciar',
                       self.do_surface, icon_name='analyze'),
            ActionCard('Capacidad y velocidad real', 'Llena el espacio libre y lo verifica sin caché: '
                       'detecta tarjetas falsas, datos corruptos y la clase real (V30/V60/V90).',
                       'Sobrescribe lo borrado', WARN, 'Iniciar', self.do_capacity, icon_name='tools'),
            ActionCard('Reparar sistema de archivos', 'Corrige errores del sistema de archivos (chkdsk / '
                       'diskutil) sin borrar los archivos visibles.', 'Modifica la tarjeta', WARN,
                       'Reparar', self.do_fsrepair, icon_name='shield'),
            ActionCard('Formatear y rehacer partición', 'Deja la tarjeta como nueva: MBR, alineación a 4 MB '
                       'y FAT32 / exFAT como pide la SD Association.', 'Borra todo', BAD, 'Formatear…',
                       self.do_format, danger=True, icon_name='alert'),
        ]
        for i, c in enumerate(self.cards):
            grid.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid)

        self.status = QLabel('')
        self.status.setObjectName('cardTitle')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)   # los mensajes de error llevan rutas
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.pct = QLabel('')
        self.pct.setObjectName('hint')
        stop_row = QHBoxLayout()
        stop_row.addWidget(self.status, 1)
        self.stop_btn = button('Detener', 'stop', 'danger')
        self.stop_btn.clicked.connect(lambda: self.job and self.job.stop())
        stop_row.addWidget(self.stop_btn)
        self.result = Banner('ok')
        self.result.setVisible(False)
        self.log = LogPanel()
        self.run_card = card(stop_row, self.progress, self.pct, self.result, self.log)
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
        self.pct.setText('')
        self.result.setVisible(False)
        self.status.setText(title + ('  ·  esperando permiso de Administrador…'
                                     if elevate and not devices.is_admin() else '…'))
        self.stop_btn.setVisible(True)
        self.run_card.setVisible(args[0] != 'tarjeta')
        self.job = Job(args, elevate, self)
        self.job.log.connect(self.log.append)
        self.job.progress.connect(self._on_progress)
        self.job.result.connect(self._on_result)
        self.job.finished.connect(self._on_finished)
        self._set_enabled()
        try:
            self.job.start()
        except PermissionError as e:
            self._on_finished(False, str(e))
        self._set_enabled()

    def _on_progress(self, p):
        self.progress.setVisible(True)
        self.progress.setValue(int(p * 1000))
        self.pct.setText('%.0f %%' % (p * 100))

    def do_info(self):
        self.info.setText('Leyendo información de %s…' % esc(self._sel()['id']))
        self._run(['tarjeta', self._sel()['id']], 'Información')

    def do_surface(self):
        self._run(['probar-lectura', self._sel()['id']], 'Prueba de lectura completa', elevate=True)

    def _disclaimer(self, title, what):
        """Aviso que hay que aceptar marcando la casilla. True si el usuario acepta."""
        d = self._sel()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText('<b>%s escribe en %s (%s, %s).</b>' % (what, esc(d['id']), esc(d['name']),
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
            self.info.setText(
                '<span style="color:#fff;font-size:14px;font-weight:600">%s &nbsp;%s</span>'
                '&nbsp;&nbsp;<span style="color:%s">%s</span><br><span style="color:%s">%s</span><br>'
                '<span style="color:%s"><b>Vida útil:</b> %s</span>'
                % (esc(c['id']), esc(c['model']), MUTED, human_size(c['size'] or 0), MUTED, esc(line),
                   TEXT, esc(h['conclusion'])))
        elif 'veredicto' in data:
            v = data['veredicto']
            title = {'correcta': 'Tarjeta correcta', 'falsa': 'TARJETA FALSA',
                     'errores': 'Tarjeta con DATOS CORRUPTOS', 'incompleta': 'Prueba incompleta'}[v]
            self.result_text = ('ok' if v == 'correcta' else 'warn' if v == 'incompleta' else 'bad', title,
                                'Escritura %.0f MB/s (mínima %.0f) · lectura %.0f MB/s · clase real: %s' % (
                                    data['escritura_media'], data['escritura_minima'],
                                    data['lectura_media'], data.get('clase') or 'ninguna'))
        elif 'zonas_ilegibles' in data:
            bad = data['zonas_ilegibles']
            self.result_text = (
                'bad' if bad else 'ok',
                '%d zonas ilegibles (%s)' % (len(bad), human_size(data['bytes_ilegibles'])) if bad
                else 'Sin sectores dañados',
                'Lectura media %.0f MB/s, mínima %.0f MB/s' % (data.get('lectura_media', 0),
                                                               data.get('lectura_min', 0)))
        elif 'fs' in data:
            self.result_text = ('ok', 'Formateada en %s (clúster %s)' % (data['fs'], data['cluster']),
                                'Formatéala también en la cámara antes de grabar.')

    def _on_finished(self, ok, msg):
        self.stop_btn.setVisible(False)
        self._set_enabled()
        summary, self.result_text = self.result_text, None
        if self.current == 'Información':
            if not ok:
                self.info.setText('No se pudo leer la información: %s' % esc(msg))
            return
        self.status.setText(self.current)
        self.result.setVisible(True)
        if ok:
            self.progress.setValue(1000)
            self.pct.setText('100 %')
            self.result.set(*(summary or ('ok', 'Hecho', '')))
        else:
            self.result.set('bad', 'No se pudo completar', msg)


# ---------------------------------------------------------------- acerca de

class AboutPage(QWidget):
    FEATURES = [
        ('rescue', 'Rescate sin escribir', 'Explora la tarjeta o una imagen de disco y recupera solo lo '
                                          'que eliges, aunque se haya formateado.'),
        ('cut', 'Grabaciones cortadas', 'Detecta lo que quedó al quitar la batería o sacar la tarjeta y lo '
                                       'repara con clips sanos de la misma tarjeta.'),
        ('shield', 'Reparación verificada', 'Reconstruye el índice sin recodificar y comprueba con ffmpeg '
                                            'que el clip se reproduce.'),
        ('tools', 'Herramientas de tarjeta', 'Tarjetas falsas, sectores dañados, velocidad real y formateo '
                                             'como pide la SD Association.'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = page_scaffold(self)
        hero = QHBoxLayout()
        hero.setSpacing(22)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(84))
        hero.addWidget(mark, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(6)
        t = QLabel('MD55 Rescue')
        t.setObjectName('title')
        ver = QHBoxLayout()
        ver.setSpacing(8)
        ver.addWidget(uikit.Badge('Versión %s' % __version__, ACCENT))
        ver.addWidget(uikit.Badge('ffmpeg incluido' if ffmpeg.available() else 'Sin ffmpeg',
                                  OK if ffmpeg.available() else FAINT))
        ver.addStretch(1)
        s = QLabel('Recuperación de vídeo y fotos para cámaras Sony: XAVC S / HS / S-I, MXF, RAW (ARW), '
                   'HEIF y JPG. Copia los datos originales sin recodificar.')
        s.setObjectName('subtitle')
        s.setWordWrap(True)
        words.addWidget(t)
        words.addLayout(ver)
        words.addWidget(s)
        hero.addLayout(words, 1)
        lay.addWidget(card(hero, margins=(24, 22, 24, 22)))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, (ic, title, text) in enumerate(self.FEATURES):
            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(icon_tile(ic, 40), 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(3)
            tl = QLabel(title)
            tl.setObjectName('cardTitle')
            col.addWidget(tl)
            col.addWidget(hint(text))
            row.addLayout(col, 1)
            grid.addWidget(card(row), i // 2, i % 2)
        lay.addLayout(grid)

        tips = QLabel(
            '<p style="color:#fff"><b>Buenas prácticas</b></p><ul style="color:#c9ccd3">'
            '<li>Deja de grabar en la tarjeta afectada en cuanto notes el problema.</li>'
            '<li>Guarda siempre lo recuperado en otro disco.</li>'
            '<li>Si el sistema ofrece formatear la tarjeta, pulsa Cancelar.</li>'
            '<li>Cuantas menos veces conectes la tarjeta antes de rescatarla, mejor: el sistema escribe '
            'en ella al conectarla.</li>'
            '<li>Para reparar un clip, graba uno de referencia con la misma cámara y configuración '
            '(si en la tarjeta hay clips sanos, se usan solos).</li></ul>'
            '<p style="color:#fff"><b>Límites</b></p><p style="color:#c9ccd3">Un formateo completo '
            '(sobrescritura) no es recuperable. Lo que se haya sobrescrito después de borrar o formatear '
            'tampoco. Los archivos muy fragmentados pueden salir incompletos.</p>')
        tips.setWordWrap(True)
        tips.setTextFormat(Qt.RichText)
        lay.addWidget(card(tips, margins=(22, 8, 22, 12)))
        if ffmpeg.available():
            lay.addWidget(hint('Incluye ffmpeg (https://ffmpeg.org) con licencia LGPL v2.1: se usa como '
                               'programa aparte para verificar los clips y sacar fotogramas.'))
        lay.addStretch(1)


# ---------------------------------------------------------------- ventana

class MainWindow(QMainWindow):
    PAGES = [('rescue', 'Rescatar tarjeta'), ('repair', 'Reparar clip'), ('analyze', 'Analizar clip'),
             ('tools', 'Herramientas de tarjeta'), ('about', 'Acerca de')]

    def __init__(self):
        super().__init__()
        self.setWindowTitle('MD55 Rescue')
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1040, 700)
        geometry = settings().value('ventana/geometria')
        if geometry:
            self.restoreGeometry(geometry)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setObjectName('side')
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 26, 0, 18)
        sl.setSpacing(16)
        brand = QHBoxLayout()
        brand.setContentsMargins(24, 0, 12, 10)
        brand.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(34))
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel('MD55 Rescue')
        name.setObjectName('brand')
        sub = QLabel('Recuperación para Sony')
        sub.setObjectName('brandSub')
        names.addWidget(name)
        names.addWidget(sub)
        brand.addWidget(mark)
        brand.addLayout(names)
        brand.addStretch(1)
        sl.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setCursor(Qt.PointingHandCursor)
        for i, (key, label) in enumerate(self.PAGES):
            item = QListWidgetItem(uikit.nav_icon(key), '  ' + label)
            item.setToolTip('%s  ·  Ctrl+%d' % (label, i + 1))
            item.setSizeHint(QSize(0, 46))
            self.nav.addItem(item)
            short = QShortcut(QKeySequence('Ctrl+%d' % (i + 1)), self)
            short.activated.connect(lambda row=i: self.nav.setCurrentRow(row))
        sl.addWidget(self.nav, 1)

        status = QVBoxLayout()
        status.setSpacing(7)
        for on, yes, no in ((devices.is_admin(), 'Con permisos de Administrador',
                             'Pedirá permiso al leer la tarjeta'),
                            (ffmpeg.available(), 'Verificación con ffmpeg', 'Sin verificación (falta ffmpeg)')):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel()
            dot.setPixmap(svg_pixmap('ok' if on else 'about', OK if on else DIM, 14))
            txt = QLabel(yes if on else no)
            txt.setObjectName('hint')
            txt.setWordWrap(True)
            row.addWidget(dot, 0, Qt.AlignTop)
            row.addWidget(txt, 1)
            status.addLayout(row)
        ver = QLabel('Versión %s' % __version__)
        ver.setStyleSheet('color: %s; font-size: 11px;' % FAINT)
        status.addWidget(ver)
        box = card(status, margins=(14, 12, 14, 12), spacing=7, name='sideCard')
        holder = QHBoxLayout()
        holder.setContentsMargins(14, 0, 14, 0)
        holder.addWidget(box)
        sl.addLayout(holder)
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
    app.setFont(uikit.app_font())
    app.setStyleSheet(uikit.stylesheet())
    try:
        worker.clean_temp()             # eventos y vistas previas de días anteriores
    except OSError:
        pass
    w = MainWindow()
    w.show()
    return app.exec()
