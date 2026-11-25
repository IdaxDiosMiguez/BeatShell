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

from .widgets import Logo

NEW_PLAYLIST = "__new__"


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


class PlaylistPicker(ModalScreen[str | None]):
    """Devuelve el id de una playlist o NEW_PLAYLIST."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, playlists, what: str) -> None:
        super().__init__()
        self._playlists = playlists
        self._what = what

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Guardar {self._what} en…", classes="dialog-title")
            options = [Option(Text("＋ Nueva playlist…", style="bold"), id=NEW_PLAYLIST)]
            for p in self._playlists:
                options.append(Option(f"☰ {p.name}  ({len(p.tracks)})", id=p.id))
            yield OptionList(*options, id="picker")
            yield Label("Enter para elegir · Esc para cancelar", classes="dialog-hint")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.focus()
        if len(self._playlists):
            ol.highlighted = 1

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
    ]),
    ("Buscar y añadir", [
        ("/", "Ir a la barra de búsqueda"),
        ("texto", "Buscar en YouTube"),
        ("+texto", "Añadir directamente el primer resultado a la cola"),
        ("enlace", "YouTube (vídeo o playlist) o Spotify (canción, álbum, playlist)"),
        ("ruta", "Archivo para reproducir o carpeta para añadir a la biblioteca"),
        ("Esc", "Volver a la lista"),
    ]),
    ("En una lista", [
        ("Enter", "Reproducir desde aquí"),
        ("a · A", "Añadir a la cola · añadir todo"),
        ("i", "Reproducir a continuación"),
        ("g · G", "Guardar en playlist · guardar todo"),
        ("d · D", "Descargar MP3 · descargar todo"),
        ("x", "Quitar de la cola/playlist"),
        ("Mayús ↑↓", "Mover en la cola/playlist"),
    ]),
    ("Biblioteca y playlists", [
        ("f", "Añadir carpeta a la biblioteca"),
        ("R", "Volver a escanear la biblioteca"),
        ("N", "Nueva playlist"),
        ("e", "Renombrar biblioteca/playlist"),
        ("x", "Quitar biblioteca/playlist seleccionada"),
    ]),
    ("General", [
        ("1-5", "Cola · Buscar · Biblioteca · Playlists · Descargas"),
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
