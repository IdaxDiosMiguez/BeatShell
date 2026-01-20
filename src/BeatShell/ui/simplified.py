from BeatShell.ui.constants import *

class BeatShellSimpleUI():
    
    def __init__(self, centered: bool=False, boxed: bool=False):
        self.centered = centered
        self.boxed = boxed

    def draw(self, term_size: tuple[int, int], metadata):
        width, height = term_size

        x_start = 1
        y_start = 1

        lines = (
            metadata["title"],
            metadata["album"],
            metadata["artist"],
            metadata["date"],
            "",
            "",
            "",
            "",
            f"{PREVIOUS}    {STOP}    {PLAY}    {NEXT} "
        )

        max_len = len(max(lines, key=len))

        buffer = "\x1b[2J"


        if self.centered:
            x_start = (width - max_len + (-1 if self.boxed else 4)) // 2
            y_start = (height - len(lines)) // 2


        if self.boxed:
            buffer += f"\x1b[{y_start};{x_start}H╭" + "─" * (max_len + 2) + "╮"
            lines = tuple([f"│ {line:^{max_len}} │" for line in lines])

        for count, line in enumerate(lines, 1):
            buffer += f"\x1b[{y_start+count};{x_start}H{line:^{max_len}}"

        if self.boxed:
            buffer += f"\x1b[{y_start+count+1};{x_start}H╰" + "─" * (max_len + 2) + "╯"

        return buffer