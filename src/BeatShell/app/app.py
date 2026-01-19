from .core import Core

from BeatShell.metadata import MP3Parser
from BeatShell.events import UnixHandler

class App(Core):

    def __init__(self, file_path, ui):
        super().__init__(
            file_path=file_path,
            terminal=UnixHandler(),
            metadata_parser=MP3Parser(),
            ui=ui
        )


