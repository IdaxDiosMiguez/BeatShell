import os
import signal

from subprocess import (
    Popen,
    DEVNULL,
    TimeoutExpired
)

from BeatShell.metadata import MetadataParser
from BeatShell.events import TerminalHandler
from BeatShell.ui import BeatShellSimpleUI


__all__ = (
    "Core",
)


class Core:

    def __init__(
        self,
        file_path: str,
        terminal: TerminalHandler,
        metadata_parser: MetadataParser,
        ui: BeatShellSimpleUI | None
        ):

        self.file_path = file_path

        self.terminal = terminal
        self.metadata = metadata_parser.parse(self.file_path)
        self.ui = ui

        self.playing: bool = False
        self.process: Popen
        
        self.DEFAULT_KEY_BINDS = {
            "q": self._stop
        }

    def _load(self):
        # self.metadata.get()
        ...

    def _play(self):
        # Linux systems defaults ffplay
        self.process = Popen([
                'ffplay', 
                '-nodisp', 
                '-autoexit', 
                '-loglevel', 'quiet',  # Keep the TUI clean
                '-i', self.file_path
            ],
            stdout=DEVNULL,
            stderr=DEVNULL,
            start_new_session=True,
            env={**os.environ, "SDL_AUDIODRIVER": "pipewire"} # Force SDL to use PW
        )

        self.playing = True

    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)

            try: 
                self.process.wait(timeout=1)
            
            except TimeoutExpired:
                self.process.terminate()
                self.process.wait()

        self.playing = False

    def _input_handler(self, key_input):
        self.DEFAULT_KEY_BINDS[key_input]()

    def _draw_ui(self):
        if self.terminal.needs_redraw and self.ui:
            self.terminal.write(self.ui.draw())
            self.terminal.needs_redraw = False

    def _main_loop(self):

        self.terminal.enter_raw_mode()
        self._play()

        while self.playing:
            self._draw_ui()

            key_input = self.terminal.key_pressed()

            if key_input in self.DEFAULT_KEY_BINDS:
                self._input_handler(key_input)

        self.terminal.exit_raw_mode()

    def run(self):
        self.terminal.enter_raw_mode()
        self._main_loop()
        self.terminal.exit_raw_mode()
        