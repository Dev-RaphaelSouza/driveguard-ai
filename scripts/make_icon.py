"""Gera assets/icons/driveguard.ico a partir de assets/icons/logo.svg.

O .ico é usado pelo PyInstaller como ícone do executável. Uso:
    python scripts/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from config.settings import ICONS_DIR  # noqa: E402


def main() -> int:
    app = QGuiApplication([])  # noqa: F841 - necessário para renderizar
    svg = (ICONS_DIR / "logo.svg").read_bytes()
    renderer = QSvgRenderer(QByteArray(svg))
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, 256, 256))
    painter.end()
    target = ICONS_DIR / "driveguard.ico"
    if not image.save(str(target), "ICO"):
        print("Falha ao gravar o .ico (plugin de imagem ICO indisponível?)", file=sys.stderr)
        return 1
    print(f"Ícone gerado: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
