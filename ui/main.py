import sys
import os
from enum import Enum, auto
from typing import List, Dict

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QToolBar,
    QAction,
    QStyle,
    QMessageBox,
    QStatusBar,
    QGraphicsView,
    QGraphicsScene,
    QDockWidget,
    QSizePolicy,
    QGraphicsLineItem,
    QGraphicsEllipseItem,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QToolButton,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QCheckBox,
)
from PyQt5.QtGui import (
    QFont,
    QColor,
    QPainter,
    QPen,
    QCursor,
    QPixmap,
    QLinearGradient,
    QIcon,
    QPainterPath,
    QTransform,
    QBrush,              # <-- IMPORT QBrush
)
from PyQt5.QtCore import (
    Qt,
    QPointF,
    QRectF,
    pyqtSignal,
    QTimer,
    QLineF,
    QSize,
)

# Integración con el módulo de carga
from core import cat_loader
from core.cat_loader import extraer_primitivas_basicas, Primitive

# ================== Configuración Colores / Recursos ================== #
RESOURCE_LOGO = "assets/logov1.png"
ASSETS_ICONS_DIR = "assets/icons"
COLOR_BG_WORKAREA = QColor("#161b1f")
COLOR_GRID_MINOR = QColor(55, 70, 78)
COLOR_GRID_MAJOR = QColor(74, 105, 116)
COLOR_SNAP = QColor(0, 200, 255)
COLOR_CROSSHAIR = QColor(200, 210, 215, 190)

# LOD thresholds
LOD_BOX_THRESHOLD = 0.03         # escala muy lejana: sólo bbox de capa
LOD_SIMPLE_THRESHOLD = 0.12      # escala media: dibujo sin lineweight/patrones
LOD_TEXT_THRESHOLD = 0.35        # a partir de aquí mostramos textos
LINEWEIGHT_SCALE_BASE = 2.2      # factor base para convertir mm a px aprox


class SnapMode(Enum):
    NONE = auto()
    GRID = auto()


def _resolve_path(*parts):
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def _icon(name: str, fallback_standard_icon: QStyle.StandardPixmap = None) -> QIcon:
    p = _resolve_path(ASSETS_ICONS_DIR, f"{name}.png")
    if os.path.isfile(p):
        return QIcon(p)
    if fallback_standard_icon is not None:
        app = QApplication.instance()
        if app and app.style():
            return app.style().standardIcon(fallback_standard_icon)
    return QIcon()


# ================== Vista de Dibujo ================== #
class DrawingView(QGraphicsView):
    mouseMoved = pyqtSignal(float, float, bool, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(
            QPainter.Antialiasing
            | QPainter.SmoothPixmapTransform
            | QPainter.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(COLOR_BG_WORKAREA)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CrossCursor))

        # Grid
        self.grid_minor = 25
        self.grid_major_factor = 5
        self.grid_color_minor = COLOR_GRID_MINOR
        self.grid_color_major = COLOR_GRID_MAJOR
        self.grid_visible = True

        # Snap
        self.snap_enabled = True
        self.snap_mode = SnapMode.GRID
        self.snap_tolerance_pixels = 14
        self._snap_point = None

        # Crosshair
        self._show_crosshair = True
        self._crosshair_pos = QPointF(0, 0)

        # Crosshair items
        self._h_line_item = QGraphicsLineItem()
        self._v_line_item = QGraphicsLineItem()
        pen_cross = QPen(COLOR_CROSSHAIR, 0)
        self._h_line_item.setPen(pen_cross)
        self._v_line_item.setPen(pen_cross)
        self._h_line_item.setZValue(10_000)
        self._v_line_item.setZValue(10_000)

        # Snap marker
        self._snap_item = QGraphicsEllipseItem(-6, -6, 12, 12)
        pen_snap = QPen(COLOR_SNAP, 2)
        self._snap_item.setPen(pen_snap)
        self._snap_item.setBrush(COLOR_SNAP)
        self._snap_item.setZValue(10_001)
        self._snap_item.setVisible(False)

        # Logo overlay
        self._logo_label = None
        QTimer.singleShot(0, self._init_logo_overlay)

    def setScene(self, scene: QGraphicsScene):
        super().setScene(scene)
        if scene:
            scene.addItem(self._h_line_item)
            scene.addItem(self._v_line_item)
            scene.addItem(self._snap_item)

    def _init_logo_overlay(self):
        if self._logo_label:
            return
        parent = self.viewport()
        self._logo_label = QLabel(parent)
        logo_path = self._resolve_logo_path()
        if os.path.isfile(logo_path):
            pm = QPixmap(logo_path)
            if not pm.isNull():
                scaled = pm.scaledToWidth(180, Qt.SmoothTransformation)
                self._logo_label.setPixmap(scaled)
        self._logo_label.setStyleSheet("background: transparent;")
        self._logo_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._reposition_logo()

    def _resolve_logo_path(self):
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, RESOURCE_LOGO)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_logo()
        self._update_crosshair_lines()

    def _reposition_logo(self):
        if self._logo_label:
            self._logo_label.move(14, 14)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, COLOR_BG_WORKAREA)
        if not self.grid_visible:
            return
        start_x = int(rect.left()) - (int(rect.left()) % self.grid_minor)
        start_y = int(rect.top()) - (int(rect.top()) % self.grid_minor)
        pen_minor = QPen(self.grid_color_minor, 0)
        pen_major = QPen(self.grid_color_major, 0)
        x = start_x
        idx = 0
        right = rect.right()
        bottom = rect.bottom()
        top = rect.top()
        while x <= right:
            painter.setPen(pen_major if (idx % self.grid_major_factor) == 0 else pen_minor)
            painter.drawLine(QLineF(x, top, x, bottom))
            x += self.grid_minor
            idx += 1
        y = start_y
        idy = 0
        left = rect.left()
        while y <= bottom:
            painter.setPen(pen_major if (idy % self.grid_major_factor) == 0 else pen_minor)
            painter.drawLine(QLineF(left, y, right, y))
            y += self.grid_minor
            idy += 1

    def _update_crosshair_lines(self):
        if not self._show_crosshair or self.scene() is None:
            self._h_line_item.setVisible(False)
            self._v_line_item.setVisible(False)
            return
        view_rect = self.viewport().rect()
        top_left = self.mapToScene(view_rect.topLeft())
        bottom_right = self.mapToScene(view_rect.bottomRight())
        x = self._crosshair_pos.x()
        y = self._crosshair_pos.y()
        self._h_line_item.setLine(QLineF(top_left.x(), y, bottom_right.x(), y))
        self._v_line_item.setLine(QLineF(x, top_left.y(), x, bottom_right.y()))
        self._h_line_item.setVisible(True)
        self._v_line_item.setVisible(True)

    def _update_snap_item(self):
        if self._snap_point and self.snap_enabled:
            self._snap_item.setPos(self._snap_point)
            self._snap_item.setVisible(True)
        else:
            self._snap_item.setVisible(False)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        raw_x, raw_y = scene_pos.x(), scene_pos.y()
        snapped = False
        snap_point = None
        if self.snap_enabled and self.snap_mode == SnapMode.GRID:
            snap_point = self._grid_snap(scene_pos)
            if snap_point is not None:
                view_snap = self.mapFromScene(snap_point)
                dist = (view_snap - event.pos())
                if dist.manhattanLength() <= self.snap_tolerance_pixels:
                    snapped = True
                else:
                    snap_point = None
        if snapped:
            self._snap_point = snap_point
            self._crosshair_pos = snap_point
            x_out, y_out = snap_point.x(), snap_point.y()
        else:
            self._snap_point = None
            self._crosshair_pos = scene_pos
            x_out, y_out = raw_x, raw_y
        self._update_crosshair_lines()
        self._update_snap_item()
        self.mouseMoved.emit(x_out, y_out, snapped, raw_x, raw_y)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else (1 / 1.15)
        self.scale(factor, factor)
        self._update_crosshair_lines()
        super().wheelEvent(event)

    def toggle_crosshair(self, value: bool):
        self._show_crosshair = value
        self._update_crosshair_lines()

    def set_snap_enabled(self, enabled: bool):
        self.snap_enabled = enabled
        if not enabled:
            self._snap_point = None
        self._update_snap_item()

    def set_snap_mode(self, mode: SnapMode):
        self.snap_mode = mode
        self._update_snap_item()

    def set_grid_visible(self, visible: bool):
        self.grid_visible = visible
        self.viewport().update()

    def _grid_snap(self, point: QPointF) -> QPointF:
        gx = round(point.x() / self.grid_minor) * self.grid_minor
        gy = round(point.y() / self.grid_minor) * self.grid_minor
        return QPointF(gx, gy)


# ================== Tarjeta de Bienvenida ================== #
class WelcomeCard(QWidget):
    def __init__(self, parent=None, on_new=None, on_open=None):
        super().__init__(parent)
        self.setObjectName("WelcomeCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignCenter)

        logo = QLabel(self)
        logo.setAlignment(Qt.AlignCenter)
        logo_path = _resolve_path(RESOURCE_LOGO)
        if os.path.isfile(logo_path):
            pm = QPixmap(logo_path)
            if not pm.isNull():
                logo.setPixmap(pm.scaledToWidth(220, Qt.SmoothTransformation))
        lay.addWidget(logo)

        title = QLabel("Bienvenido a ComCAD V1", self)
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        subt = QLabel("Editor CAD para planos de telecomunicaciones")
        subt.setAlignment(Qt.AlignCenter)
        lay.addWidget(subt)

        btns = QHBoxLayout()
        btn_new = QPushButton("Nuevo plano")
        btn_new.setIcon(_icon("file-add", QStyle.SP_FileIcon))
        btn_open = QPushButton("Abrir plano")
        btn_open.setIcon(_icon("folder-open", QStyle.SP_DialogOpenButton))
        btn_new.setMinimumHeight(36)
        btn_open.setMinimumHeight(36)
        btn_new.clicked.connect(on_new if on_new else lambda: None)
        btn_open.clicked.connect(on_open if on_open else lambda: None)
        btns.addWidget(btn_new)
        btns.addWidget(btn_open)
        lay.addLayout(btns)

        tips = QLabel("Consejo: Usa el snap a grilla y el crosshair para alinear tus elementos.")
        tips.setWordWrap(True)
        tips.setAlignment(Qt.AlignCenter)
        lay.addWidget(tips)

    def sizeHint(self):
        return QSize(520, 360)


# ================== Diálogos ================== #
class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_units="mm"):
        super().__init__(parent)
        self.setWindowTitle("Configuración - Unidades")
        self.setModal(True)
        self.setObjectName("SettingsDialog")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.cb_units = QComboBox(self)
        self.cb_units.addItems(["mm", "cm", "m", "in", "ft"])
        idx = self.cb_units.findText(current_units)
        if idx >= 0:
            self.cb_units.setCurrentIndex(idx)
        form.addRow("Unidades de medida:", self.cb_units)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_units(self):
        return self.cb_units.currentText()


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acerca de ComCAD")
        self.setModal(True)
        self.setObjectName("AboutDialog")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        logo = QLabel(self)
        logo.setAlignment(Qt.AlignCenter)
        logo_path = _resolve_path(RESOURCE_LOGO)
        if os.path.isfile(logo_path):
            pm = QPixmap(logo_path)
            if not pm.isNull():
                logo.setPixmap(pm.scaledToWidth(140, Qt.SmoothTransformation))
        lay.addWidget(logo)
        title = QLabel("ComCAD V1")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lay.addWidget(title)
        ver = QLabel("Versión: v1.8\n© 2025 Equitelcom")
        ver.setAlignment(Qt.AlignCenter)
        lay.addWidget(ver)
        info = QLabel("Visor de planos CAD (DWG/DXF/PDF) con LOD, lineweight, linetypes y texto básico.")
        info.setAlignment(Qt.AlignCenter)
        info.setWordWrap(True)
        lay.addWidget(info)
        btns = QDialogButtonBox(QDialogButtonBox.Ok, self)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)


# ================== LayerItem (LOD + lineweights + linetypes + texto) ================== #
from PyQt5.QtWidgets import QGraphicsRectItem

class LayerItemBase(QGraphicsRectItem):
    pass


class LayerItem(LayerItemBase):
    def __init__(self, layer_name: str, primitives: List[Primitive], simplify_fn, parent=None):
        bbox = self._compute_bbox(primitives)
        super().__init__(bbox, parent)
        self.setZValue(-20)
        self.layer_name = layer_name
        self.primitives = primitives
        self._simplify_fn = simplify_fn
        self.setPen(QPen(Qt.NoPen))        # <-- CORREGIDO
        self.setBrush(QBrush(Qt.NoBrush))  # <-- CORREGIDO
        self._pattern_cache = {}
        self._font_cache = {}

    @staticmethod
    def _compute_bbox(prims: List[Primitive]) -> QRectF:
        from math import inf
        xmin, ymin, xmax, ymax = inf, inf, -inf, -inf
        for p in prims:
            d = p.data
            t = p.tipo
            try:
                if t == "LINE":
                    for x, y in [(d["x1"], d["y1"]), (d["x2"], d["y2"])]:
                        xmin = min(xmin, x); xmax = max(xmax, x)
                        ymin = min(ymin, y); ymax = max(ymax, y)
                elif t == "POLYLINE":
                    for x, y in d.get("pts", []):
                        xmin = min(xmin, x); xmax = max(xmax, x)
                        ymin = min(ymin, y); ymax = max(ymax, y)
                elif t in ("CIRCLE", "ARC"):
                    r = d["r"]; cx, cy = d["cx"], d["cy"]
                    xmin = min(xmin, cx - r); xmax = max(xmax, cx + r)
                    ymin = min(ymin, cy - r); ymax = max(ymax, cy + r)
                elif t == "TEXT":
                    x, y = d["x"], d["y"]; h = d.get("h", 2.5)
                    xmin = min(xmin, x); xmax = max(xmax, x + h * 0.6)
                    ymin = min(ymin, y); ymax = max(ymax, y + h)
                elif t == "POINT":
                    x, y = d["x"], d["y"]
                    xmin = min(xmin, x); xmax = max(xmax, x)
                    ymin = min(ymin,y); ymax = max(ymax, y)
            except Exception:
                continue
        if xmin == float('inf'):
            xmin = ymin = 0
            xmax = ymax = 0
        pad = 1.5
        return QRectF(xmin - pad, ymin - pad, (xmax - xmin) + 2 * pad, (ymax - ymin) + 2 * pad)

    def paint(self, painter: QPainter, option, widget=None):
        view_scale = painter.worldTransform().m11()
        simplify = self._simplify_fn()

        if view_scale < LOD_BOX_THRESHOLD:
            painter.setPen(QPen(QColor(80, 90, 100), 0))
            painter.drawRect(self.rect())
            return

        pattern_map = {
            "DASHED": [6, 4],
            "HIDDEN": [3, 3],
            "CENTER": [10, 4, 2, 4],
            "PHANTOM": [12, 4, 2, 4, 2, 4],
        }

        detailed = view_scale >= LOD_SIMPLE_THRESHOLD
        show_text = view_scale >= LOD_TEXT_THRESHOLD

        for p in self.primitives:
            d = p.data
            t = p.tipo
            color = p.color if p.color else (200, 200, 200)
            r, g, b = color
            lw100 = d.get("lw", 0)
            lt = (d.get("lt") or "").upper()

            # lineweight
            if not detailed:
                pen_w = 0
            else:
                if lw100 <= 0:
                    pen_w = 0
                else:
                    pen_w = (lw100 / 100.0) * LINEWEIGHT_SCALE_BASE
                    if view_scale > 0.6:
                        pen_w *= 1.15
                    if pen_w < 0.4:
                        pen_w = 0.4

            dash = None
            if detailed and lt in pattern_map:
                dash = pattern_map[lt]

            pen = QPen(QColor(r, g, b), pen_w)
            if pen_w == 0:
                pen.setCosmetic(True)
            if dash:
                pen.setDashPattern(dash)
            painter.setPen(pen)

            try:
                if t == "LINE":
                    painter.drawLine(d["x1"], d["y1"], d["x2"], d["y2"])

                elif t == "POLYLINE":
                    pts = d.get("pts", [])
                    if not pts:
                        continue
                    if simplify and len(pts) > 400 and view_scale < 0.5:
                        step = 3
                    else:
                        step = 1
                    for i in range(0, len(pts) - 1, step):
                        x1, y1 = pts[i]
                        x2, y2 = pts[i + 1]
                        painter.drawLine(x1, y1, x2, y2)
                    if d.get("closed") and len(pts) > 2:
                        painter.drawLine(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1])

                elif t == "CIRCLE":
                    painter.drawEllipse(QPointF(d["cx"], d["cy"]), d["r"], d["r"])

                elif t == "ARC":
                    cx, cy, r_ = d["cx"], d["cy"], d["r"]
                    start = d["start"]
                    end = d["end"]
                    sweep = end - start
                    if sweep < 0:
                        sweep += 360
                    rect = QRectF(cx - r_, cy - r_, r_ * 2, r_ * 2)
                    path = QPainterPath()
                    path.arcMoveTo(rect, start)
                    path.arcTo(rect, start, sweep)
                    painter.drawPath(path)

                elif t == "TEXT" and show_text:
                    txt = d.get("value", "")
                    if not txt.strip():
                        continue
                    x, y = d["x"], d["y"]
                    h = d.get("h", 2.5)
                    rot = d.get("rot", 0.0)
                    painter.save()
                    painter.translate(x, y)
                    if rot:
                        painter.rotate(rot)
                    f = painter.font()
                    f.setPointSizeF(h * view_scale * 1.4)
                    painter.setFont(f)
                    painter.setPen(QColor(r, g, b))
                    painter.drawText(0, 0, txt)
                    painter.restore()

                elif t== "POINT":
                    x, y = d["x"], d["y"]
                    size = 3 + (pen_w if pen_w > 0 else 1)
                    painter.drawEllipse(QPointF(x, y), size, size)
                    

            except Exception:
                continue


# ================== Ventana Principal ================== #
class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ComCAD V1_Equitelcom")
        background_color = QColor("#0d4d63")
        self.setStyleSheet(f"QMainWindow {{ background-color: {background_color.name()}; }}")
        self.setMinimumSize(1100, 720)
        self.resize(1400, 860)

        # Estado
        self._has_document = False
        self.current_file = None
        self.items_count = 0
        self.current_mode = "Selección"
        self.units = "mm"

        # Tracking
        self._loaded_items: List = []
        self._layer_items: Dict[str, LayerItem] = {}
        self._layer_visible: Dict[str, bool] = {}
        self._last_primitives: List[Primitive] = []
        self._simplify_render = True

        self._cargar_stylesheet()
        self._crear_centro()
        self._crear_docks()
        self._crear_toolbar()
        self._crear_menubar()
        self._crear_statusbar()
        self._conectar_signals()

        QTimer.singleShot(200, self._show_initial_config)

    # ----- Estilos -----
    def _cargar_stylesheet(self):
        base = os.path.dirname(os.path.abspath(__file__))
        css_path = os.path.join(base, "styles.css")
        if os.path.isfile(css_path):
            try:
                with open(css_path, "r", encoding="utf-8") as f:
                    self.setStyleSheet(f.read())
            except Exception:
                self.setStyleSheet("QMainWindow { background:#0d4d63; }")
        else:
            self.setStyleSheet("QMainWindow { background:#0d4d63; }")

    # ----- Centro -----
    def _crear_centro(self):
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(-3000, -3000, 6000, 6000)
        self.graphics_view = DrawingView()
        self.graphics_view.setScene(self.scene)
        self.graphics_view.setObjectName("graphicsView")
        self.setCentralWidget(self.graphics_view)
        self.graphics_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._welcome = WelcomeCard(
            parent=self.graphics_view.viewport(),
            on_new=self._on_new,
            on_open=self._on_open,
        )
        self._welcome.hide()
        self._update_welcome_card()
        self.graphics_view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.graphics_view.viewport() and event.type() == event.Resize:
            self._position_welcome_card()
        return super().eventFilter(obj, event)

    def _position_welcome_card(self):
        if not self._welcome:
            return
        r = self.graphics_view.viewport().rect()
        size = self._welcome.sizeHint()
        self._welcome.setGeometry(
            r.center().x() - size.width() // 2,
            r.center().y() - size.height() // 2,
            size.width(),
            size.height()
        )

    def _update_welcome_card(self):
        if self._has_document:
            self._welcome.hide()
        else:
            self._welcome.show()
            self._position_welcome_card()

    # ----- Docks -----
    def _crear_docks(self):
        # Herramientas
        self.dock_left = QDockWidget("Herramientas", self)
        self.dock_left.setObjectName("dockLeft")
        self.dock_left.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        left_container = QWidget()
        lay = QVBoxLayout(left_container)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        header_row = QHBoxLayout()
        header_lbl = QLabel("Acciones rápidas")
        header_lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
        btn_collapse = QToolButton()
        btn_collapse.setText("◀ Ocultar")
        btn_collapse.setToolTip("Plegar panel de herramientas")
        btn_collapse.clicked.connect(lambda: self.dock_left.hide())
        header_row.addWidget(header_lbl)
        header_row.addStretch()
        header_row.addWidget(btn_collapse)
        lay.addLayout(header_row)

        self.btn_open = QPushButton(" Abrir plano")
        self.btn_open.setIcon(_icon("folder-open", QStyle.SP_DialogOpenButton))
        self.btn_insert = QPushButton(" Insertar símbolo")
        self.btn_insert.setIcon(_icon("plus-symbol", QStyle.SP_DialogYesButton))
        self.btn_draw = QPushButton(" Dibujar canalización")
        self.btn_draw.setIcon(_icon("line", QStyle.SP_ArrowForward))
        self.btn_report = QPushButton(" Generar reporte")
        self.btn_report.setIcon(_icon("report", QStyle.SP_FileDialogDetailedView))
        for b in (self.btn_open, self.btn_insert, self.btn_draw, self.btn_report):
            b.setMinimumHeight(40)
            lay.addWidget(b)
        lay.addStretch()
        self.dock_left.setWidget(left_container)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_left)

        # Propiedades
        self.dock_right = QDockWidget("Propiedades", self)
        self.dock_right.setObjectName("dockRight")
        self.dock_right.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        right_container = QWidget()
        rlay = QVBoxLayout(right_container)
        lbl_prop = QLabel("Inspector / Propiedades")
        f = QFont(); f.setBold(True)
        lbl_prop.setFont(f)
        rlay.addWidget(lbl_prop)
        form = QFormLayout()
        self.prop_type = QComboBox(); self.prop_type.addItems(["Símbolo", "Canalización", "Texto", "Otro"])
        self.prop_x = QDoubleSpinBox(); self.prop_x.setDecimals(3); self.prop_x.setRange(-1e6, 1e6)
        self.prop_y = QDoubleSpinBox(); self.prop_y.setDecimals(3); self.prop_y.setRange(-1e6, 1e6)
        self.prop_angle = QDoubleSpinBox(); self.prop_angle.setDecimals(2); self.prop_angle.setRange(-360.0, 360.0)
        self.prop_range = QLineEdit(); self.prop_range.setPlaceholderText("Rango/Longitud (placeholder)")
        form.addRow("Tipo de objeto:", self.prop_type)
        form.addRow("Coordenada X:", self.prop_x)
        form.addRow("Coordenada Y:", self.prop_y)
        form.addRow("Ángulo (°):", self.prop_angle)
        form.addRow("Rango:", self.prop_range)
        rlay.addLayout(form)
        self.btn_apply_props = QPushButton("Aplicar cambios")
        rlay.addWidget(self.btn_apply_props)
        rlay.addStretch()
        self.dock_right.setWidget(right_container)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_right)

        # Capas
        self.dock_layers = QDockWidget("Capas", self)
        self.dock_layers.setObjectName("dockLayers")
        self.dock_layers.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        layers_container = QWidget()
        self._layers_layout = QVBoxLayout(layers_container)
        self._layers_layout.setContentsMargins(8, 8, 8, 8)
        self._layers_layout.setSpacing(4)
        self._layers_layout.addStretch()
        self.dock_layers.setWidget(layers_container)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_layers)

    # ----- Toolbar -----
    def _crear_toolbar(self):
        tb = QToolBar("Principal")
        tb.setIconSize(QSize(28, 28))
        self.addToolBar(Qt.TopToolBarArea, tb)

        act_abrir = QAction(_icon("folder-open", QStyle.SP_DialogOpenButton), "Abrir", self)
        act_nuevo = QAction(_icon("file-add", QStyle.SP_FileIcon), "Nuevo", self)
        act_guardar = QAction(_icon("save", QStyle.SP_DialogSaveButton), "Guardar", self)
        act_export_pdf = QAction(_icon("export-pdf", QStyle.SP_DriveDVDIcon), "Exportar PDF", self)
        act_insertar = QAction(_icon("plus-symbol", QStyle.SP_DialogYesButton), "Insertar símbolo", self)
        act_dibujar = QAction(_icon("line", QStyle.SP_ArrowForward), "Dibujar canalización", self)
        act_reporte = QAction(_icon("report", QStyle.SP_FileDialogDetailedView), "Generar reporte", self)

        act_zoom_in = QAction("Zoom +", self)
        act_zoom_out = QAction("Zoom -", self)
        act_fit = QAction("Ajustar", self)
        act_crosshair = QAction("Crosshair", self); act_crosshair.setCheckable(True); act_crosshair.setChecked(True)
        act_snap_enable = QAction("Snap", self); act_snap_enable.setCheckable(True); act_snap_enable.setChecked(True)
        act_snap_grid = QAction("Grid Snap", self); act_snap_grid.setCheckable(True); act_snap_grid.setChecked(True)
        act_grid_visible = QAction("Mostrar cuadrícula", self); act_grid_visible.setCheckable(True); act_grid_visible.setChecked(True)
        act_simplify = QAction("Simplificar", self); act_simplify.setCheckable(True); act_simplify.setChecked(True)
        act_toggle_left = QAction("Panel Izq", self); act_toggle_left.setCheckable(True); act_toggle_left.setChecked(True)
        act_toggle_right = QAction("Panel Der", self); act_toggle_right.setCheckable(True); act_toggle_right.setChecked(True)

        for a in (act_nuevo, act_abrir, act_guardar, act_export_pdf): tb.addAction(a)
        tb.addSeparator()
        for a in (act_insertar, act_dibujar, act_reporte): tb.addAction(a)
        tb.addSeparator()
        for a in (act_zoom_in, act_zoom_out, act_fit): tb.addAction(a)
        tb.addSeparator()
        tb.addAction(act_crosshair)
        tb.addAction(act_grid_visible)
        tb.addAction(act_simplify)
        tb.addSeparator()
        tb.addAction(act_snap_enable)
        tb.addAction(act_snap_grid)
        tb.addSeparator()
        tb.addAction(act_toggle_left)
        tb.addAction(act_toggle_right)

        self.act_zoom_in = act_zoom_in
        self.act_zoom_out = act_zoom_out
        self.act_fit = act_fit
        self.act_crosshair = act_crosshair
        self.act_snap_enable = act_snap_enable
        self.act_snap_grid = act_snap_grid
        self.act_toggle_left = act_toggle_left
        self.act_toggle_right = act_toggle_right
        self.act_grid_visible = act_grid_visible
        self.act_simplify = act_simplify

        act_abrir.triggered.connect(self._on_open)
        act_nuevo.triggered.connect(self._on_new)
        act_guardar.triggered.connect(self._on_save)
        act_export_pdf.triggered.connect(self._on_export_pdf)
        act_insertar.triggered.connect(self._on_insert)
        act_dibujar.triggered.connect(self._on_draw)
        act_reporte.triggered.connect(self._on_report)
        act_simplify.toggled.connect(self._on_toggle_simplify)

    # ----- Menú -----
    def _crear_menubar(self):
        mb = self.menuBar()
        # Archivo
        m_file = mb.addMenu("Archivo")
        self.m_file_new = QAction("Nuevo", self)
        self.m_file_open = QAction("Abrir", self)
        self.m_file_save = QAction("Guardar", self)
        self.m_file_export_pdf = QAction("Exportar PDF", self)
        self.m_file_settings = QAction("Configuración…", self)
        self.m_file_exit = QAction("Salir", self)
        for a in (self.m_file_new, self.m_file_open, self.m_file_save, self.m_file_export_pdf):
            m_file.addAction(a)
        m_file.addSeparator()
        m_file.addAction(self.m_file_settings)
        m_file.addSeparator()
        m_file.addAction(self.m_file_exit)

        # Edición
        m_edit = mb.addMenu("Edición")
        self.m_edit_undo = QAction("Deshacer", self)
        self.m_edit_redo = QAction("Rehacer", self)
        self.m_edit_delete = QAction("Eliminar elemento", self)
        m_edit.addAction(self.m_edit_undo)
        m_edit.addAction(self.m_edit_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.m_edit_delete)

        # Ver
        m_view = mb.addMenu("Ver")
        self.m_view_zoom_in = QAction("Zoom +", self)
        self.m_view_zoom_out = QAction("Zoom -", self)
        self.m_view_fit = QAction("Ajustar vista", self)
        self.m_view_grid = QAction("Mostrar cuadrícula", self, checkable=True, checked=True)
        self.m_view_crosshair = QAction("Mostrar crosshair", self, checkable=True, checked=True)
        self.m_view_simplify = QAction("Simplificar dibujo", self, checkable=True, checked=True)
        self.m_view_panels = m_view.addMenu("Paneles")
        self.m_view_left = QAction("Panel izquierdo", self, checkable=True, checked=True)
        self.m_view_right = QAction("Panel derecho", self, checkable=True, checked=True)

        for a in (self.m_view_zoom_in, self.m_view_zoom_out, self.m_view_fit):
            m_view.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.m_view_grid)
        m_view.addAction(self.m_view_crosshair)
        m_view.addAction(self.m_view_simplify)
        self.m_view_panels.addAction(self.m_view_left)
        self.m_view_panels.addAction(self.m_view_right)

        # Ayuda
        m_help = mb.addMenu("Ayuda")
        self.m_help_about = QAction("Acerca de ComCAD", self)
        m_help.addAction(self.m_help_about)

        # Conexiones menú
        self.m_file_new.triggered.connect(self._on_new)
        self.m_file_open.triggered.connect(self._on_open)
        self.m_file_save.triggered.connect(self._on_save)
        self.m_file_export_pdf.triggered.connect(self._on_export_pdf)
        self.m_file_settings.triggered.connect(self._on_settings)
        self.m_file_exit.triggered.connect(self.close)
        self.m_edit_undo.triggered.connect(lambda: self.statusBar().showMessage("Deshacer (pendiente)", 2500))
        self.m_edit_redo.triggered.connect(lambda: self.statusBar().showMessage("Rehacer (pendiente)", 2500))
        self.m_edit_delete.triggered.connect(lambda: self.statusBar().showMessage("Eliminar (pendiente)", 2500))
        self.m_view_zoom_in.triggered.connect(lambda: self._zoom(1.15))
        self.m_view_zoom_out.triggered.connect(lambda: self._zoom(1 / 1.15))
        self.m_view_fit.triggered.connect(self._fit_to_content)
        self.m_view_grid.toggled.connect(self._toggle_grid)
        self.m_view_crosshair.toggled.connect(self.graphics_view.toggle_crosshair)
        self.m_view_simplify.toggled.connect(self._on_toggle_simplify)
        self.m_view_left.toggled.connect(lambda c: self.dock_left.setVisible(c))
        self.m_view_right.toggled.connect(lambda c: self.dock_right.setVisible(c))
        self.m_help_about.triggered.connect(self._on_about)

    # ----- StatusBar -----
    def _crear_statusbar(self):
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.coords_label = QLabel("X: 0.000  Y: 0.000 (snap)")
        self.raw_label = QLabel("Raw: 0.000, 0.000")
        self.items_label = QLabel("Capas: 0/0  Primitivas: 0")
        self.file_label = QLabel("Archivo: ninguno")
        self.mode_label = QLabel("Modo: Selección")
        status.addWidget(self.coords_label)
        status.addWidget(self.raw_label)
        status.addPermanentWidget(self.items_label)
        status.addPermanentWidget(self.file_label)
        status.addPermanentWidget(self.mode_label)

    # ----- Conexiones -----
    def _conectar_signals(self):
        self.graphics_view.mouseMoved.connect(self._on_mouse_moved)
        self.btn_open.clicked.connect(self._on_open)
        self.btn_insert.clicked.connect(self._on_insert)
        self.btn_draw.clicked.connect(self._on_draw)
        self.btn_report.clicked.connect(self._on_report)
        self.btn_apply_props.clicked.connect(self._on_apply_props)
        self.act_zoom_in.triggered.connect(lambda: self._zoom(1.15))
        self.act_zoom_out.triggered.connect(lambda: self._zoom(1 / 1.15))
        self.act_fit.triggered.connect(self._fit_to_content)
        self.act_crosshair.toggled.connect(self.graphics_view.toggle_crosshair)
        self.act_snap_enable.toggled.connect(self.graphics_view.set_snap_enabled)
        self.act_snap_grid.toggled.connect(self._on_snap_grid_toggled)
        self.act_toggle_left.toggled.connect(lambda c: self.dock_left.setVisible(c))
        self.act_toggle_right.toggled.connect(lambda c: self.dock_right.setVisible(c))
        self.act_grid_visible.toggled.connect(self._toggle_grid)
        self.act_simplify.toggled.connect(self._on_toggle_simplify)
        self.dock_left.visibilityChanged.connect(lambda _: self._sync_panel_actions())
        self.dock_right.visibilityChanged.connect(lambda _: self._sync_panel_actions())
        self.dock_layers.visibilityChanged.connect(lambda _: self._sync_panel_actions())

    # ----- Paint -----
    def paintEvent(self, event):
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor("#0b3446"))
        grad.setColorAt(0.35, QColor("#0d4d63"))
        grad.setColorAt(1.0, QColor("#0e5d73"))
        painter.fillRect(self.rect(), grad)
        super().paintEvent(event)

    # ----- Utilidades -----
    def _zoom(self, factor: float):
        self.graphics_view.scale(factor, factor)
        self.graphics_view._update_crosshair_lines()

    def _fit_to_content(self):
        if self.scene.items():
            self.graphics_view.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
        else:
            self.graphics_view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.graphics_view._update_crosshair_lines()

    def _on_snap_grid_toggled(self, checked: bool):
        self.graphics_view.set_snap_mode(SnapMode.GRID if checked else SnapMode.NONE)

    def _toggle_grid(self, checked: bool):
        self.graphics_view.set_grid_visible(checked)
        self.act_grid_visible.blockSignals(True)
        self.act_grid_visible.setChecked(checked)
        self.act_grid_visible.blockSignals(False)
        self.m_view_grid.blockSignals(True)
        self.m_view_grid.setChecked(checked)
        self.m_view_grid.blockSignals(False)

    def _sync_panel_actions(self):
        left_v = self.dock_left.isVisible()
        right_v = self.dock_right.isVisible()
        for act in (self.act_toggle_left, self.m_view_left):
            act.blockSignals(True); act.setChecked(left_v); act.blockSignals(False)
        for act in (self.act_toggle_right, self.m_view_right):
            act.blockSignals(True); act.setChecked(right_v); act.blockSignals(False)

    def _set_mode(self, mode_text: str):
        self.current_mode = mode_text
        self.mode_label.setText(f"Modo: {mode_text}")

    def _set_current_file(self, path: str = None):
        self.current_file = path
        shown = os.path.basename(path) if path else "ninguno"
        self.file_label.setText(f"Archivo: {shown}")

    def _update_items_label(self):
        capas = len(self._layer_items)
        visibles = sum(1 for v in self._layer_visible.values() if v)
        self.items_label.setText(f"Capas: {visibles}/{capas}  Primitivas: {self.items_count}")

    def _clear_loaded_items(self):
        for it in self._loaded_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self._loaded_items.clear()
        self._layer_items.clear()
        self._layer_visible.clear()
        self._last_primitives.clear()
        if hasattr(self, '_layers_layout'):
            while self._layers_layout.count() > 1:
                w = self._layers_layout.itemAt(0).widget()
                if w:
                    w.setParent(None)
        self.items_count = 0
        self._update_items_label()

    # ----- Acciones -----
    def _on_new(self):
        self._clear_loaded_items()
        self._has_document = True
        self._set_current_file("nuevo_plano.dxf")
        self._update_welcome_card()
        self.statusBar().showMessage("Nuevo plano (placeholder)", 2500)

    def _on_open(self):
        path = cat_loader.abrir_archivo(self)
        if not path:
            return
        result = cat_loader.cargar_archivo(path)
        if not result.ok:
            msg = f"Error cargando: {result.error.message}"
            if result.error.detail:
                msg += f" ({result.error.detail})"
            self.statusBar().showMessage(msg, 7000)
            return
        self._clear_loaded_items()
        self._has_document = True
        self._set_current_file(path)
        self._update_welcome_card()
        self.statusBar().showMessage(cat_loader.descripcion_corta(result), 5000)

        if cat_loader.es_pdf(result):
            pm = result.pdf.first_page_pixmap
            if pm:
                item = QGraphicsPixmapItem(pm)
                item.setZValue(-100)
                self.scene.addItem(item)
                self._loaded_items.append(item)
        elif cat_loader.es_dwg(result):
            try:
                prims = extraer_primitivas_basicas(result.dwg.path,
                                                   expand_blocks=True,
                                                   incluir_texto=True)
            except Exception as e:
                prims = []
                self.statusBar().showMessage(f"Error extrayendo primitivas: {e}", 6000)
            if not prims:
                info = result.dwg
                if info and info.extents:
                    xmin, ymin, xmax, ymax = info.extents
                    rect_item = QGraphicsRectItem(QRectF(xmin, ymin, xmax - xmin, ymax - ymin))
                    rect_item.setPen(QPen(QColor('#4fc3f7'), 0))
                    rect_item.setZValue(-50)
                    self.scene.addItem(rect_item)
                    self._loaded_items.append(rect_item)
                    self.items_count = 1
            else:
                self._build_layer_items(prims)

        self._fit_to_content()
        self._update_items_label()

    def _build_layer_items(self, prims: List[Primitive]):
        self._last_primitives = prims
        if hasattr(self, '_layers_layout'):
            while self._layers_layout.count() > 1:
                w = self._layers_layout.itemAt(0).widget()
                if w:
                    w.setParent(None)

        layer_map: Dict[str, List[Primitive]] = {}
        for p in prims:
            layer_map.setdefault(p.layer, []).append(p)

        for layer, plist in layer_map.items():
            item = LayerItem(layer, plist, lambda: self._simplify_render)
            self.scene.addItem(item)
            self._layer_items[layer] = item
            self._layer_visible[layer] = True
            self._loaded_items.append(item)
            cb = QCheckBox(layer)
            cb.setChecked(True)
            cb.stateChanged.connect(lambda state, lay=layer: self._on_layer_toggle(lay, state == Qt.Checked))
            self._layers_layout.insertWidget(self._layers_layout.count() - 1, cb)

        self.items_count = len(prims)
        self._update_items_label()

    def _on_layer_toggle(self, layer, visible):
        self._layer_visible[layer] = visible
        item = self._layer_items.get(layer)
        if item:
            item.setVisible(visible)
        self._update_items_label()

    def _on_save(self):
        self.statusBar().showMessage("Guardar (pendiente)", 3000)

    def _on_export_pdf(self):
        self.statusBar().showMessage("Exportar PDF (pendiente)", 3000)

    def _on_insert(self):
        self._set_mode("Inserción")
        self.items_count += 1
        self._update_items_label()
        self.statusBar().showMessage("Insertar símbolo (placeholder)", 3000)

    def _on_draw(self):
        self._set_mode("Dibujo")
        self.items_count += 1
        self._update_items_label()
        self.statusBar().showMessage("Dibujar canalización (placeholder)", 3000)

    def _on_report(self):
        self.statusBar().showMessage("Generar reporte (pendiente)", 3000)

    def _on_apply_props(self):
        self.statusBar().showMessage("Aplicar cambios (placeholder)", 2500)

    def _on_settings(self):
        dlg = SettingsDialog(self, current_units=self.units)
        if dlg.exec_() == QDialog.Accepted:
            self.units = dlg.selected_units()
            self.statusBar().showMessage(f"Unidades actualizadas a: {self.units}", 2500)

    def _on_about(self):
        AboutDialog(self).exec_()

    def _on_mouse_moved(self, x, y, snapped, raw_x, raw_y):
        if snapped:
            self.coords_label.setText(f"X: {x:,.3f}  Y: {y:,.3f} (snap)")
        else:
            self.coords_label.setText(f"X: {x:,.3f}  Y: {y:,.3f}")
        self.raw_label.setText(f"Raw: {raw_x:,.3f}, {raw_y:,.3f}")

    def _show_initial_config(self):
        if not getattr(self, "_config_shown", False):
            self._config_shown = True
            self._on_settings()

    def _on_toggle_simplify(self, checked: bool):
        self._simplify_render = checked
        if hasattr(self, 'm_view_simplify'):
            self.m_view_simplify.blockSignals(True)
            self.m_view_simplify.setChecked(checked)
            self.m_view_simplify.blockSignals(False)
        if hasattr(self, 'act_simplify'):
            self.act_simplify.blockSignals(True)
            self.act_simplify.setChecked(checked)
            self.act_simplify.blockSignals(False)
        for item in self._layer_items.values():
            item.update()

    # ----- Cierre -----
    def closeEvent(self, event):
        dlg = QMessageBox(self)
        dlg.setObjectName("closeConfirm")
        dlg.setWindowTitle("Confirmar cierre")
        dlg.setText("¿Desea cerrar ComCAD V1?")
        dlg.setInformativeText("Se perderán los cambios no guardados.")
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dlg.setDefaultButton(QMessageBox.No)
        yes_btn = dlg.button(QMessageBox.Yes)
        no_btn = dlg.button(QMessageBox.No)
        if yes_btn: yes_btn.setText("Sí")
        if no_btn: no_btn.setText("No")
        dlg.setStyleSheet("""
        QMessageBox#closeConfirm {
            background-color: #103949;
            border: 1px solid #19a7d8;
            border-radius: 10px;
        }
        QMessageBox#closeConfirm QLabel { color: #eaf7fb; font-size: 14px; }
        QMessageBox#closeConfirm QPushButton {
            background: #0f6f90; color: #e8f8fc; border: 1px solid #1093c0;
            padding: 6px 14px; border-radius: 6px; min-width: 80px; font-weight: 600;
        }
        QMessageBox#closeConfirm QPushButton:hover { background: #129fd1; }
        QMessageBox#closeConfirm QPushButton:pressed { background: #0b5d78; }
        """)
        ret = dlg.exec_()
        if ret == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    win = VentanaPrincipal()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()