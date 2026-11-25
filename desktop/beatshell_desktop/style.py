"""Colores y hoja de estilo. Cada tema: neutros + un acento (y un segundo para degradados)."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass
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


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return "#{:02x}{:02x}{:02x}".format(*(round(x + (y - x) * t) for x, y in (
        (ca.red(), cb.red()), (ca.green(), cb.green()), (ca.blue(), cb.blue()))))


def _retro(bg: str, side: str, hover: str, selected: str, text: str, accent: str, accent2: str,
           error: str) -> Theme:
    """Los temas de la versión de terminal, con los neutros derivados de sus colores."""
    return Theme(dark=True, bg=bg, side=side, hover=hover, selected=selected,
                 border=_mix(bg, text, 0.07), text=text, muted=_mix(text, bg, 0.42),
                 faint=_mix(text, bg, 0.64), accent=accent, accent2=accent2,
                 accent_text=_mix(bg, "#000000", 0.3), error=error)


# Los mismos temas que BeatShell en la terminal (más claro/oscuro neutros).
THEMES: dict[str, tuple[str, Theme | None]] = {
    "auto": ("Automático", None),
    "dark": ("Oscuro", DARK),
    "light": ("Claro", LIGHT),
    "beatshell": ("BeatShell", _retro("#0f0b1e", "#0b0817", "#171233", "#241b48",
                                      "#ece6ff", "#ff4fd8", "#5ee7ff", "#ff5c7a")),
    "gameboy": ("Game Boy", _retro("#0f1f0f", "#0b170b", "#163016", "#244a24",
                                   "#c4dc8c", "#9bbc0f", "#e0f8d0", "#f87858")),
    "sunset": ("Sunset", _retro("#1a0f12", "#140b0e", "#26151b", "#3a1e24",
                                "#fff1e0", "#ff8a3d", "#ff4f6d", "#ff5c7a")),
    "ice": ("Ice", _retro("#0a1020", "#070c18", "#111a30", "#1a2644",
                          "#e6f1ff", "#5ab8ff", "#a78bfa", "#ff7a90")),
}


def system_is_light() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
    except Exception:  # noqa: BLE001 - Qt antiguo
        return False


def current(pref: str = "auto") -> Theme:
    """Una copia del tema (la app la modifica en el sitio al cambiar de tema en vivo)."""
    base = THEMES.get(pref, THEMES["auto"])[1]
    if base is None:
        base = LIGHT if system_is_light() else DARK
    return replace(base)


def assign(target: Theme, source: Theme) -> None:
    """Copia los colores de `source` en `target`: todo lo que usa `target` cambia a la vez."""
    for f in fields(Theme):
        setattr(target, f.name, getattr(source, f.name))


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


def _arrow(color: str) -> str:
    """Flecha de los desplegables como imagen (QSS no sabe dibujar triángulos)."""
    from BeatShell.config import CACHE_DIR

    from . import icons

    base = CACHE_DIR / f"arrow-{color.lstrip('#')}"
    try:
        if not Path(f"{base}.png").exists():
            for suffix, size in (("", 12), ("@2x", 24)):
                px = icons.pixmap("chevron", color, size)
                px.setDevicePixelRatio(1.0)
                px.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation).save(f"{base}{suffix}.png")
        return Path(f"{base}.png").as_posix()
    except Exception:  # noqa: BLE001
        return ""


def qss(t: Theme) -> str:
    arrow = _arrow(t.muted)
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
#heroTitle {{ font-size: 44px; font-weight: 800; }}
#heroKind {{ font-size: 12px; font-weight: 600; color: {t.text}; }}
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

/* ---------- ajustes ---------- */
QScrollArea#settings, #settingsBody {{ background: {t.bg}; border: none; }}
#setSection {{ font-size: 16px; font-weight: 800; padding-top: 18px; }}
#setCard {{ background: {t.side}; border: 1px solid {t.border}; border-radius: 12px; }}
#setLabel {{ font-weight: 600; }}
#setHint {{ color: {t.muted}; font-size: 12px; }}
#setRowSep {{ background: {t.border}; max-height: 1px; min-height: 1px; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit#setField {{
    background: {t.hover}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px 10px;
    min-height: 18px; selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit#setField:hover {{ border-color: {t.faint}; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit#setField:focus {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {t.selected}; border: 1px solid {t.border}; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: {t.hover if not t.dark else _mix(t.selected, t.text, 0.08)}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none; }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 34px; height: 20px; border-radius: 10px; background: {t.selected};
    border: 1px solid {t.border}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}

#toast {{ background: {t.text}; color: {t.bg}; border-radius: 10px; padding: 10px 16px;
          font-weight: 600; }}
#toast[error="true"] {{ background: {t.error}; color: #ffffff; }}
"""
