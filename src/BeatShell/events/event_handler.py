import os
import sys
import signal
import struct

if (sys.platform == "linux") or (sys.platform == "darwin"):
    import fcntl
    import select
    import termios


__all__ = (
    "TerminalHandler",
    "UnixHandler"
)


class TerminalHandler():

    DEFAULT_TERMINAL_WIDTH = 0
    DEFAULT_TERMINAL_HEIGHT = 0
    
    def __init__(self, log: bool=False):
        self.fd = sys.stdout.fileno()

        self.old_settings = None
        self.needs_redraw = True
        self.buffer = []

        self._width = 0
        self._height = 0
        
        self.log = log

        signal.signal(signal.SIGWINCH, self._handle_term_resize)
        self._update_term_size()

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def _handle_term_resize(self, signum, frame):
        try:
            self.needs_redraw = True
            self._update_term_size()

        finally:
            if self.log:
                print(self.size)

    def _update_term_size(self):
        raise NotImplementedError("Children classes must implement the '_update_term_size' method.")

    def key_pressed(self) -> str:
        raise NotImplementedError("Children classes must implement the 'key_pressed' method.")

    def enter_raw_mode(self):
        raise NotImplementedError("Children classes must implement the 'enter_raw_mode' method.")

    def exit_raw_mode(self):
        raise NotImplementedError("Children classes must implement the 'exit_raw_mode' method.")

    def write(self, data: str):
        os.write(self.fd, data.encode("utf-8"))

class UnixHandler(TerminalHandler):

    def _update_term_size(self):
        try:
            raw_data = fcntl.ioctl(1, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0))
            rows, cols, *_ = struct.unpack('HHHH', raw_data)

            self._width = cols
            self._height = rows

        except Exception:
            self._width = int(
                os.environ.get(
                    'COLUMNS', self.DEFAULT_TERMINAL_WIDTH
                )
            )

            self._height = int(
                os.environ.get(
                    'LINES', self.DEFAULT_TERMINAL_HEIGHT
                )
            )

    def key_pressed(self) -> str:
        dr, dw, de = select.select([self.fd], [], [], 0)
        if dr:
            # Data is ready, read up to 5 bytes (covers most escape sequences)
            return os.read(self.fd, 5).decode('utf-8', errors='ignore')
        return ""

    def enter_raw_mode(self):
        self.old_settings = termios.tcgetattr(self.fd)
        new = termios.tcgetattr(self.fd)
        
        new[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, new)

        self.write("\x1b[?25l")
        self.write("\x1b[?1049h")

    def exit_raw_mode(self):
        if self.old_settings:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)

        self.write("\x1b[?1049l")
        self.write("\x1b[?25h")



"""

        else:
            # Send 'SIGCONT' to resume
            self.process.send_signal(18) # 18 is SIGCONT
            self.start_time = time.time()
            self.playing = True

    def get_current_time(self):
        if not self.playing:
            return self.elapsed_offset
        return self.elapsed_offset + (time.time() - self.start_time)
"""