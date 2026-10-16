"""Componentes visuais reutilizáveis.

Todos são widgets "burros": recebem valores prontos e se desenham. Nenhum
deles conhece câmera, banco ou regras de detecção.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.drowsiness_analyzer import DriverState
from ui.icons import icon, pixmap
from ui.styles import STATE_COLORS, STATE_ICONS, Palette, qcolor


def caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Caption")
    return label


def styled_label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def make_button(text: str, icon_name: str | None = None, name: str = "",
                icon_color: str = Palette.TEXT) -> QPushButton:
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    if icon_name:
        button.setIcon(icon(icon_name, icon_color, 18))
        button.setIconSize(QSize(18, 18))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def repolish(widget: QWidget) -> None:
    """Reaplica o QSS após mudar uma propriedade dinâmica usada em seletores."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Card(QFrame):
    """Superfície elevada com título opcional."""

    def __init__(self, title: str | None = None, icon_name: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 18)
        self.body.setSpacing(12)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            if icon_name:
                badge = QLabel()
                badge.setPixmap(pixmap(icon_name, Palette.TEXT_MUTED, 16))
                self.header.addWidget(badge)
            self.header.addWidget(caption(title))
            self.header.addStretch(1)
            self.body.addLayout(self.header)


# --------------------------------------------------------------------------- #
# Estado do motorista
# --------------------------------------------------------------------------- #
class StatusPanel(QFrame):
    """Painel grande com o estado atual, motivo e animação de cor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(138)
        self._color = qcolor(Palette.IDLE)
        self._glow = 0.0
        self._state: DriverState | None | str = "init"

        self._color_anim = QVariantAnimation(self, duration=280)
        self._color_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._color_anim.valueChanged.connect(self._on_color)
        self._pulse = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=900)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.valueChanged.connect(self._on_pulse)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 18, 20, 18)
        layout.setSpacing(16)
        self._badge = QLabel()
        self._badge.setFixedSize(48, 48)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(caption("Estado do motorista"))
        self._title = QLabel()
        title_font = QFont(self.font())
        title_font.setPixelSize(22)
        title_font.setWeight(QFont.Weight.Bold)
        self._title.setFont(title_font)
        texts.addWidget(self._title)
        self._description = styled_label("", "Muted", wrap=True)
        texts.addWidget(self._description)
        self._reasons = styled_label("", "", wrap=True)
        texts.addWidget(self._reasons)
        for label in (self._title, self._description, self._reasons):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        texts.addStretch(1)
        layout.addLayout(texts, 1)

    def set_state(self, state: DriverState | None, label: str, description: str,
                  reasons: list[str] | tuple[str, ...] = ()) -> None:
        color = STATE_COLORS[state]
        if state != self._state:
            self._state = state
            self._badge.setPixmap(pixmap(STATE_ICONS[state], color, 26))
            self._title.setStyleSheet(f"color: {color};")
            self._color_anim.stop()
            self._color_anim.setStartValue(QColor(self._color))
            self._color_anim.setEndValue(qcolor(color))
            self._color_anim.start()
            if state == DriverState.DROWSY:
                self._pulse.start()
            else:
                self._pulse.stop()
                self._glow = 0.0
        self._title.setText(label)
        self._description.setText(description)
        text = "\n".join(f"•  {r}" for r in reasons[:3])
        self._reasons.setVisible(bool(text))
        self._reasons.setText(text)

    def _on_color(self, value: QColor) -> None:
        self._color = QColor(value)
        self.update()

    def _on_pulse(self, value: float) -> None:
        self._glow = 1.0 - abs(1.0 - 2.0 * value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - API do Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        p.fillPath(path, qcolor(Palette.SURFACE))
        tint = QColor(self._color)
        tint.setAlphaF(0.09 + 0.12 * self._glow)
        p.fillPath(path, tint)
        border = QColor(self._color)
        border.setAlphaF(0.35 + 0.4 * self._glow)
        p.setPen(QPen(border, 1.2))
        p.drawPath(path)
        p.setClipPath(path)
        p.fillRect(QRectF(rect.left(), rect.top(), 5, rect.height()), self._color)
        # círculo de fundo do ícone
        badge = QRectF(self._badge.geometry())
        halo = QColor(self._color)
        halo.setAlphaF(0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(badge)


class AlertBanner(QFrame):
    """Faixa de alerta destacada (sonolência ou falha de monitoramento)."""

    mute_requested = Signal()

    DROWSY = "drowsy"
    MONITORING = "monitoring"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = self.DROWSY
        self._glow = 0.0
        self.setMaximumHeight(0)
        self.setVisible(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 16, 18, 16)
        layout.setSpacing(16)
        self._icon = QLabel()
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        self._title = QLabel()
        font = QFont(self.font())
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.Bold)
        self._title.setFont(font)
        self._title.setWordWrap(True)
        self._title.setStyleSheet("color: white;")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: rgba(255,255,255,0.9);")
        self._advice = QLabel()
        self._advice.setWordWrap(True)
        self._advice.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 12px;")
        texts.addWidget(self._title)
        texts.addWidget(self._detail)
        texts.addWidget(self._advice)
        layout.addLayout(texts, 1)
        self.mute_button = make_button("Silenciar", "volume-off", "BannerButton", "white")
        self.mute_button.clicked.connect(self.mute_requested)
        layout.addWidget(self.mute_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._height_anim = QPropertyAnimation(self, b"maximumHeight", self, duration=220)
        self._height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._height_anim.finished.connect(self._after_anim)
        self._pulse = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=800)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)
        self._showing = False

    @property
    def showing(self) -> bool:
        return self._showing

    def show_banner(self, kind: str, title: str, detail: str, advice: str = "",
                    mute_visible: bool = True) -> None:
        self._kind = kind
        icon_name = "alert-octagon" if kind == self.DROWSY else "face-off"
        if "CÂMERA" in title:
            icon_name = "camera-off"
        self._icon.setPixmap(pixmap(icon_name, "white", 34))
        self._title.setText(title)
        self._detail.setText(detail)
        self._advice.setText(advice)
        self._advice.setVisible(bool(advice))
        self.mute_button.setVisible(mute_visible)
        if kind == self.DROWSY:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._glow = 0.0
        if not self._showing:
            self._showing = True
            self.setVisible(True)
            self._animate_to(self._target_height())
        else:
            self.setMaximumHeight(self._target_height())
        self.update()

    def hide_banner(self) -> None:
        if not self._showing:
            return
        self._showing = False
        self._pulse.stop()
        self._animate_to(0)

    def _target_height(self) -> int:
        self.layout().activate()
        width = max(self.width(), 600)
        return max(84, self.layout().totalHeightForWidth(width))

    def _animate_to(self, height: int) -> None:
        self._height_anim.stop()
        self._height_anim.setStartValue(self.maximumHeight())
        self._height_anim.setEndValue(height)
        self._height_anim.start()

    def _after_anim(self) -> None:
        if not self._showing:
            self.setVisible(False)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._showing and self._height_anim.state() != QPropertyAnimation.State.Running:
            self.setMaximumHeight(self._target_height())

    def _on_pulse(self, value: float) -> None:
        self._glow = 1.0 - abs(1.0 - 2.0 * value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = Palette.DANGER if self._kind == self.DROWSY else "#7C5CDB"
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        start = qcolor(base)
        end = qcolor(base)
        start = start.darker(115 - int(15 * self._glow))
        end = end.darker(160)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, gradient)
        p.setPen(QPen(QColor(255, 255, 255, 40 + int(60 * self._glow)), 1))
        p.drawPath(path)


# --------------------------------------------------------------------------- #
# Vídeo
# --------------------------------------------------------------------------- #
class VideoView(QWidget):
    """Exibe os quadros da câmera com HUD, placeholders e borda de estado."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image: QImage | None = None
        self._state: DriverState | None = None
        self._placeholder = ("power", "Câmera desligada", "Clique em Iniciar monitoramento para começar.")
        self._live = False
        self._hud_left = ""
        self._hud_right = ""
        self._calibration: float | None = None
        self._glow = 0.0
        self._pulse = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=900)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear_frame(self) -> None:
        self._image = None
        self.update()

    def set_placeholder(self, icon_name: str, title: str, subtitle: str) -> None:
        self._placeholder = (icon_name, title, subtitle)
        self.update()

    def set_state(self, state: DriverState | None) -> None:
        if state == self._state:
            return
        self._state = state
        if state == DriverState.DROWSY:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._glow = 0.0
        self.update()

    def set_live(self, live: bool) -> None:
        self._live = live
        self.update()

    def set_hud(self, left: str, right: str) -> None:
        if (left, right) != (self._hud_left, self._hud_right):
            self._hud_left, self._hud_right = left, right
            self.update()

    def set_calibration(self, progress: float | None) -> None:
        if progress != self._calibration:
            self._calibration = progress
            self.update()

    def _on_pulse(self, value: float) -> None:
        self._glow = 1.0 - abs(1.0 - 2.0 * value)
        self.update()

    # -- desenho ----------------------------------------------------------- #
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        p.fillPath(path, QColor("#070B10"))

        p.save()
        p.setClipPath(path)
        if self._image is not None and not self._image.isNull():
            self._draw_image(p, rect)
        else:
            self._draw_placeholder(p, rect)
        self._draw_hud(p, rect)
        p.restore()

        color = qcolor(STATE_COLORS.get(self._state, Palette.IDLE))
        if self._state in (None, DriverState.NORMAL, DriverState.STARTING):
            color = qcolor(Palette.BORDER_STRONG)
            width = 1.2
        elif self._state == DriverState.DROWSY:
            color.setAlphaF(0.55 + 0.45 * self._glow)
            width = 3.0 + 2.0 * self._glow
        else:
            color.setAlphaF(0.8)
            width = 2.2
        p.setPen(QPen(color, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _draw_image(self, p: QPainter, rect: QRectF) -> None:
        img_size = QSize(self._image.width(), self._image.height())
        scaled = img_size.scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding) \
            if self._fill_mode(rect) else img_size.scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, scaled.width(), scaled.height())
        target.moveCenter(rect.center())
        p.drawImage(target, self._image)
        shade = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + 70))
        shade.setColorAt(0, QColor(0, 0, 0, 120))
        shade.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, shade)

    def _fill_mode(self, rect: QRectF) -> bool:
        """Preenche a área se a diferença de proporção for pequena (evita faixas pretas finas)."""
        img_ratio = self._image.width() / max(1, self._image.height())
        view_ratio = rect.width() / max(1.0, rect.height())
        return abs(img_ratio - view_ratio) / view_ratio < 0.12

    def _draw_placeholder(self, p: QPainter, rect: QRectF) -> None:
        icon_name, title, subtitle = self._placeholder
        center = rect.center()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(Palette.SURFACE_2))
        p.drawEllipse(center + QPointF(0, -38), 38, 38)
        pm = pixmap(icon_name, Palette.TEXT_MUTED, 34)
        p.drawPixmap(int(center.x() - 17), int(center.y() - 38 - 17), pm)
        font = QFont(self.font())
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(qcolor(Palette.TEXT))
        p.drawText(QRectF(rect.left() + 20, center.y() + 12, rect.width() - 40, 26),
                   Qt.AlignmentFlag.AlignCenter, title)
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(qcolor(Palette.TEXT_MUTED))
        p.drawText(QRectF(rect.left() + 40, center.y() + 40, rect.width() - 80, 44),
                   Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, subtitle)

    def _pill(self, p: QPainter, x: float, y: float, text: str, dot: QColor | None = None,
              align_right: bool = False) -> None:
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        fm = QFontMetrics(font)
        pad = 10
        dot_space = 14 if dot is not None else 0
        width = fm.horizontalAdvance(text) + 2 * pad + dot_space
        if align_right:
            x -= width
        box = QRectF(x, y, width, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(8, 12, 17, 190))
        p.drawRoundedRect(box, 12, 12)
        if dot is not None:
            p.setBrush(dot)
            p.drawEllipse(QPointF(x + pad + 3.5, y + 12), 3.5, 3.5)
        p.setPen(QColor(232, 238, 245))
        p.drawText(box.adjusted(pad + dot_space, 0, -pad, 0), Qt.AlignmentFlag.AlignVCenter, text)

    def _draw_hud(self, p: QPainter, rect: QRectF) -> None:
        if self._live and self._hud_left:
            self._pill(p, rect.left() + 14, rect.top() + 14, self._hud_left, qcolor(Palette.DANGER))
        if self._hud_right:
            self._pill(p, rect.right() - 14, rect.top() + 14, self._hud_right, align_right=True)
        if self._calibration is not None:
            text = "Calibrando referência  ·  olhe para a frente com os olhos abertos"
            font = QFont(self.font())
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            width = min(rect.width() - 40, QFontMetrics(font).horizontalAdvance(text) + 48)
            box = QRectF(0, 0, width, 50)
            box.moveCenter(QPointF(rect.center().x(), rect.bottom() - 42))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(8, 12, 17, 210))
            p.drawRoundedRect(box, 12, 12)
            p.setPen(QColor(232, 238, 245))
            p.drawText(box.adjusted(16, 7, -16, -22), Qt.AlignmentFlag.AlignCenter, text)
            track = QRectF(box.left() + 16, box.bottom() - 15, box.width() - 32, 5)
            p.setBrush(qcolor(Palette.SURFACE_3))
            p.drawRoundedRect(track, 2.5, 2.5)
            p.setBrush(qcolor(Palette.ACCENT))
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * self._calibration, 5), 2.5, 2.5)


# --------------------------------------------------------------------------- #
# Métricas
# --------------------------------------------------------------------------- #
class MetricTile(QFrame):
    """Bloco compacto: legenda, valor principal, detalhe e barra opcional."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setMinimumHeight(84)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(3)
        self._caption_row = QHBoxLayout()
        self._caption_row.setSpacing(6)
        self._caption_row.addWidget(caption(title))
        self._caption_row.addStretch(1)
        layout.addLayout(self._caption_row)
        self._row = QHBoxLayout()
        self._row.setSpacing(10)
        self.value = styled_label("—", "TileValue")
        self._row.addWidget(self.value)
        self._row.addStretch(1)
        layout.addLayout(self._row)
        self.sub = styled_label("", "TileSub", wrap=True)
        layout.addWidget(self.sub)
        # Só o subtítulo (texto longo) abre mão da largura mínima; o valor é curto
        # e precisa da largura preferida para não ser espremido pelo stretch da linha.
        self.sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        self._color: str | None = None
        self._bar_color: str | None = None

    def add_to_caption_row(self, widget: QWidget) -> None:
        self._caption_row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_value(self, value: str, sub: str | None = None, color: str | None = None) -> None:
        self.value.setText(value)
        if sub is not None:
            self.sub.setText(sub)
        if color != self._color:
            self._color = color
            self.value.setStyleSheet(f"color: {color};" if color else "")

    def set_bar(self, fraction: float | None, color: str = Palette.ACCENT) -> None:
        self._bar.setVisible(fraction is not None)
        if fraction is None:
            return
        self._bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        if color != self._bar_color:
            self._bar_color = color
            self._bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}")


class EyesIndicator(QWidget):
    """Dois olhos estilizados: abertos (verde), fechados (vermelho) ou sem dados."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._closed: bool | None = None
        self.setFixedSize(52, 16)

    def set_closed(self, closed: bool | None) -> None:
        if closed != self._closed:
            self._closed = closed
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for cx in (12.0, 40.0):
            self._eye(p, cx, 8.0)

    def _eye(self, p: QPainter, cx: float, cy: float) -> None:
        w, h = 10.5, 5.2
        if self._closed is None:
            pen = QPen(qcolor(Palette.TEXT_FAINT), 1.6, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._almond(cx, cy, w, h))
        elif self._closed:
            p.setPen(QPen(qcolor(Palette.DANGER), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            lid = QPainterPath(QPointF(cx - w, cy))
            lid.quadTo(QPointF(cx, cy + h), QPointF(cx + w, cy))
            p.drawPath(lid)
        else:
            color = qcolor(Palette.OK)
            p.setPen(QPen(color, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._almond(cx, cy, w, h))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(cx, cy), 2.8, 2.8)

    @staticmethod
    def _almond(cx: float, cy: float, w: float, h: float) -> QPainterPath:
        path = QPainterPath(QPointF(cx - w, cy))
        path.quadTo(QPointF(cx, cy - h * 1.6), QPointF(cx + w, cy))
        path.quadTo(QPointF(cx, cy + h * 1.6), QPointF(cx - w, cy))
        return path


class Sparkline(QWidget):
    """Série temporal do EAR com a linha do limiar de olho fechado."""

    def __init__(self, window_seconds: float = 20.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window = window_seconds
        self._points: deque[tuple[float, float | None]] = deque()
        self._threshold: float | None = None
        self.setMinimumHeight(104)

    def clear(self) -> None:
        self._points.clear()
        self._threshold = None
        self.update()

    def add(self, t: float, value: float | None, threshold: float) -> None:
        self._points.append((t, value))
        self._threshold = threshold
        while self._points and self._points[0][0] < t - self.window:
            self._points.popleft()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(2, 8, -2, -6)
        values = [v for _, v in self._points if v is not None]
        top = max(0.40, (max(values) * 1.15) if values else 0.0)

        def y_of(v: float) -> float:
            return area.bottom() - (v / top) * area.height()

        p.setPen(QPen(qcolor(Palette.BORDER), 1))
        for frac in (0.25, 0.5, 0.75):
            y = area.top() + area.height() * frac
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))

        if not self._points:
            p.setPen(qcolor(Palette.TEXT_FAINT))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Aguardando dados do monitoramento")
            return

        t_end = self._points[-1][0]
        t_start = t_end - self.window

        def x_of(t: float) -> float:
            return area.left() + (t - t_start) / self.window * area.width()

        segments: list[list[QPointF]] = [[]]
        for t, v in self._points:
            if v is None:
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(QPointF(x_of(t), y_of(min(v, top))))

        accent = qcolor(Palette.ACCENT)
        for seg in segments:
            if len(seg) < 2:
                continue
            fill = QPainterPath(QPointF(seg[0].x(), area.bottom()))
            for pt in seg:
                fill.lineTo(pt)
            fill.lineTo(QPointF(seg[-1].x(), area.bottom()))
            grad = QLinearGradient(QPointF(0, area.top()), QPointF(0, area.bottom()))
            grad.setColorAt(0.0, qcolor(Palette.ACCENT, 0.28))
            grad.setColorAt(1.0, qcolor(Palette.ACCENT, 0.0))
            p.fillPath(fill, grad)
            line = QPainterPath(seg[0])
            for pt in seg[1:]:
                line.lineTo(pt)
            p.setPen(QPen(accent, 2.0))
            p.drawPath(line)

        if self._threshold is not None:
            y = y_of(self._threshold)
            p.setPen(QPen(qcolor(Palette.WARN, 0.85), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(qcolor(Palette.WARN))
            p.drawText(QRectF(area.right() - 160, y + 3, 158, 14),
                       Qt.AlignmentFlag.AlignRight, "LIMIAR DE OLHO FECHADO")

        last_t, last_v = self._points[-1]
        if last_v is not None:
            closed = self._threshold is not None and last_v < self._threshold
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(Palette.DANGER if closed else Palette.ACCENT))
            p.drawEllipse(QPointF(x_of(last_t), y_of(min(last_v, top))), 4, 4)


# --------------------------------------------------------------------------- #
# Controles
# --------------------------------------------------------------------------- #
class ToggleSwitch(QAbstractButton):
    """Interruptor liga/desliga animado (substitui QCheckBox nas configurações)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self, duration=140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(44, 24)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        if self.isVisible():
            self._anim.start()
        else:
            self._set_offset(1.0 if checked else 0.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, 44, 24).adjusted(1, 1, -1, -1)
        rect.moveCenter(QRectF(self.rect()).center())
        off, on = qcolor(Palette.SURFACE_3), qcolor(Palette.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        if not self.isEnabled():
            track.setAlphaF(0.4)
        p.setPen(QPen(qcolor(Palette.BORDER_STRONG), 1) if self._offset < 0.5 else Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        d = rect.height() - 6
        x = rect.left() + 3 + self._offset * (rect.width() - d - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white") if self._offset > 0.5 else qcolor(Palette.TEXT_MUTED))
        p.drawEllipse(QRectF(x, rect.top() + 3, d, d))


class Pill(QFrame):
    """Indicador compacto com ponto/ícone colorido (cabeçalho)."""

    def __init__(self, icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = Palette.IDLE
        self._icon_name = icon_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 12, 4)
        layout.setSpacing(7)
        self._icon = QLabel()
        layout.addWidget(self._icon)
        self._text = QLabel()
        self._text.setStyleSheet("font-weight: 600; font-size: 12px;")
        layout.addWidget(self._text)
        self.setFixedHeight(30)

    def set(self, text: str, color: str, icon_name: str | None = None) -> None:
        self._color = color
        name = icon_name or self._icon_name
        if name:
            self._icon.setPixmap(pixmap(name, color, 15))
        self._text.setText(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qcolor(self._color, 0.35), 1))
        p.setBrush(qcolor(self._color, 0.10))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


class NavButton(QToolButton):
    def __init__(self, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Nav")
        self.setText(text)
        self.setIcon(icon(icon_name, Palette.TEXT_MUTED, 22, checked_color=Palette.ACCENT))
        self.setIconSize(QSize(22, 22))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(78, 66)


class Toast(QLabel):
    """Mensagem temporária sobreposta na parte inferior da janela."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {Palette.SURFACE_3}; color: {Palette.TEXT}; border: 1px solid "
            f"{Palette.BORDER_STRONG}; border-radius: 12px; padding: 11px 18px; font-weight: 600;"
        )
        self.setWordWrap(True)
        self.setMaximumWidth(560)
        self._effect = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self, duration=220)
        self._timer = QTimer(self, singleShot=True, timeout=self._fade_out)
        self.hide()

    def show_message(self, text: str, msec: int = 4200) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2 + 48, 88)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(msec)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        QTimer.singleShot(240, lambda: self.hide() if self._effect.opacity() < 0.05 else None)
