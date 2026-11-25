"""Colores y hoja de estilo. Un único acento (el rosa de BeatShell) sobre neutros."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass(frozen=True)
class Theme:
    dark: bool
    bg: str
    side: str
    hover: str
    selected: str
    border: str
    text: str
    muted: str
    faint: str
    accent: str
    accent2: str
    accent_text: str
    error: str


DARK = Theme(
    dark=True, bg="#0e0e11", side="#0a0a0c", hover="#18181d", selected="#212128",
    border="#1c1c22", text="#f2f2f5", muted="#8d8d99", faint="#55555f",
    accent="#ff4fd8", accent2="#5ee7ff", accent_text="#16060f", error="#ff5c7a",
)
LIGHT = Theme(
    dark=False, bg="#ffffff", side="#f6f6f8", hover="#f0f0f3", selected="#e9e9ef",
    border="#e7e7ec", text="#141418", muted="#6c6c78", faint="#a6a6b0",
    accent="#d81fb6", accent2="#0aa2c8", accent_text="#ffffff", error="#d93355",
)


def current(pref: str = "auto") -> Theme:
    if pref == "light":
        return LIGHT
    if pref == "dark":
        return DARK
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return LIGHT if scheme == Qt.ColorScheme.Light else DARK
    except Exception:  # noqa: BLE001 - Qt antiguo
        return DARK


def palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.bg, QPalette.ColorRole.Base: t.bg,
        QPalette.ColorRole.AlternateBase: t.hover, QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.WindowText: t.text, QPalette.ColorRole.Button: t.hover,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.Highlight: t.selected,
        QPalette.ColorRole.HighlightedText: t.text, QPalette.ColorRole.ToolTipBase: t.selected,
        QPalette.ColorRole.ToolTipText: t.text, QPalette.ColorRole.PlaceholderText: t.faint,
        QPalette.ColorRole.Link: t.accent,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    return p


def qss(t: Theme) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t.text}; font-size: 13px; }}
QMainWindow, #content {{ background: {t.bg}; }}
QToolTip {{ background: {t.selected}; color: {t.text}; border: none; padding: 5px 8px;
            border-radius: 6px; }}

/* ---------- barra lateral ---------- */
#sidebar {{ background: {t.side}; border-right: 1px solid {t.border}; }}
#brand {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 4px; }}
#brandDot {{ color: {t.accent}; font-size: 15px; font-weight: 800; }}
QPushButton#nav {{ text-align: left; padding: 8px 10px; border: none; border-radius: 8px;
                   color: {t.muted}; font-weight: 600; background: transparent; }}
QPushButton#nav:hover {{ color: {t.text}; background: {t.hover}; }}
QPushButton#nav:checked {{ color: {t.text}; background: {t.selected}; }}
#sectionTitle {{ color: {t.faint}; font-size: 11px; font-weight: 700; letter-spacing: 1px;
                 padding: 4px 4px 2px 4px; }}
QListWidget#collections {{ background: transparent; border: none; }}
QListWidget#collections::item {{ padding: 6px 8px; border-radius: 7px; color: {t.muted}; }}
QListWidget#collections::item:hover {{ background: {t.hover}; color: {t.text}; }}
QListWidget#collections::item:selected {{ background: {t.selected}; color: {t.text}; }}

/* ---------- cabecera y buscador ---------- */
QLineEdit#search {{ background: {t.hover}; border: 1px solid transparent; border-radius: 10px;
                    padding: 9px 12px 9px 4px; font-size: 14px; selection-background-color: {t.accent};
                    selection-color: {t.accent_text}; }}
QLineEdit#search:focus {{ border: 1px solid {t.faint}; background: {t.bg}; }}
QLineEdit#filter {{ background: transparent; border: none; border-bottom: 1px solid {t.border};
                    padding: 6px 2px; }}
QLineEdit#filter:focus {{ border-bottom: 1px solid {t.accent}; }}
#pageTitle {{ font-size: 26px; font-weight: 800; }}
#pageSub {{ color: {t.muted}; }}
#empty {{ color: {t.faint}; font-size: 14px; }}

QPushButton {{ border: none; border-radius: 9px; padding: 7px 16px; font-weight: 600;
               background: {t.hover}; color: {t.text}; }}
QPushButton:hover {{ background: {t.selected}; }}
QPushButton:disabled {{ color: {t.faint}; }}
QPushButton#primary {{ background: {t.accent}; color: {t.accent_text}; }}
QPushButton#primary:hover {{ background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.text}; }}

/* ---------- listas ---------- */
QListView {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.selected}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

/* ---------- reproductor ---------- */
#player {{ background: {t.side}; border-top: 1px solid {t.border}; }}
#nowTitle {{ font-size: 14px; font-weight: 700; }}
#player QToolButton {{ border-radius: 16px; padding: 6px; }}
#nowArtist {{ color: {t.muted}; }}
#time {{ color: {t.faint}; font-size: 11px; }}
QToolButton {{ border: none; background: transparent; border-radius: 8px; padding: 4px; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- menús y diálogos ---------- */
QMenu {{ background: {t.selected}; border: 1px solid {t.border}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.hover if not t.dark else '#2c2c35'}; }}
QMenu::item:disabled {{ color: {t.faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
QMenu::indicator {{ width: 0; }}
QInputDialog, QMessageBox {{ background: {t.bg}; }}
QDialog QLineEdit {{ background: {t.hover}; border: 1px solid {t.border}; border-radius: 8px; padding: 7px; }}

#toast {{ background: {t.text}; color: {t.bg}; border-radius: 10px; padding: 10px 16px;
          font-weight: 600; }}
#toast[error="true"] {{ background: {t.error}; color: #ffffff; }}
"""
