"""Componentes visuales de la interfaz: iconos vectoriales, fichas de tarjeta, etiquetas y
tarjetas de acción. Todo dibujado aquí (sin imágenes externas salvo el logo)."""
import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

ACCENT = '#f0a43a'
OK = '#7bd88f'
WARN = '#f0c05a'
BAD = '#ff7b72'
MUTED = '#9aa0ab'

# Iconos de línea propios (24×24, trazo 1.8)
ICONS = {
    'rescue': '<path d="M8 3h7l4 4v13a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>'
              '<path d="M13 9v7M10 13l3 3 3-3"/>',
    'repair': '<rect x="3" y="5" width="18" height="14" rx="2"/>'
              '<path d="M7 5v14M17 5v14M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4"/>',
    'analyze': '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    'tools': '<path d="M4 16.5a8 8 0 1 1 16 0"/><path d="M12 16.5l4-5"/><circle cx="12" cy="16.5" r="1.2"/>',
    'about': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.4"/>',
    'card': '<path d="M8 3h7l4 4v13a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>'
            '<path d="M10 3v3M12.5 3v3M15 4v2"/>',
    'image': '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/>'
             '<path d="M21 16l-5-5-9 9"/>',
    'video': '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/>',
    'photo': '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    'disk': '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 12h.01M17 12h-5"/>',
    'check': '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    'folder': '<path d="M3 6h6l2 2h10v11H3z"/>',
}


def svg_pixmap(name, color=MUTED, size=20, stroke=1.8):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" '
           'stroke-width="%s" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
           % (color, stroke, ICONS[name]))
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ratio = 2
    img = QImage(size * ratio, size * ratio, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(ratio)
    return pm


def nav_icon(name):
    icon = QIcon()
    icon.addPixmap(svg_pixmap(name, '#8b919c'), QIcon.Normal, QIcon.Off)
    icon.addPixmap(svg_pixmap(name, ACCENT), QIcon.Selected, QIcon.Off)
    icon.addPixmap(svg_pixmap(name, ACCENT), QIcon.Active, QIcon.Off)
    return icon


def _check_image():
    """PNG de la marca de las casillas (la hoja de estilos necesita un archivo)."""
    path = os.path.join(tempfile.gettempdir(), 'md55_check.png')
    if not os.path.exists(path):
        svg_pixmap('check', '#1a1306', 14, 3).toImage().save(path)
    return path.replace('\\', '/')


def stylesheet():
    return """
* { font-size: 13px; }
QMainWindow, QWidget#page { background: #15171c; color: #e6e6e6; }
QWidget { color: #e6e6e6; }
QWidget#side { background: #101216; }
QListWidget#nav { background: transparent; border: none; outline: 0; }
QListWidget#nav::item { padding: 10px 12px; margin: 2px 10px; border-radius: 8px; color: #b9bcc4; }
QListWidget#nav::item:selected { background: #22262e; color: #ffffff; }
QListWidget#nav::item:hover:!selected { background: #1a1d24; }
QLabel#title { font-size: 22px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { color: %(muted)s; }
QLabel#step { font-size: 13px; font-weight: 600; color: %(accent)s; letter-spacing: 0.3px; }
QLabel#hint { color: #8b919c; font-size: 12px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; color: #ffffff; }
QFrame#card { background: #1b1e25; border: 1px solid #2a2e37; border-radius: 12px; }
QFrame#tile { background: #1b1e25; border: 1px solid #2a2e37; border-radius: 10px; }
QFrame#tile[selected="true"] { background: #2a2216; border: 1px solid %(accent)s; }
QFrame#tile:hover { border-color: #3a404b; }
QFrame#warn { background: #34191c; border: 1px solid #7a3338; border-radius: 10px; }
QLineEdit { background: #1b1e25; border: 1px solid #2f343e; border-radius: 7px; padding: 8px 10px; }
QLineEdit:focus { border-color: %(accent)s; }
QPushButton { background: #262a33; border: 1px solid #343944; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover { background: #2e333d; }
QPushButton:disabled { color: #5f646d; border-color: #2a2e37; }
QPushButton#primary { background: %(accent)s; color: #1a1306; border: none; font-weight: 600; padding: 10px 22px; }
QPushButton#primary:hover { background: #f6b85c; }
QPushButton#primary:disabled { background: #4a3d28; color: #2a2112; }
QPushButton#danger { background: #4a2326; border-color: #6b2f33; color: #ffd6d6; }
QPushButton#danger:hover { background: #5a2a2e; }
QPushButton#danger:disabled { background: #2a1d1f; color: #6b5557; }
QPushButton#link { background: transparent; border: none; color: %(muted)s; padding: 2px 0; text-align: left; }
QPushButton#link:hover { color: #ffffff; }
QProgressBar { background: #22262e; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
QProgressBar#usage { max-height: 4px; min-height: 4px; border-radius: 2px; background: #2a2e37; }
QProgressBar#usage::chunk { background: #6b7280; border-radius: 2px; }
QTableWidget { background: #1a1d23; alternate-background-color: #1d2027; border: 1px solid #2a2e37; border-radius: 10px; gridline-color: #262a33; }
QHeaderView::section { background: #1d2027; color: %(muted)s; border: none; border-bottom: 1px solid #2a2e37; padding: 7px 6px; font-weight: 600; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:selected { background: #2a2216; color: #ffffff; }
QPlainTextEdit { background: #101216; border: 1px solid #2a2e37; border-radius: 8px; font-family: Consolas, Menlo, monospace; font-size: 12px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4a505c; border-radius: 4px; background: #1b1e25; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(check)s); }
QTableView::indicator { width: 16px; height: 16px; border: 1px solid #4a505c; border-radius: 4px; background: #1b1e25; }
QTableView::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(check)s); }
QTableView::indicator:disabled { border-color: #353a45; }
QTableView::indicator:checked:disabled { background: #7a5a26; border-color: #7a5a26; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #353a45; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #454b57; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #353a45; border-radius: 4px; min-width: 30px; }
QToolTip { background: #22262e; color: #e6e6e6; border: 1px solid #3a404b; padding: 6px; border-radius: 6px; }
QMessageBox { background: #1b1e25; }
QToolButton { border: none; color: %(muted)s; }
""" % {'accent': ACCENT, 'muted': MUTED, 'check': _check_image()}


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%.0f %s' if unit in ('B', 'KB') else '%.1f %s') % (n, unit)
        n /= 1024.0


class Badge(QLabel):
    """Etiqueta pequeña de color (p.ej. «No borra», «Borra todo»)."""

    def __init__(self, text, color=OK, parent=None):
        super().__init__(text, parent)
        c = QColor(color)
        self.setStyleSheet(
            'color: %s; background: rgba(%d,%d,%d,0.12); border: 1px solid rgba(%d,%d,%d,0.45);'
            'border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600;'
            % (color, c.red(), c.green(), c.blue(), c.red(), c.green(), c.blue()))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class DriveTile(QFrame):
    """Ficha seleccionable de una tarjeta / unidad / imagen de disco."""

    def __init__(self, drive, on_click, parent=None):
        super().__init__(parent)
        self.drive = drive
        self.setObjectName('tile')
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 14, 10)
        lay.setSpacing(12)
        icon = QLabel()
        kind = 'image' if drive.get('image') else 'card' if drive.get('removable') else 'disk'
        icon.setPixmap(svg_pixmap(kind, ACCENT if kind != 'disk' else MUTED, 26))
        lay.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel('<b style="color:#fff">%s</b>&nbsp;&nbsp;%s' % (
            drive['id'] if not drive.get('image') else os.path.basename(drive['id']),
            drive['name'] if not drive.get('image') else ''))
        top.addWidget(title)
        if drive.get('removable'):
            top.addWidget(Badge('Extraíble', ACCENT))
        top.addStretch(1)
        text.addLayout(top)
        size = drive.get('size') or 0
        used = drive.get('used')
        sub = QLabel(' · '.join(x for x in (
            drive.get('fs') or ('Imagen de disco' if drive.get('image') else ''),
            human_size(size) if size else '',
            ('%s usados' % human_size(used)) if used is not None else '') if x))
        sub.setObjectName('hint')
        text.addWidget(sub)
        if used is not None and size:
            bar = QProgressBar()
            bar.setObjectName('usage')
            bar.setRange(0, 1000)
            bar.setValue(int(1000 * used / size))
            text.addWidget(bar)
        lay.addLayout(text, 1)

    def set_selected(self, on):
        self.setProperty('selected', 'true' if on else 'false')
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.on_click(self)


class DriveList(QWidget):
    """Lista de fichas de tarjeta con selección única."""

    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self.on_change = on_change
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.tiles = []
        self.current = None
        self.empty = QLabel('')
        self.empty.setObjectName('hint')

    def set_drives(self, drives, empty_text):
        keep = self.current.drive['id'] if self.current else None
        for t in self.tiles:
            t.setParent(None)
        self.tiles = []
        self.current = None
        self.empty.setParent(None)
        for d in drives:
            self.add(d, select=False)
        if not drives:
            self.empty.setText(empty_text)
            self.lay.addWidget(self.empty)
        for t in self.tiles:
            if t.drive['id'] == keep:
                self._select(t)
        if self.on_change:
            self.on_change()

    def add(self, drive, select=True):
        tile = DriveTile(drive, self._select)
        self.tiles.append(tile)
        self.lay.addWidget(tile)
        if select:
            self._select(tile)
        return tile

    def _select(self, tile):
        self.current = tile
        for t in self.tiles:
            t.set_selected(t is tile)
        if self.on_change:
            self.on_change()

    def selected(self):
        return self.current.drive if self.current else None


class ActionCard(QFrame):
    """Tarjeta con título, descripción, etiqueta y botón."""

    def __init__(self, title, text, badge, badge_color, button, handler, danger=False, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName('cardTitle')
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(Badge(badge, badge_color))
        lay.addLayout(head)
        d = QLabel(text)
        d.setObjectName('hint')
        d.setWordWrap(True)
        lay.addWidget(d, 1)
        self.button = QPushButton(button)
        if danger:
            self.button.setObjectName('danger')
        self.button.clicked.connect(handler)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.button)
        lay.addLayout(row)


def section(title):
    lab = QLabel(title.upper())
    lab.setObjectName('step')
    return lab


def preview_pixmap(path, max_side=360):
    """Vista previa de una foto: la JPEG incrustada (ARW/HIF) o la propia JPG."""
    try:
        with open(path, 'rb') as f:
            data = f.read(4 << 20)
    except OSError:
        return None
    best = None
    i = data.find(b'\xff\xd8\xff')
    tries = 0
    while i >= 0 and tries < 12:
        j = data.find(b'\xff\xd9', i)
        while j >= 0:
            img = QImage()
            if img.loadFromData(data[i:j + 2]) and not img.isNull():
                if best is None or img.width() > best.width():
                    best = img
                break
            j = data.find(b'\xff\xd9', j + 2)
            if j - i > (3 << 20):
                break
        i = data.find(b'\xff\xd8\xff', i + 3)
        tries += 1
    if best is None:
        return None
    return QPixmap.fromImage(best.scaled(QSize(max_side, max_side), Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
