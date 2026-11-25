"""Ventanas modales: ayuda, elegir playlist, pedir texto y confirmar."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .. import art
from ..eq import BAND_LABELS, PRESET_HINT, PRESETS, gains
from .widgets import Logo, palette

NEW_PLAYLIST = "__new__"
NEW_FOLDER = "__newfolder__"
ROOT = "__root__"
SAVE_ALBUM = "__album__"
SAVE_ARTIST = "__artist__"


class TextPrompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, placeholder: str = "", value: str = "", hint: str = "") -> None:
        super().__init__()
        self._title, self._placeholder, self._value, self._hint = title, placeholder, value, hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="prompt-input")
            if self._hint:
                yield Label(self._hint, classes="dialog-hint")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancelar", id="cancel")
                yield Button("Aceptar", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one(Input).value.strip() or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "No"), Binding("y,s", "yes", "Sí")]

    def __init__(self, question: str, detail: str = "", yes: str = "Sí") -> None:
        super().__init__()
        self._q, self._detail, self._yes = question, detail, yes

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._q, classes="dialog-title")
            if self._detail:
                yield Label(self._detail, classes="dialog-hint")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancelar", id="no")
                yield Button(self._yes, id="yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class SaveMenu(ModalScreen[str | None]):
    """Guardar en la biblioteca: en una playlist, o el álbum / artista de la canción.

    Devuelve el id de una playlist, NEW_PLAYLIST, SAVE_ALBUM o SAVE_ARTIST.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, store, what: str, album: str = "", artist: str = "") -> None:
        super().__init__()
        self._store, self._what, self._album, self._artist = store, what, album, artist

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Guardar {self._what} en tu biblioteca", classes="dialog-title")
            options: list = []
            if self._album:
                options.append(Option(Text(f"◉ Guardar el álbum «{self._album}»"), id=SAVE_ALBUM))
            if self._artist:
                options.append(Option(Text(f"★ Seguir al artista «{self._artist}»"), id=SAVE_ARTIST))
            if options:
                options.append(None)  # separador
            options.append(Option(Text("＋ Nueva playlist…", style="bold"), id=NEW_PLAYLIST))
            for p in self._store.playlists:
                where = self._store.path_of(p.parent)
                label = Text(f"☰ {p.name}")
                label.append(f"  {len(p.tracks)}" + (f" · {where}" if where else ""), style="dim")
                options.append(Option(label, id=p.id))
            yield OptionList(*options, id="picker")
            yield Label("Enter para elegir · Esc para cancelar", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class FolderPicker(ModalScreen[str | None]):
    """Elegir carpeta de destino. Devuelve un id de carpeta, ROOT o NEW_FOLDER."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, store, what: str, exclude: set[str] | None = None) -> None:
        super().__init__()
        self._store, self._what, self._exclude = store, what, exclude or set()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Mover {self._what} a…", classes="dialog-title")
            options = [Option(Text("⌂ Biblioteca (sin carpeta)"), id=ROOT),
                       Option(Text("＋ Nueva carpeta…", style="bold"), id=NEW_FOLDER)]
            for f in self._store.folders:
                if f.id not in self._exclude:
                    options.append(Option(Text(f"◫ {self._store.path_of(f.id)}"), id=f.id))
            yield OptionList(*options, id="picker")
            yield Label("Enter para elegir · Esc para cancelar", classes="dialog-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EqGraph(Static):
    """Curva del ecualizador dibujada con bloques."""

    DEFAULT_CSS = "EqGraph { height: 9; width: auto; }"

    def __init__(self, preset: str) -> None:
        super().__init__()
        self.preset = preset

    def show(self, preset: str) -> None:
        self.preset = preset
        self.refresh()

    def render(self) -> Text:
        p = palette(self.app)
        g = gains(self.preset)
        rows = [6, 4, 2, 0, -2, -4, -6]
        out = Text(no_wrap=True)
        for r in rows:
            out.append(f"{r:+d}".rjust(3) + " ", style=art.hexc(p["dim"]))
            for i, gain in enumerate(g):
                t = i / max(1, len(g) - 1)
                col = art.hexc(art.gradient([p["secondary"], p["primary"], p["accent"]], t))
                # cada fila son 2 dB; medio bloque para los valores impares
                if r > 0 and gain >= r:
                    out.append("███ ", style=col)
                elif r > 0 and gain >= r - 1:
                    out.append("▄▄▄ ", style=col)
                elif r < 0 and gain <= r:
                    out.append("███ ", style=col)
                elif r < 0 and gain <= r + 1:
                    out.append("▀▀▀ ", style=col)
                elif r == 0:
                    out.append("━━━ ", style=col if gain == 0 else art.hexc(p["dim"]))
                else:
                    out.append("    ")
            out.append("\n")
        out.append("    ")
        for lab in BAND_LABELS:
            out.append(lab.center(3) + " ", style=art.hexc(p["dim"]))
        return out


class EqualizerScreen(ModalScreen[str | None]):
    """Elegir preset. Se escucha al momento al moverse por la lista."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, current: str, preview) -> None:  # noqa: ANN001
        super().__init__()
        self._current = current
        self._preview = preview
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog eq"):
            yield Label("≋ Ecualizador", classes="dialog-title")
            with Horizontal(classes="eq-body"):
                opts = [Option(name, id=name) for name in PRESETS]
                yield OptionList(*opts, id="eq-presets")
                with Vertical(classes="eq-side"):
                    yield EqGraph(self._current)
                    yield Label("", id="eq-hint", classes="dialog-hint")
            yield Label("↑↓ se escucha al momento · Enter aplicar · Esc deshacer",
                        classes="dialog-hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.focus()
        names = list(PRESETS)
        ol.highlighted = names.index(self._current) if self._current in names else 0

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        name = event.option.id
        self.query_one(EqGraph).show(name)
        self.query_one("#eq-hint", Label).update(PRESET_HINT.get(name, ""))
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(0.35, lambda: self._preview(name))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


HELP_SECTIONS = [
    ("Reproducción", [
        ("espacio", "Reproducir / pausa"),
        ("n · b", "Siguiente · anterior"),
        ("← →", "Retroceder / avanzar 5 s  (Mayús: 30 s)"),
        ("+ −", "Subir / bajar volumen"),
        ("m", "Silenciar"),
        ("s", "Aleatorio"),
        ("r", "Repetir: no → todo → una"),
        ("E", "Ecualizador (presets)"),
        ("j", "Modo DJ: fundidos, volumen nivelado y radio infinita"),
    ]),
    ("Buscar y añadir", [
        ("/", "Ir a la barra de búsqueda"),
        ("texto", "Buscar en YouTube"),
        ("+texto", "Añadir directamente el primer resultado a la cola"),
        ("enlace", "YouTube (vídeo o playlist) o Spotify (canción, álbum, playlist)"),
        ("ruta", "Carpeta (biblioteca), archivo de audio o playlist .m3u8 (importar)"),
        ("Esc", "Volver a la lista"),
    ]),
    ("En una lista", [
        ("Enter", "Reproducir desde aquí"),
        ("a · A", "Añadir a la cola · añadir todo"),
        ("i", "Reproducir a continuación"),
        ("g", "Guardar: en una playlist, el álbum o el artista"),
        ("G", "Guardar toda la lista (playlist o álbum)"),
        ("d · D", "Descargar MP3 · descargar todo"),
        ("x", "Quitar de la cola/playlist"),
        ("Mayús ↑↓", "Mover en la cola/playlist"),
    ]),
    ("Biblioteca", [
        ("Enter", "En el árbol: reproducir la playlist, álbum, artista o carpeta entera"),
        ("N", "Nueva playlist"),
        ("C", "Nueva carpeta"),
        ("M", "Mover a una carpeta"),
        ("e", "Renombrar"),
        ("x", "Quitar de la biblioteca (en el árbol)"),
        ("Ctrl+E", "Exportar playlist a .m3u8"),
        ("Ctrl+O", "Importar playlist .m3u/.m3u8"),
        ("f · R", "Añadir carpeta de música · re-escanear"),
    ]),
    ("General", [
        ("1-4", "Cola · Buscar · Biblioteca · Descargas"),
        ("Ctrl+B", "Mostrar / ocultar el menú lateral"),
        ("t", "Cambiar tema de color"),
        ("Ctrl+P", "Paleta de comandos"),
        ("?", "Esta ayuda"),
        ("q", "Salir (guarda la cola)"),
    ]),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "close", "Cerrar")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog help"):
            yield Logo()
            with VerticalScroll():
                for title, rows in HELP_SECTIONS:
                    t = Table.grid(padding=(0, 2))
                    t.add_column(style="bold", min_width=12, justify="right")
                    t.add_column()
                    for k, v in rows:
                        t.add_row(k, v)
                    yield Label(title, classes="help-title")
                    yield Static(t)
            yield Label("Esc para cerrar", classes="dialog-hint")

    def action_close(self) -> None:
        self.dismiss(None)
