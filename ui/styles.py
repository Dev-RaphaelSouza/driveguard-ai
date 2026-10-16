"""Identidade visual: paleta, tipografia e folha de estilo (QSS).

Direção: painel automotivo noturno. Fundo grafite-azulado para reduzir o
ofuscamento, um acento ciano-esverdeado para a marca e cores semânticas fortes
reservadas aos estados — verde (normal), âmbar (atenção), vermelho (crítico) e
violeta para falhas de *monitoramento*, que não devem ser confundidas com
falhas do *motorista*.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFontDatabase

from app.drowsiness_analyzer import DriverState
from config.settings import ICONS_DIR


class Palette:
    BG = "#0A0F15"
    BG_ELEVATED = "#0D141C"
    SURFACE = "#111923"
    SURFACE_2 = "#16202B"
    SURFACE_3 = "#1D2937"
    BORDER = "#1E2A38"
    BORDER_STRONG = "#2A3A4D"
    TEXT = "#E8EEF5"
    TEXT_MUTED = "#8E9DB2"
    TEXT_FAINT = "#5D6E84"
    ACCENT = "#2DD4BF"
    ACCENT_HOVER = "#5EEAD4"
    ON_ACCENT = "#03201C"
    OK = "#22C55E"
    WARN = "#F5A524"
    DANGER = "#EF4444"
    SYSTEM = "#A78BFA"
    INFO = "#38BDF8"
    IDLE = "#64748B"


STATE_COLORS: dict[DriverState | None, str] = {
    None: Palette.IDLE,
    DriverState.STARTING: Palette.INFO,
    DriverState.NORMAL: Palette.OK,
    DriverState.ATTENTION: Palette.WARN,
    DriverState.DROWSY: Palette.DANGER,
    DriverState.NO_FACE: Palette.SYSTEM,
    DriverState.CAMERA_UNAVAILABLE: Palette.SYSTEM,
}

STATE_ICONS: dict[DriverState | None, str] = {
    None: "power",
    DriverState.STARTING: "loader",
    DriverState.NORMAL: "shield-check",
    DriverState.ATTENTION: "alert-triangle",
    DriverState.DROWSY: "alert-octagon",
    DriverState.NO_FACE: "face-off",
    DriverState.CAMERA_UNAVAILABLE: "camera-off",
}

IDLE_LABEL = "DESLIGADO"
IDLE_DESCRIPTION = "Monitoramento desligado. Clique em Iniciar para começar."


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(alpha)
    return c


def pick_fonts() -> tuple[str, str]:
    """Escolhe (texto, títulos) entre as fontes instaladas, com fallback seguro."""
    families = set(QFontDatabase.families())
    text = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Roboto") if f in families), "")
    display = next((f for f in ("Segoe UI Variable Display", "Segoe UI Semibold", text) if f in families), text)
    return text or "Sans Serif", display or text or "Sans Serif"


def build_stylesheet(text_font: str, display_font: str) -> str:
    p = Palette
    chevron_down = (ICONS_DIR / "chevron-down.svg").as_posix()
    chevron_up = (ICONS_DIR / "chevron-up.svg").as_posix()
    return f"""
* {{
    font-family: "{text_font}";
    font-size: 13px;
    color: {p.TEXT};
    outline: none;
}}
QMainWindow, QWidget#Root, QWidget#Page {{ background: {p.BG}; }}
QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QToolTip {{
    background: {p.SURFACE_3}; color: {p.TEXT}; border: 1px solid {p.BORDER_STRONG};
    padding: 6px 8px; border-radius: 6px;
}}

/* ---------- Estrutura ---------- */
QFrame#Sidebar {{ background: {p.BG_ELEVATED}; border-right: 1px solid {p.BORDER}; }}
QFrame#Header {{ background: {p.BG}; border-bottom: 1px solid {p.BORDER}; }}
QFrame#Footer {{ background: {p.BG_ELEVATED}; border-top: 1px solid {p.BORDER}; }}
QFrame#Card {{ background: {p.SURFACE}; border: 1px solid {p.BORDER}; border-radius: 14px; }}
QFrame#Tile {{ background: {p.SURFACE_2}; border: 1px solid {p.BORDER}; border-radius: 11px; }}
QFrame#Notice {{
    background: {rgba(p.WARN, 0.08)}; border: 1px solid {rgba(p.WARN, 0.35)}; border-radius: 12px;
}}
QFrame#Divider {{ background: {p.BORDER}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------- Tipografia ---------- */
QLabel#Brand {{ font-family: "{display_font}"; font-size: 11px; font-weight: 700; color: {p.TEXT_MUTED}; }}
QLabel#H1 {{ font-family: "{display_font}"; font-size: 21px; font-weight: 600; }}
QLabel#H2 {{ font-family: "{display_font}"; font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {p.TEXT_MUTED}; }}
QLabel#Faint {{ color: {p.TEXT_FAINT}; font-size: 12px; }}
QLabel#Caption {{ color: {p.TEXT_FAINT}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px; }}
QLabel#TileValue {{ font-family: "{display_font}"; font-size: 20px; font-weight: 600; }}
QLabel#TileSub {{ color: {p.TEXT_MUTED}; font-size: 11px; }}
QLabel#StatValue {{ font-family: "{display_font}"; font-size: 24px; font-weight: 600; }}
QLabel#Help {{ color: {p.TEXT_FAINT}; font-size: 12px; }}

/* ---------- Botões ---------- */
QPushButton {{
    background: {p.SURFACE_2}; border: 1px solid {p.BORDER_STRONG}; border-radius: 10px;
    padding: 9px 16px; font-weight: 600;
}}
QPushButton:hover {{ background: {p.SURFACE_3}; border-color: #3A4E66; }}
QPushButton:pressed {{ background: {p.BORDER}; }}
QPushButton:disabled {{ color: {p.TEXT_FAINT}; background: {p.SURFACE}; border-color: {p.BORDER}; }}
QPushButton:checked {{ background: {rgba(p.WARN, 0.14)}; border-color: {rgba(p.WARN, 0.5)}; color: {p.WARN}; }}
QPushButton#Primary {{
    background: {p.ACCENT}; color: {p.ON_ACCENT}; border: 1px solid {p.ACCENT};
    padding: 11px 22px; font-size: 14px;
}}
QPushButton#Primary:hover {{ background: {p.ACCENT_HOVER}; border-color: {p.ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: {p.SURFACE_2}; color: {p.TEXT_FAINT}; border-color: {p.BORDER}; }}
QPushButton#Primary[running="true"] {{
    background: {rgba(p.DANGER, 0.14)}; color: #FCA5A5; border: 1px solid {rgba(p.DANGER, 0.55)};
}}
QPushButton#Primary[running="true"]:hover {{ background: {rgba(p.DANGER, 0.22)}; }}
QPushButton#Danger {{
    background: {rgba(p.DANGER, 0.10)}; color: #FCA5A5; border: 1px solid {rgba(p.DANGER, 0.45)};
}}
QPushButton#Danger:hover {{ background: {rgba(p.DANGER, 0.20)}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {p.TEXT_MUTED}; }}
QPushButton#Ghost:hover {{ background: {p.SURFACE_2}; color: {p.TEXT}; }}
QPushButton#BannerButton {{
    background: rgba(255, 255, 255, 0.10); border: 1px solid rgba(255, 255, 255, 0.28);
    color: white; padding: 9px 18px;
}}
QPushButton#BannerButton:hover {{ background: rgba(255, 255, 255, 0.18); }}

QToolButton#Nav {{
    background: transparent; border: none; border-radius: 12px; color: {p.TEXT_MUTED};
    padding: 10px 2px 8px 2px; font-size: 11px; font-weight: 600;
}}
QToolButton#Nav:hover {{ background: {p.SURFACE_2}; color: {p.TEXT}; }}
QToolButton#Nav:checked {{ background: {rgba(p.ACCENT, 0.12)}; color: {p.ACCENT}; }}

/* ---------- Entradas ---------- */
QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QLineEdit {{
    background: {p.SURFACE_2}; border: 1px solid {p.BORDER_STRONG}; border-radius: 9px;
    padding: 6px 10px; min-height: 22px;
    selection-background-color: {p.ACCENT}; selection-color: {p.ON_ACCENT};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover {{ border-color: #3A4E66; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QLineEdit:focus {{
    border-color: {p.ACCENT};
}}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled {{
    color: {p.TEXT_FAINT}; background: {p.SURFACE};
}}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{chevron_down}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {p.SURFACE_2}; border: 1px solid {p.BORDER_STRONG}; border-radius: 8px;
    selection-background-color: {p.SURFACE_3}; selection-color: {p.TEXT}; padding: 4px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 20px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 11px; height: 11px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{chevron_down}"); width: 11px; height: 11px; }}

QSlider::groove:horizontal {{ height: 6px; background: {p.SURFACE_3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {p.ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {p.TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 9px;
    border: 2px solid {p.ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: white; }}

QProgressBar {{ background: {p.SURFACE_3}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {p.ACCENT}; border-radius: 3px; }}

/* ---------- Rolagem ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {p.BORDER_STRONG}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #3A4E66; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QScrollBar:horizontal {{ height: 0; }}

/* ---------- Tabela ---------- */
QTableView {{
    background: {p.SURFACE}; border: none; gridline-color: transparent;
    selection-background-color: {p.SURFACE_3}; selection-color: {p.TEXT};
}}
QTableView::item {{ border-bottom: 1px solid {p.BORDER}; padding: 0 12px; }}
QHeaderView {{ background: {p.SURFACE}; }}
QHeaderView::section {{
    background: {p.SURFACE}; color: {p.TEXT_FAINT}; border: none; border-bottom: 1px solid {p.BORDER};
    padding: 10px 12px; font-size: 11px; font-weight: 700;
}}
QTableCornerButton::section {{ background: {p.SURFACE}; border: none; }}

/* ---------- Diálogos ---------- */
QMessageBox, QDialog {{ background: {p.SURFACE}; }}
QMessageBox QLabel {{ color: {p.TEXT}; }}
QMessageBox QPushButton {{ min-width: 90px; }}
QCalendarWidget QWidget {{ alternate-background-color: {p.SURFACE_2}; }}
QCalendarWidget QAbstractItemView {{
    background: {p.SURFACE_2}; selection-background-color: {p.ACCENT}; selection-color: {p.ON_ACCENT};
}}
QCalendarWidget QToolButton {{ background: transparent; border: none; padding: 4px 8px; font-weight: 600; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {p.SURFACE_3}; }}
"""
