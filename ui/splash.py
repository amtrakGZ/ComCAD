import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QGraphicsDropShadowEffect, QMessageBox
)
from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSlot
)
from PyQt5.QtGui import (
    QPixmap, QPainter, QLinearGradient, QColor, QBrush, QFont
)

# ---------------- CONFIG RÁPIDA ---------------- #
WIDTH = 720
HEIGHT = 360
CARD_MARGIN = 40
LOGO_RATIO = 0.50         # % del ancho/alto
BAR_WIDTH_RATIO = 0.62
PROGRESS_HEIGHT = 12
DOTS_INTERVAL_MS = 550
FAKE_PROGRESS_DURATION_MS = 8000   # Duración animación barra (ms)
FINISH_DELAY_MS = 300              # Pequeña pausa tras 100%
FADE_IN_MS = 350
FADE_OUT_MS = 300
USE_FADE = True
SHADOW = True                     # Pon True si quieres sombra (ligero coste)
ALLOW_SKIP = True
# Paths relative to project root (parent of this file's directory)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(ROOT_DIR, "assets", "logov1.png")
STYLES_PATH = os.path.join(ROOT_DIR, "styles", "styles.css")

# ---------------- SPLASH ---------------- #
class FastSplash(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Ventana sin marco y siempre arriba
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(WIDTH, HEIGHT)

        self._dot_state = 0
        self._anim = None
        self._fade = None
        self._finished = False

        self._build_ui()
        # MOSTRAR INMEDIATAMENTE (para evitar retardo visual):
        self.show()
        if USE_FADE:
            self._start_fade_in()
        else:
            self.setWindowOpacity(1.0)

        self._start_dots()
        self._start_fake_progress()

    # ---------- UI ---------- #
    def _build_ui(self):
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)

        # Card interna (contenedor)
        self.card = QWidget(self)
        self.card.setObjectName("card")
        self.card.setAttribute(Qt.WA_TranslucentBackground, True)
        self.card.setGeometry(
            CARD_MARGIN, CARD_MARGIN,
            self.width() - CARD_MARGIN * 2,
            self.height() - CARD_MARGIN * 2
        )

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(40, 28, 40, 28)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)

        # Logo
        self.logo_label = QLabel(self.card)
        self.logo_label.setAlignment(Qt.AlignCenter)
        self._load_logo()
        lay.addWidget(self.logo_label)

        # Título
        self.title = QLabel("ComCAD V1", self.card)
        self.title.setStyleSheet("color:white;")
        self.title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)

        # Subtítulo
        self.subtitle = QLabel("Interfaz de gestión CAD", self.card)
        self.subtitle.setStyleSheet("color:rgba(255,255,255,0.9);")
        self.subtitle.setFont(QFont("Segoe UI", 10))
        self.subtitle.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.subtitle)

        lay.addStretch(1)

        # Barra de progreso + porcentaje
        self.progress = QProgressBar(self.card)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(int(self.width() * BAR_WIDTH_RATIO), PROGRESS_HEIGHT)
        self.progress.setStyleSheet(
            "QProgressBar {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "  stop:0 rgba(255,255,255,0.04), stop:1 rgba(0,0,0,0.08));"
            " border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 12px; padding:2px;"
            "}"
            "QProgressBar::chunk {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "  stop:0 #6ec1ff, stop:0.5 #a6d8ff, stop:1 #ffffff);"
            " border-radius: 12px; margin:0;"
            "}"
        )

        self.percent = QLabel("0%", self.card)
        self.percent.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.percent.setStyleSheet("color:white; background:transparent;")
        self.percent.setFixedHeight(PROGRESS_HEIGHT)
        self.percent.setAlignment(Qt.AlignCenter)
        self.percent.setMinimumWidth(54)

        hbar = QHBoxLayout()
        hbar.setSpacing(12)
        hbar.addWidget(self.progress)
        hbar.addWidget(self.percent)
        lay.addLayout(hbar)

        # Texto de carga
        self.loading = QLabel("Cargando", self.card)
        self.loading.setStyleSheet("color:rgba(255,255,255,0.9);")
        self.loading.setFont(QFont("Segoe UI", 9))
        self.loading.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.loading)

        if SHADOW:
            # Guardar la referencia en self evita que el efecto pueda ser recolectado
            # y mejora la estabilidad de los repaints de hijos animados (ej. QProgressBar).
            self._shadow_effect = QGraphicsDropShadowEffect(self)
            self._shadow_effect.setBlurRadius(24)
            self._shadow_effect.setOffset(0, 6)
            self._shadow_effect.setColor(QColor(0, 0, 0, 110))
            self.card.setGraphicsEffect(self._shadow_effect)

        if ALLOW_SKIP:
            self.setToolTip("Presiona ESC para omitir")

    def _load_logo(self):
        path = LOGO_PATH
        pm = QPixmap(path)
        if pm.isNull():
            self.logo_label.setText("")
            return
        target_w = int(self.width() * LOGO_RATIO)
        target_h = int(self.height() * LOGO_RATIO)
        self.logo_label.setPixmap(
            pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    # ---------- Pintura de fondo (gradiente + card translúcida) ---------- #
    def paintEvent(self, event):
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor("#052f4f"))
        grad.setColorAt(0.6, QColor("#0e4b66"))
        grad.setColorAt(1.0, QColor("#2b7a8f"))
        painter.fillRect(self.rect(), QBrush(grad))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(
            CARD_MARGIN, CARD_MARGIN,
            self.width() - 2 * CARD_MARGIN,
            self.height() - 2 * CARD_MARGIN,
            14, 14
        )

    # ---------- Animaciones ---------- #
    def _start_fake_progress(self):
        # Animación lineal simple (corta) sin conversiones extras
        self._anim = QPropertyAnimation(self.progress, b"value", self)
        self._anim.setDuration(FAKE_PROGRESS_DURATION_MS)
        self._anim.setStartValue(0)
        self._anim.setEndValue(100)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_progress_value)
        self._anim.finished.connect(self._on_progress_finished)
        self._anim.start()

    @pyqtSlot("QVariant")
    def _on_progress_value(self, v):
        # En algunos sistemas, con un QGraphicsDropShadowEffect activo, la animación
        # puede no repintar visualmente si sólo se usa el QPropertyAnimation implícito.
        # Forzamos el setValue y un update manual para asegurar que el chunk se dibuje.
        iv = int(v)
        if self.progress.value() != iv:
            self.progress.setValue(iv)
        self.percent.setText(f"{iv}%")
        self.progress.update()

    def _on_progress_finished(self):
        QTimer.singleShot(FINISH_DELAY_MS, self.finish)

    def _start_fade_in(self):
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_IN_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade.start()

    def _start_fade_out(self, callback):
        if not USE_FADE:
            callback()
            return
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_OUT_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade.finished.connect(callback)
        self._fade.start()

    # ---------- Dots ---------- #
    def _start_dots(self):
        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.start(DOTS_INTERVAL_MS)

    def _tick_dots(self):
        self._dot_state = (self._dot_state + 1) % 4
        self.loading.setText("Cargando" + "." * self._dot_state)

    # ---------- Finish ---------- #
    def finish(self):
        if self._finished:
            return
        self._finished = True
        if self._dots_timer:
            self._dots_timer.stop()
        self._start_fade_out(self._final_close)

    def _final_close(self):
        self.close()
        self._launch_main()

    def _launch_main(self):
        try:
            # Import as package to work when run from app.py
            from ui.main import VentanaPrincipal
            self._main_window = VentanaPrincipal()
            self._main_window.show()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"No se pudo abrir ventana principal:\n{e}")

    # ---------- Skip ---------- #
    def keyPressEvent(self, event):
        if ALLOW_SKIP and event.key() == Qt.Key_Escape:
            self.finish()
        else:
            super().keyPressEvent(event)


# ---------------- FUNCIÓN PRINCIPAL ---------------- #
def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # Load global stylesheet so both splash and main share it
    try:
        if os.path.isfile(STYLES_PATH):
            with open(STYLES_PATH, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
    except Exception:
        pass

    splash = FastSplash()
    # Keep running
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

# ---------------- FIN DEL CÓDIGO ---------------- #