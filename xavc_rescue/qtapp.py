"""Interfaz de escritorio (Qt / PySide6) para Windows y macOS."""
import os
import sys
import time

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import __version__, analyze, devices, uikit, worker
from .events import EventReader
from .uikit import (ACCENT, BAD, MUTED, OK, WARN, ActionCard, DriveList, human_size, section,
                    svg_pixmap)

PHOTO_KINDS = ('raw', 'heif', 'jpg')


def qss():
    return uikit.stylesheet()


def human_time(s):
    if s is None:
        return '—'
    if s < 60:
        return ('%.1f s' % s).replace('.0 s', ' s')
    s = int(round(s))
    if s >= 3600:
        return '%d h %02d min' % (s // 3600, s % 3600 // 60)
    return '%d min %02d s' % (s // 60, s % 60)


def default_output():
    base = os.path.join(os.path.expanduser('~'), 'Documents', 'MD55 Rescue')
    return os.path.join(base, time.strftime('Rescate_%Y-%m-%d_%H%M'))


def open_path(path):
    if path:
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
    src = src.scaledToHeight(height * 2, Qt.SmoothTransformation)
    pm = QPixmap(src.size())
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), QColor(color))
    p.end()
    pm.setDevicePixelRatio(2)
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
    logo = QPixmap(asset('logo.png'))
    if not logo.isNull():
        logo = logo.scaledToHeight(int(size * 0.68), Qt.SmoothTransformation)
        tinted = QPixmap(logo.size())
        tinted.fill(Qt.transparent)
        q = QPainter(tinted)
        q.drawPixmap(0, 0, logo)
        q.setCompositionMode(QPainter.CompositionMode_SourceIn)
        q.fillRect(tinted.rect(), QColor(ACCENT))
        q.end()
        p.drawPixmap((size - tinted.width()) // 2, (size - tinted.height()) // 2, tinted)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- trabajos en segundo plano

class Job(QObject):
    """Proceso del motor (quizá con permisos de Administrador) leído por eventos."""
    log = Signal(str)
    progress = Signal(float)
    clip = Signal(dict)
    result = Signal(dict)
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

    def _poll(self):
        for ev in self.reader.poll():
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
                return
        if self.proc is not None and self.proc.poll() is not None and not self.done:
            for ev in self.reader.poll():           # el proceso terminó: ¿dejó su resultado?
                if ev.get('t') == 'done':
                    self._finish(ev.get('ok', False), ev.get('msg', ''))
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
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton('▸  Registro detallado')
        self.toggle.setObjectName('link')
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._toggled)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        self.text.setVisible(False)
        self.text.setMinimumHeight(150)
        lay.addWidget(self.toggle, 0, Qt.AlignLeft)
        lay.addWidget(self.text)

    def _toggled(self, on):
        self.text.setVisible(on)
        self.toggle.setText(('▾' if on else '▸') + '  Registro detallado')

    def append(self, msg):
        self.text.appendPlainText(msg)

    def clear(self):
        self.text.clear()


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
    return it


# ---------------------------------------------------------------- rescatar tarjeta

class RescuePage(QWidget):
    COLS = ['', 'Archivo', 'Duración', 'Formato', 'Perfil', 'Tamaño', 'Estado']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('page')
        self.job = None
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
                                  'Recupera lo borrado o perdido tras formatear la tarjeta. Solo se lee '
                                  'la tarjeta: nunca se escribe en ella.'))
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

        self.adv_toggle = QPushButton('▸  Opciones avanzadas')
        self.adv_toggle.setObjectName('link')
        self.adv_toggle.setCheckable(True)
        lay.addWidget(self.adv_toggle, 0, Qt.AlignLeft)
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
        adv.setVisible(False)
        self.adv_toggle.toggled.connect(lambda on: (adv.setVisible(on), self.adv_toggle.setText(
            ('▾' if on else '▸') + '  Opciones avanzadas')))
        lay.addWidget(adv)
        lay.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.go = QPushButton('Buscar')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        bottom.addWidget(self.go)
        lay.addLayout(bottom)

    def refresh_drives(self):
        self.drives.set_drives([d for d in devices.list_drives() if not d['system']],
                               'No se detecta ninguna tarjeta. Conéctala y pulsa «Actualizar».')

    def pick_image(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Imagen de la tarjeta', '',
                                           'Imágenes de disco (*.img *.dd *.bin *.dmg);;Todos (*)')
        if p:
            p = os.path.normpath(p)
            self.drives.add({'id': p, 'name': 'Imagen de disco', 'fs': '', 'size': os.path.getsize(p),
                             'removable': False, 'system': False, 'image': True})

    def _validate(self):
        if hasattr(self, 'go'):
            self.go.setEnabled(bool(self.drives.selected()) and bool(self.out.text())
                               and (self.want_video.isChecked() or self.want_photo.isChecked()))

    # ---- búsqueda y resultados
    def _run_view(self):
        w = QWidget()
        w.setObjectName('page')
        lay = QVBoxLayout(w)
        lay.setContentsMargins(36, 28, 36, 24)
        lay.setSpacing(12)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.run_title = QLabel('Buscando…')
        self.run_title.setObjectName('title')
        self.run_sub = QLabel('')
        self.run_sub.setObjectName('subtitle')
        self.run_sub.setWordWrap(True)
        titles.addWidget(self.run_title)
        titles.addWidget(self.run_sub)
        head.addLayout(titles, 1)
        self.stop_btn = QPushButton('Detener')
        self.stop_btn.setObjectName('danger')
        self.stop_btn.clicked.connect(self.stop)
        self.open_btn = QPushButton('Abrir carpeta')
        self.open_btn.clicked.connect(lambda: open_path(self.out_dir))
        self.new_btn = QPushButton('Nueva búsqueda')
        self.new_btn.setObjectName('primary')
        self.new_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        for b in (self.stop_btn, self.open_btn, self.new_btn):
            head.addWidget(b, 0, Qt.AlignTop)
        lay.addLayout(head)
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(18, 18))
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        h = self.table.horizontalHeader()
        h.setMinimumSectionSize(34)
        for col, width in ((0, 34), (2, 70), (3, 200), (4, 160), (5, 80), (6, 150)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda r, _c: open_path(self.table.item(r, 1).data(Qt.UserRole)))
        self.table.itemSelectionChanged.connect(self._show_preview)
        self.table.setToolTip('Doble clic para abrir')
        body.addWidget(self.table, 1)
        lay.addLayout(body, 1)
        # vista previa de la foto seleccionada, debajo de la tabla
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(240, 160)
        self.preview.setObjectName('hint')
        self.preview_info = QLabel()
        self.preview_info.setWordWrap(True)
        self.preview_info.setTextFormat(Qt.RichText)
        prow = QHBoxLayout()
        prow.setSpacing(16)
        prow.addWidget(self.preview)
        prow.addWidget(self.preview_info, 1, Qt.AlignTop)
        self.preview_card = card(prow, margins=(12, 12, 16, 12))
        self.preview_card.setVisible(False)
        lay.addWidget(self.preview_card)
        self.log = LogPanel()
        lay.addWidget(self.log)
        return w

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
        args = ['rescatar', d['id'], '-o', self.out_dir]
        if self.ref.text():
            args += ['--referencia', self.ref.text()]
        if self.cmp.text():
            args += ['--comparar', self.cmp.text()]
        args += ['--tipos', ','.join(t for t, cb in (('video', self.want_video), ('foto', self.want_photo))
                                     if cb.isChecked())]
        elevate = devices.is_device(d['id'])
        self.source_size = d.get('size') or 0
        self.table.setRowCount(0)
        self.preview_card.setVisible(False)
        self.log.clear()
        self.videos = self.photos = 0
        self.progress.setVisible(False)
        name = d['id'] if not d.get('image') else os.path.basename(d['id'])
        self.run_title.setText('Buscando en %s' % name)
        self.run_sub.setText('Esperando permiso de Administrador…' if elevate and not devices.is_admin()
                             else 'Preparando…')
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
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

    def _counts(self):
        return '%d vídeo%s · %d foto%s' % (self.videos, '' if self.videos == 1 else 's',
                                           self.photos, '' if self.photos == 1 else 's')

    def _on_log(self, msg):
        if self.t0 is None:
            self.t0 = time.monotonic()
            self.run_sub.setText('Leyendo la tarjeta. Puedes seguir usando el ordenador.')
        self.log.append(msg)

    def _on_progress(self, p):
        if self.t0 is None:
            self.t0 = time.monotonic()
        el = time.monotonic() - self.t0
        parts = ['%.0f %%' % (p * 100)]
        if self.source_size:
            parts.append('%s de %s' % (human_size(self.source_size * p), human_size(self.source_size)))
            if el > 3 and 0.002 < p < 1:
                parts.append('%s/s' % human_size(self.source_size * p / el))
                parts.append('quedan ~%s' % human_time(el / p * (1 - p)))
        parts.append(self._counts() + ' recuperados')
        self.progress.set(p, '   ·   '.join(parts))

    def _on_clip(self, ev):
        photo = ev.get('kind') in PHOTO_KINDS
        if photo:
            self.photos += 1
        else:
            self.videos += 1
        if ev.get('identico_a'):
            state, color = 'Idéntico a ' + ev['identico_a'], OK
        elif ev.get('complete'):
            state, color = 'Recuperado', OK
        else:
            state, color = 'Incompleto', WARN
        r = self.table.rowCount()
        self.table.insertRow(r)
        kind = QTableWidgetItem()
        kind.setIcon(QIcon(svg_pixmap('photo' if photo else 'video', ACCENT if photo else '#7aa7ff', 18)))
        kind.setToolTip('Foto' if photo else 'Vídeo')
        name = QTableWidgetItem(os.path.basename(ev.get('path', '')))
        name.setData(Qt.UserRole, ev.get('path'))
        name.setData(Qt.UserRole + 1, photo)
        name.setToolTip('%s\n%s' % (ev.get('path'), ev.get('note', '')))
        name.setData(Qt.UserRole + 2, ev)
        cells = [kind, name, QTableWidgetItem(human_time(ev.get('duracion')) if not photo else '—'),
                 QTableWidgetItem(ev.get('codec') or '—'), QTableWidgetItem(ev.get('perfil') or '—'),
                 QTableWidgetItem(human_size(ev.get('size') or 0)), status_item(state, color)]
        cells[-1].setToolTip(state)
        for c, it in enumerate(cells):
            self.table.setItem(r, c, it)
        self.table.scrollToBottom()

    def _show_preview(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        it = self.table.item(rows[0].row(), 1)
        if not it.data(Qt.UserRole + 1):
            self.preview_card.setVisible(False)
            return
        pm = uikit.preview_pixmap(it.data(Qt.UserRole), 240)
        if pm is None:
            self.preview.setText('Sin vista previa')
        else:
            self.preview.setPixmap(pm)
        ev = it.data(Qt.UserRole + 2) or {}
        self.preview_info.setText(
            '<span style="color:#fff;font-size:14px;font-weight:600">%s</span><br>'
            '<span style="color:%s">%s · %s · %s</span><br><br>'
            '<span style="color:%s">%s</span><br><span style="color:%s">Doble clic en la fila para abrirla.</span>'
            % (os.path.basename(ev.get('path', '')), MUTED, ev.get('codec') or '—', ev.get('fecha') or '—',
               human_size(ev.get('size') or 0), MUTED, ev.get('path', ''), MUTED))
        self.preview_card.setVisible(True)

    def _on_finished(self, ok, msg):
        self.stop_btn.setVisible(False)
        self.new_btn.setVisible(True)
        if ok:
            self._on_progress(1.0)
            self.run_title.setText('Terminado: ' + self._counts())
            self.run_sub.setText('Doble clic en un archivo para abrirlo. Están en %s' % self.out_dir)
        else:
            self.run_title.setText('No se pudo completar')
            self.run_sub.setText(msg)


# ---------------------------------------------------------------- reparar clip

class RepairPage(QWidget):
    COLS = ['Estado', 'Clip dañado', 'Duración', 'Perfil', 'Resultado']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
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
        clear = QPushButton('Quitar todos')
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        head.addWidget(add)
        head.addWidget(clear)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(180)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._open_result)
        drop = QFrame()
        drop.setObjectName('card')
        drop.setStyleSheet('QFrame#card { border: 1px dashed #3a404b; }')
        drop.setMinimumHeight(200)
        dl = QVBoxLayout(drop)
        dl.setAlignment(Qt.AlignCenter)
        di = QLabel()
        di.setPixmap(svg_pixmap('repair', MUTED, 40))
        di.setAlignment(Qt.AlignCenter)
        dt = QLabel('Arrastra aquí los clips que no abren')
        dt.setObjectName('cardTitle')
        dt.setAlignment(Qt.AlignCenter)
        ds = hint('o usa «Añadir clips…». Se reparan uno tras otro con la misma referencia.')
        ds.setAlignment(Qt.AlignCenter)
        for w in (di, dt, ds):
            dl.addWidget(w)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(drop)
        self.list_stack.addWidget(self.table)
        self.table.model().rowsInserted.connect(self._update_empty)
        self.table.model().rowsRemoved.connect(self._update_empty)
        self.table.model().modelReset.connect(self._update_empty)
        lay.addWidget(self.list_stack, 1)

        lay.addWidget(section('2 · Referencia'))
        self.ref = PathRow('Carpeta con clips sanos (se elige el compatible) o un clip concreto')
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
        self.go = QPushButton('Reparar')
        self.go.setObjectName('primary')
        self.go.clicked.connect(self.start)
        row.addWidget(self.go, 0, Qt.AlignTop)
        lay.addLayout(row)
        self.queue = []
        self.job = None

    def _update_empty(self, *_):
        self.list_stack.setCurrentIndex(1 if self.table.rowCount() else 0)

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
            self.table.setItem(r, 0, status_item('Pendiente', MUTED))
            name = QTableWidgetItem(os.path.basename(f))
            name.setData(Qt.UserRole, os.path.normpath(f))
            name.setToolTip(f)
            self.table.setItem(r, 1, name)
            for c in (2, 3, 4):
                self.table.setItem(r, c, QTableWidgetItem('—' if c < 4 else ''))

    def _open_result(self, r, _c):
        it = self.table.item(r, 4)
        open_path((it.data(Qt.UserRole) if it else None) or self.table.item(r, 1).data(Qt.UserRole))

    def start(self):
        if not self.ref.text():
            QMessageBox.information(self, 'Referencia', 'Indica una carpeta con clips sanos o un clip de referencia.')
            return
        self.queue = [r for r in range(self.table.rowCount())
                      if self.table.item(r, 0).text().endswith(('Pendiente', 'Error'))]
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
            self.progress.set(1.0, 'Terminado: %d clip(s)' % self.total)
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
        self.setAcceptDrops(True)
        lay = page_scaffold(self)
        lay.addWidget(page_header('Analizar un clip',
                                  'Estructura del archivo, si le falta el índice, códec, fps y perfil de '
                                  'imagen (S-Log3, S-Cinetone, HLG…) leído de los metadatos de Sony.'))
        row = QHBoxLayout()
        self.path = PathRow('Clip MP4 / MXF', mode='file', filter_='Vídeo (*.mp4 *.MP4 *.mxf *.MXF *.mov)')
        row.addWidget(self.path, 1)
        go = QPushButton('Analizar')
        go.setObjectName('primary')
        go.clicked.connect(self.run)
        row.addWidget(go)
        lay.addWidget(card(row))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText('Arrastra aquí un clip o elígelo arriba.')
        self.text.setMinimumHeight(420)
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
                               'No hay tarjetas conectadas. Solo se muestran unidades extraíbles '
                               '(tarjetas y lectores), nunca discos ni SSD externos.')

    def _sel(self):
        return self.drives.selected()

    def _busy(self):
        return bool(self.job and self.job.running)

    def _set_enabled(self):
        for c in self.cards:
            c.button.setEnabled(bool(self._sel()) and not self._busy())

    def _selected_changed(self):
        self._set_enabled()
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
            self.info.setText(
                '<span style="color:#fff;font-size:14px;font-weight:600">%s &nbsp;%s</span>'
                '&nbsp;&nbsp;<span style="color:%s">%s</span><br>'
                '%s · %s %s · partición %s · %s usados de %s<br>'
                '<span style="color:#e6e6e6"><b>Vida útil:</b> %s</span>' % (
                    c['id'], c['model'], MUTED, human_size(c['size'] or 0), c.get('bus') or '',
                    c.get('style') or '', c.get('fs') or '', parts or '—',
                    human_size(used) if used is not None else '—',
                    human_size(c['volsize']) if c.get('volsize') else '—', h['conclusion']))
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
        for key, label in self.PAGES:
            self.nav.addItem(QListWidgetItem(uikit.nav_icon(key), '  ' + label))
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
    app.setStyleSheet(qss())
    w = MainWindow()
    w.show()
    return app.exec()
