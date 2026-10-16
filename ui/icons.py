"""Carregamento e tingimento dos ícones SVG de ``assets/icons``.

Os SVGs usam ``currentColor``; aqui a cor é substituída antes da renderização,
o que permite um único conjunto de arquivos para todos os estados da interface.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from config.settings import ICONS_DIR
from ui.styles import Palette

RENDER_SCALE = 2.0  # renderiza em 2× para nitidez em telas de alta densidade


@lru_cache(maxsize=None)
def _svg_source(name: str) -> str:
    path = ICONS_DIR / f"{name}.svg"
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = Palette.TEXT, size: int = 20) -> QPixmap:
    """Renderiza o ícone ``name`` na cor ``color`` com ``size`` px lógicos."""
    svg = _svg_source(name).replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    side = int(size * RENDER_SCALE)
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, side, side))
    painter.end()
    pix = QPixmap.fromImage(image)
    pix.setDevicePixelRatio(RENDER_SCALE)
    return pix


def icon(name: str, color: str = Palette.TEXT, size: int = 20,
         checked_color: str | None = None) -> QIcon:
    """QIcon com variantes normal, desabilitada e (opcional) marcada."""
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(pixmap(name, Palette.TEXT_FAINT, size), QIcon.Mode.Disabled, QIcon.State.Off)
    if checked_color:
        result.addPixmap(pixmap(name, checked_color, size), QIcon.Mode.Normal, QIcon.State.On)
    return result


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(pixmap("logo", Palette.TEXT, size))
    return result
