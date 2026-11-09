"""Componentes visuales de la interfaz: colores, iconos vectoriales, hoja de estilos y piezas
reutilizables (fichas de tarjeta, tarjetas de elección, chips, indicadores, avisos…).
Todo dibujado aquí, sin imágenes externas salvo el logo."""
import html
import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QStyledItemDelegate, QStyle, QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------- colores

BG = '#0f1115'           # fondo de las páginas
SIDE = '#0b0d10'         # barra lateral
SURFACE = '#161a20'      # tarjetas
RAISED = '#1c2028'       # campos, botones
BORDER = '#252a33'
BORDER_HI = '#343b47'
TEXT = '#e8eaee'
WHITE = '#ffffff'
MUTED = '#9aa1ad'
DIM = '#6e7581'          # texto secundario dentro de fichas
FAINT = '#565c67'        # elementos omitidos / deshabilitados
ACCENT = '#f5a524'
ACCENT_HI = '#ffb84a'
OK = '#5fd38d'
WARN = '#f3c35b'
BAD = '#ff6b6b'
VIDEO = '#7aa2ff'
CUT = '#c79bff'          # grabaciones cortadas (RSV)


def rgba(color, alpha):
    c = QColor(color)
    return 'rgba(%d,%d,%d,%s)' % (c.red(), c.green(), c.blue(), alpha)


# ---------------------------------------------------------------- iconos

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
    'cut': '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.6 7.6L20 18M8.6 16.4L20 6"/>',
    'refresh': '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    'play': '<path d="M7 5l12 7-12 7z"/>',
    'shield': '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    'alert': '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.4"/>',
    'ok': '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16 10"/>',
    'error': '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    'sliders': '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    'external': '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5H5V6h5"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    'download': '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    'spark': '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
}


def svg_pixmap(name, color=MUTED, size=20, stroke=1.8, fill='none'):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%s" stroke="%s" '
           'stroke-width="%s" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
           % (fill, color, stroke, ICONS[name]))
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


def icon(name, color=TEXT, size=18):
    return QIcon(svg_pixmap(name, color, size))


def tint(pm, color):
    """Copia de un pixmap con todo lo opaco pintado de un color (para el logo)."""
    ratio = pm.devicePixelRatio()
    src = QPixmap(pm)
    src.setDevicePixelRatio(1)           # pintar píxel a píxel; la escala se restaura al final
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    out.setDevicePixelRatio(ratio)
    return out


def plural(n, word, many=None):
    """'1 vídeo', '3 vídeos'."""
    return '%d %s' % (n, word if n == 1 else many or word + 's')


def esc(text):
    """Texto para las etiquetas con HTML: un nombre con < o & no rompe el formato."""
    return html.escape('%s' % text, quote=False)


def nav_icon(name):
    icon_ = QIcon()
    icon_.addPixmap(svg_pixmap(name, '#7d8490'), QIcon.Normal, QIcon.Off)
    icon_.addPixmap(svg_pixmap(name, ACCENT), QIcon.Selected, QIcon.Off)
    icon_.addPixmap(svg_pixmap(name, ACCENT), QIcon.Active, QIcon.Off)
    return icon_


def _check_image():
    """PNG de la marca de las casillas (la hoja de estilos necesita un archivo)."""
    path = os.path.join(tempfile.gettempdir(), 'md55_check_v2.png')
    if not os.path.exists(path):
        svg_pixmap('check', '#1a1306', 14, 3).toImage().save(path)
    return path.replace('\\', '/')


def app_font():
    """Fuente de la interfaz: la del sistema, algo más moderna si está disponible."""
    f = QFont()
    for family in ('Segoe UI Variable Text', 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', 'Inter'):
        f.setFamily(family)
        if QFont(family).exactMatch():
            break
    f.setPointSizeF(9.75)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def stylesheet():
    return """
* { font-size: 13px; }
QMainWindow, QWidget#page { background: %(bg)s; color: %(text)s; }
QWidget { color: %(text)s; }
QWidget#side { background: %(side)s; border-right: 1px solid #171b21; }
QListWidget#nav { background: transparent; border: none; outline: 0; }
QListWidget#nav::item { padding: 11px 10px; margin: 2px 12px; border-radius: 9px; color: #aab0bb; }
QListWidget#nav::item:selected { background: %(accent_bg)s; color: #ffffff; }
QListWidget#nav::item:hover:!selected { background: #14171c; color: #d6d9df; }
QFrame#sideCard { background: #12151a; border: 1px solid #1d2128; border-radius: 10px; }
QLabel#brand { font-size: 17px; font-weight: 700; color: #ffffff; letter-spacing: 0.2px; }
QLabel#brandSub { color: %(dim)s; font-size: 11px; }
QLabel#title { font-size: 24px; font-weight: 650; color: #ffffff; }
QLabel#subtitle { color: %(muted)s; font-size: 13px; }
QLabel#step { font-size: 12px; font-weight: 700; color: #c4c9d2; letter-spacing: 0.8px; }
QLabel#stepNum { background: %(accent)s; color: #1a1306; border-radius: 11px; font-size: 12px;
    font-weight: 800; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }
QLabel#hint { color: #848b97; font-size: 12px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; color: #ffffff; }
QLabel#statValue { font-size: 22px; font-weight: 700; color: #ffffff; }
QLabel#statLabel { color: %(muted)s; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }
QLabel#key { color: %(dim)s; font-size: 12px; }
QLabel#value { color: %(text)s; font-size: 12px; }
QLabel#iconTile { background: %(accent_bg)s; border: 1px solid %(accent_line)s; border-radius: 12px; }
QFrame#card { background: %(surface)s; border: 1px solid %(border)s; border-radius: 14px; }
QFrame#stat { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; }
QFrame#tile { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; }
QFrame#tile[selected="true"] { background: %(accent_bg)s; border: 1px solid %(accent)s; }
QFrame#tile:hover { border-color: %(border_hi)s; }
QFrame#empty { background: transparent; border: 1px dashed #353c48; border-radius: 14px; }
QFrame#empty[drag="true"] { border-color: %(accent)s; background: %(accent_bg)s; }
QLabel#preview { background: #0c0e12; border: 1px solid %(border)s; border-radius: 10px; color: %(dim)s; font-size: 12px; }
QFrame#banner_ok { background: %(ok_bg)s; border: 1px solid %(ok_line)s; border-radius: 12px; }
QFrame#banner_warn { background: %(warn_bg)s; border: 1px solid %(warn_line)s; border-radius: 12px; }
QFrame#banner_bad, QFrame#warn { background: %(bad_bg)s; border: 1px solid %(bad_line)s; border-radius: 12px; }
QFrame#footer { background: %(surface)s; border: 1px solid %(border)s; border-radius: 14px; }
QLineEdit { background: %(raised)s; border: 1px solid %(border)s; border-radius: 9px; padding: 9px 12px;
    selection-background-color: %(accent)s; selection-color: #1a1306; }
QLineEdit:hover { border-color: %(border_hi)s; }
QLineEdit:focus { border-color: %(accent)s; }
QPushButton { background: %(raised)s; border: 1px solid %(border_hi)s; border-radius: 9px; padding: 8px 15px;
    color: %(text)s; }
QPushButton:hover { background: #242a34; border-color: #444c5a; }
QPushButton:pressed { background: #1a1e25; }
QPushButton:disabled { color: %(faint)s; border-color: %(border)s; background: #171a20; }
QPushButton#primary { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(accent_hi)s, stop:1 %(accent)s);
    color: #1a1306; border: 1px solid #c98412; font-weight: 700; padding: 10px 22px; }
QPushButton#primary:hover { background: %(accent_hi)s; }
QPushButton#primary:pressed { background: #e0951c; }
QPushButton#primary:disabled { background: #22262e; color: %(dim)s; border-color: %(border)s; }
QPushButton#danger { background: #3b1d21; border-color: #6b2f35; color: #ffd6d6; }
QPushButton#danger:hover { background: #4a2328; }
QPushButton#danger:disabled { background: #221a1c; color: #6b5557; border-color: #2e2224; }
QPushButton#link { background: transparent; border: none; color: %(muted)s; padding: 2px 0; text-align: left; }
QPushButton#link:hover { color: #ffffff; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; color: %(muted)s; padding: 6px 10px; }
QPushButton#ghost:hover { background: #1b1f26; color: #ffffff; }
QPushButton#chip { background: transparent; border: 1px solid %(border_hi)s; border-radius: 15px;
    padding: 5px 13px; color: #b9bec8; }
QPushButton#chip:hover { border-color: #4a5260; color: #ffffff; }
QPushButton#chip:checked { background: %(accent_bg)s; border-color: %(accent)s; color: #ffffff; }
QPushButton#choice { background: %(surface)s; border: 1px solid %(border)s; border-radius: 14px;
    padding: 0; text-align: left; }
QPushButton#choice:hover { border-color: %(border_hi)s; }
QPushButton#choice:checked { background: %(accent_bg)s; border: 1px solid %(accent)s; }
QProgressBar { background: #1f232b; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
    text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e08a14, stop:1 %(accent_hi)s); }
QProgressBar#usage { max-height: 4px; min-height: 4px; border-radius: 2px; background: #252a33; }
QProgressBar#usage::chunk { background: #6b7280; border-radius: 2px; }
QFrame#tile[selected="true"] QProgressBar#usage::chunk { background: %(accent)s; }
QTableWidget { background: %(surface)s; alternate-background-color: #181c23; border: 1px solid %(border)s;
    border-radius: 14px; gridline-color: transparent; selection-background-color: transparent; }
QHeaderView::section { background: %(surface)s; color: %(dim)s; border: none; border-bottom: 1px solid %(border)s;
    padding: 9px 8px; font-weight: 700; font-size: 11px; letter-spacing: 0.5px; }
QTableWidget::item { padding: 4px 6px; border: none; }
QTableWidget::item:selected { background: %(accent_bg)s; color: #ffffff; }
QTableCornerButton::section { background: %(surface)s; border: none; }
QPlainTextEdit { background: #0b0d10; border: 1px solid %(border)s; border-radius: 10px;
    font-family: "Cascadia Mono", Consolas, Menlo, monospace; font-size: 12px; padding: 6px;
    selection-background-color: %(accent)s; selection-color: #1a1306; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #4a515e; border-radius: 5px; background: %(raised)s; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(check)s); }
QTableView::indicator { width: 17px; height: 17px; border: 1px solid #4a515e; border-radius: 5px; background: %(raised)s; }
QTableView::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(check)s); }
QTableView::indicator:disabled { border-color: #2f3540; background: #171a20; }
QTableView::indicator:checked:disabled { background: #6e5426; border-color: #6e5426; }
QScrollArea { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 3px; }
QScrollBar::handle:vertical { background: #2c323d; border-radius: 4px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #3c4350; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 3px; }
QScrollBar::handle:horizontal { background: #2c323d; border-radius: 4px; min-width: 32px; }
QComboBox { background: %(raised)s; border: 1px solid %(border_hi)s; border-radius: 15px; padding: 5px 12px; }
QComboBox:hover { border-color: #4a5260; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: %(raised)s; border: 1px solid %(border_hi)s; padding: 4px;
    selection-background-color: %(accent_bg)s; outline: 0; }
QToolTip { background: #1f242c; color: %(text)s; border: 1px solid %(border_hi)s; padding: 7px 9px; border-radius: 8px; }
QMessageBox, QInputDialog, QDialog { background: %(surface)s; }
QMessageBox QLabel { color: %(text)s; }
QToolButton { border: none; color: %(muted)s; }
QSplitter::handle { background: transparent; }
""" % {'accent': ACCENT, 'accent_hi': ACCENT_HI, 'accent_bg': rgba(ACCENT, 0.11),
       'accent_line': rgba(ACCENT, 0.35), 'muted': MUTED, 'dim': DIM, 'faint': FAINT, 'text': TEXT,
       'bg': BG, 'side': SIDE, 'surface': SURFACE, 'raised': RAISED, 'border': BORDER,
       'border_hi': BORDER_HI, 'check': _check_image(),
       'ok_bg': rgba(OK, 0.08), 'ok_line': rgba(OK, 0.35), 'warn_bg': rgba(WARN, 0.08),
       'warn_line': rgba(WARN, 0.35), 'bad_bg': rgba(BAD, 0.09), 'bad_line': rgba(BAD, 0.40)}


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%.0f %s' if unit in ('B', 'KB') else '%.1f %s') % (n, unit)
        n /= 1024.0


# ---------------------------------------------------------------- piezas pequeñas

class Badge(QLabel):
    """Etiqueta pequeña de color (p.ej. «No borra», «Borra todo»)."""

    def __init__(self, text, color=OK, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(
            'color: %s; background: %s; border: 1px solid %s; border-radius: 10px; padding: 2px 9px;'
            'font-size: 11px; font-weight: 700;' % (color, rgba(color, 0.12), rgba(color, 0.40)))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


def icon_tile(name, size=46, color=ACCENT):
    """Icono dentro de un recuadro redondeado de color suave (cabeceras de página)."""
    lab = QLabel()
    lab.setObjectName('iconTile')
    lab.setFixedSize(size, size)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(svg_pixmap(name, color, size // 2))
    if color != ACCENT:
        lab.setStyleSheet('QLabel#iconTile { background: %s; border-color: %s; }'
                          % (rgba(color, 0.11), rgba(color, 0.35)))
    return lab


def section(title):
    """Encabezado de bloque. «2 · Qué buscar» se muestra con el número en un círculo."""
    num, _, text = title.partition(' · ')
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 6, 0, 0)
    lay.setSpacing(10)
    if num.isdigit() and text:
        n = QLabel(num)
        n.setObjectName('stepNum')
        n.setAlignment(Qt.AlignCenter)
        lay.addWidget(n)
    else:
        text = title
    lab = QLabel(text.upper())
    lab.setObjectName('step')
    lay.addWidget(lab)
    lay.addStretch(1)
    return w


class StatTile(QFrame):
    """Cifra destacada con su etiqueta e icono (p.ej. «12 VÍDEOS»)."""

    def __init__(self, icon_name, label, color=ACCENT, parent=None):
        super().__init__(parent)
        self.setObjectName('stat')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 16, 10)
        lay.setSpacing(12)
        lay.addWidget(icon_tile(icon_name, 36, color))
        words = QVBoxLayout()
        words.setSpacing(0)
        self.value = QLabel('0')
        self.value.setObjectName('statValue')
        self.label = QLabel(label.upper())
        self.label.setObjectName('statLabel')
        words.addWidget(self.value)
        words.addWidget(self.label)
        lay.addLayout(words, 1)

    def set(self, value, label=None):
        self.value.setText('%s' % value)
        if label is not None:
            self.label.setText(label.upper())


class Chip(QPushButton):
    """Filtro que se activa y desactiva, con contador."""

    def __init__(self, text, checked=True, parent=None):
        super().__init__(parent)
        self.base = text
        self.setObjectName('chip')
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.set_count(None)

    def set_count(self, n):
        self.setText(self.base if n is None else '%s  %d' % (self.base, n))


class ChoiceCard(QPushButton):
    """Opción grande que se marca o desmarca: icono, título, descripción y casilla."""

    def __init__(self, icon_name, title, text, checked=True, color=ACCENT, parent=None):
        super().__init__(parent)
        self.setObjectName('choice')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(74)
        self.icon_name = icon_name
        self.color = color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 16, 12)
        lay.setSpacing(12)
        self.pic = icon_tile(icon_name, 42, color)
        words = QVBoxLayout()
        words.setSpacing(2)
        t = QLabel(title)
        t.setObjectName('cardTitle')
        d = QLabel(text)
        d.setObjectName('hint')
        d.setWordWrap(True)
        words.addWidget(t)
        words.addWidget(d)
        self.mark = QLabel()
        self.mark.setFixedSize(22, 22)
        self.mark.setAlignment(Qt.AlignCenter)
        for wdg in (self.pic, t, d, self.mark):
            wdg.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.pic)
        lay.addLayout(words, 1)
        lay.addWidget(self.mark, 0, Qt.AlignTop)
        self.toggled.connect(self._paint_mark)
        self.setChecked(checked)
        self._paint_mark(checked)

    def _paint_mark(self, on):
        if on:
            self.mark.setPixmap(svg_pixmap('check', '#1a1306', 14, 3))
            self.mark.setStyleSheet('background: %s; border-radius: 11px;' % ACCENT)
        else:
            self.mark.setPixmap(QPixmap())
            self.mark.setStyleSheet('border: 1px solid #4a515e; border-radius: 11px;')


class Banner(QFrame):
    """Aviso destacado: ok (verde), warn (ámbar) o bad (rojo), con icono, título, texto y botones."""

    KINDS = {'ok': ('ok', OK), 'warn': ('alert', WARN), 'bad': ('error', BAD)}

    def __init__(self, kind='ok', title='', text='', parent=None):
        super().__init__(parent)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(14)
        self.pic = QLabel()
        self.pic.setFixedWidth(30)
        self.pic.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        words = QVBoxLayout()
        words.setSpacing(3)
        self.title = QLabel()
        self.title.setObjectName('cardTitle')
        self.title.setWordWrap(True)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.PlainText)
        self.text.setStyleSheet('color: #c9ced6;')
        words.addWidget(self.title)
        words.addWidget(self.text)
        self.lay.addWidget(self.pic, 0, Qt.AlignTop)
        self.lay.addLayout(words, 1)
        self.set(kind, title, text)

    def set(self, kind, title, text=''):
        icon_name, color = self.KINDS[kind]
        self.setObjectName('banner_' + kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.pic.setPixmap(svg_pixmap(icon_name, color, 26))
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))

    def add_button(self, button):
        self.lay.addWidget(button, 0, Qt.AlignVCenter)
        return button


class InfoGrid(QWidget):
    """Pares «clave  valor» en dos columnas (detalles de un archivo)."""

    def __init__(self, columns=2, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(5)
        self.labels = []

    def set_rows(self, rows):
        for lab in self.labels:
            lab.setParent(None)
            lab.deleteLater()
        self.labels = []
        rows = [(k, v) for k, v in rows if v not in (None, '', '—')]
        per_col = -(-len(rows) // self.columns) if rows else 0
        for i, (k, v) in enumerate(rows):
            col, row = divmod(i, per_col) if per_col else (0, 0)
            key = QLabel(k)
            key.setObjectName('key')
            val = QLabel('%s' % v)
            val.setObjectName('value')
            val.setTextFormat(Qt.PlainText)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.grid.addWidget(key, row, col * 2)
            self.grid.addWidget(val, row, col * 2 + 1)
            self.labels += [key, val]
        for c in range(self.columns):
            self.grid.setColumnStretch(c * 2 + 1, 1)


# ---------------------------------------------------------------- miniaturas y estados en tablas

PILL_ROLE = Qt.UserRole + 7      # (texto, color) de la etiqueta de estado de una celda


class PillDelegate(QStyledItemDelegate):
    """Dibuja la celda como una etiqueta redondeada de color (estado de un archivo)."""

    def paint(self, painter, option, index):
        data = index.data(PILL_ROLE)
        if not data:
            return super().paint(painter, option, index)
        text, color = data
        if option.state & QStyle.State_Selected:
            sel = QColor(ACCENT)
            sel.setAlphaF(0.11)
            painter.fillRect(option.rect, sel)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        f = QFont(option.font)
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        f.setWeight(QFont.DemiBold)
        painter.setFont(f)
        fm = painter.fontMetrics()
        w = min(option.rect.width() - 12, fm.horizontalAdvance(text) + 32)
        h = fm.height() + 8
        r = QRectF(option.rect.x() + 6, option.rect.center().y() - h / 2 + 1, w, h)
        c = QColor(color)
        bg = QColor(c)
        bg.setAlphaF(0.13)
        path = QPainterPath()
        path.addRoundedRect(r, h / 2, h / 2)
        painter.fillPath(path, bg)
        painter.setPen(Qt.NoPen)
        painter.setBrush(c)
        painter.drawEllipse(QRectF(r.x() + 9, r.center().y() - 3, 6, 6))
        painter.setPen(QPen(c))
        painter.drawText(r.adjusted(20, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         fm.elidedText(text, Qt.ElideRight, int(r.width() - 24)))
        painter.restore()


def thumb_placeholder(icon_name, color, w, h, ratio=2):
    """Miniatura vacía: recuadro oscuro redondeado con el icono del tipo de archivo."""
    pm = QPixmap(int(w * ratio), int(h * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w * ratio, h * ratio), 6 * ratio, 6 * ratio)
    p.fillPath(path, QColor('#0c0e12'))
    p.setPen(QPen(QColor(color).darker(260)))
    p.drawPath(path)
    ic = svg_pixmap(icon_name, color, int(min(w, h) * 0.5), 1.8)
    ic.setDevicePixelRatio(1)
    s = ic.size()
    p.drawPixmap(int((w * ratio - s.width()) / 2), int((h * ratio - s.height()) / 2), ic)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def load_thumb(path, w, h, ratio=2):
    """Imagen reducida y recortada a w×h con esquinas redondeadas (lectura rápida de JPG)."""
    if not path or not os.path.exists(path):
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > 0:
        scale = max(w * ratio / size.width(), h * ratio / size.height())
        reader.setScaledSize(QSize(max(1, int(size.width() * scale + 0.5)),
                                   max(1, int(size.height() * scale + 0.5))))
    img = reader.read()
    if img.isNull():
        return None
    W, H = int(w * ratio), int(h * ratio)
    img = img.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    img = img.copy((img.width() - W) // 2, (img.height() - H) // 2, W, H)
    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path_ = QPainterPath()
    path_.addRoundedRect(QRectF(0, 0, W, H), 6 * ratio, 6 * ratio)
    p.setClipPath(path_)
    p.drawImage(0, 0, img)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


# ---------------------------------------------------------------- tarjetas / unidades

class DriveTile(QFrame):
    """Ficha seleccionable de una tarjeta / unidad / imagen de disco."""

    def __init__(self, drive, on_click, parent=None):
        super().__init__(parent)
        self.drive = drive
        self.setObjectName('tile')
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 16, 12)
        lay.setSpacing(14)
        kind = 'image' if drive.get('image') else 'card' if drive.get('removable') else 'disk'
        lay.addWidget(icon_tile(kind, 40, ACCENT if kind != 'disk' else MUTED), 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel('<span style="color:#fff;font-weight:700">%s</span>&nbsp;&nbsp;%s' % (
            esc(drive['id'] if not drive.get('image') else os.path.basename(drive['id'])),
            esc(drive['name']) if not drive.get('image') else ''))
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
        for bar in self.findChildren(QProgressBar):
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click(self)


class DriveList(QWidget):
    """Fichas de tarjeta en una rejilla de dos columnas, con selección única."""

    COLUMNS = 2

    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self.on_change = on_change
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        for c in range(self.COLUMNS):
            self.grid.setColumnStretch(c, 1)
        outer.addLayout(self.grid)
        self.tiles = []
        self.current = None
        self.empty = EmptyState('card', 'No hay ninguna tarjeta', '', compact=True)
        self.empty.setVisible(False)
        outer.addWidget(self.empty)

    def set_drives(self, drives, empty_text):
        keep = self.current.drive['id'] if self.current else None
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []
        self.current = None
        for d in drives:
            self.add(d, select=False)
        self.empty.text.setText(empty_text)
        self.empty.setVisible(not drives)
        for t in self.tiles:
            if t.drive['id'] == keep:
                self._select(t)
        if self.on_change:
            self.on_change()

    def add(self, drive, select=True):
        tile = DriveTile(drive, self._select)
        n = len(self.tiles)
        self.tiles.append(tile)
        self.grid.addWidget(tile, n // self.COLUMNS, n % self.COLUMNS)
        self.empty.setVisible(False)
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
    """Herramienta: icono, título, descripción, etiqueta de riesgo y botón."""

    def __init__(self, title, text, badge, badge_color, button, handler, danger=False, icon_name='tools',
                 parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(icon_tile(icon_name, 38, badge_color))
        t = QLabel(title)
        t.setObjectName('cardTitle')
        t.setWordWrap(True)
        head.addWidget(t, 1)
        head.addWidget(Badge(badge, badge_color), 0, Qt.AlignTop)
        lay.addLayout(head)
        d = QLabel(text)
        d.setObjectName('hint')
        d.setWordWrap(True)
        lay.addWidget(d, 1)
        self.button = QPushButton(button)
        self.button.setCursor(Qt.PointingHandCursor)
        if danger:
            self.button.setObjectName('danger')
        self.button.clicked.connect(handler)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.button)
        lay.addLayout(row)


class EmptyState(QFrame):
    """Recuadro discontinuo con icono, título y explicación para cuando no hay nada que mostrar."""

    def __init__(self, icon_name, title, text, compact=False, parent=None):
        super().__init__(parent)
        self.setObjectName('empty')
        if compact:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(16, 14, 16, 14)
            lay.setSpacing(14)
        else:
            lay = QVBoxLayout(self)
            lay.setAlignment(Qt.AlignCenter)
            lay.setSpacing(8)
        pic = icon_tile(icon_name, 44 if compact else 64, MUTED if compact else ACCENT)
        words = QVBoxLayout()
        words.setSpacing(3)
        self.title = QLabel(title)
        self.title.setObjectName('cardTitle')
        self.text = QLabel(text)
        self.text.setObjectName('hint')
        self.text.setWordWrap(True)
        for lab in (self.title, self.text):
            if not compact:
                lab.setAlignment(Qt.AlignCenter)
                lab.setMinimumWidth(460)
            words.addWidget(lab)
        lay.addWidget(pic, 0, Qt.AlignCenter if not compact else Qt.AlignVCenter)
        lay.addLayout(words, 1 if compact else 0)

    def set_drag(self, on):
        """Resalta el recuadro mientras se arrastran archivos encima."""
        self.setProperty('drag', 'true' if on else 'false')
        self.style().unpolish(self)
        self.style().polish(self)


class Disclosure(QPushButton):
    """Enlace «▸ Título» que muestra u oculta un widget."""

    def __init__(self, title, target, parent=None):
        super().__init__(parent)
        self.title = title
        self.target = target
        self.setObjectName('link')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self._toggled)
        self._toggled(False)

    def _toggled(self, on):
        self.target.setVisible(on)
        self.setText(('▾' if on else '▸') + '  ' + self.title)
