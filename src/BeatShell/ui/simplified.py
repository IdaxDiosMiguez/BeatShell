from BeatShell.ui.constants import *

class BeatShellSimpleUI():
    
    def __init__(self, centered: bool=False, boxed: bool=False):
        self.centered = centered
        self.boxed = boxed

    def draw(self, term_size: tuple[int, int], metadata):

        """
        Title
        Album
        Artist
        Date
        󰒮    󰓛    󰐊    󰒭
        """

        width, height = term_size

        controls = f" {PREVIOUS}    {STOP}    {PLAY}    {NEXT} "

        lines = (
            metadata["title"],
            metadata["album"],
            metadata["artist"],
            metadata["date"],
            "",
            "",
            "",
            controls
        )

        max_len = len(max(lines, key=len))


        buffer = "\x1b[2J"

        if self.centered:
            buffer += "\x1b[1;1H"
        else:
            buffer += "\x1b[1;1H"

        buffer += "╭" + "─" * (max_len + 2) + "╮"

        for idx, line in enumerate(lines):
            buffer += f"\x1b[{idx+2};1H│ {line:^{max_len}} │"
        
        buffer += f"\x1b[{idx+3};1H╰" + "─" * (max_len + 2) + "╯"

        """
          Palante
          Showtime
        Cookin Soul
         2024-09-26
        """
        """
        if self.centered:
            start_y = (height - len(lines)) // 2
            
            for i, text in enumerate(lines):
                content_len = len(text)
                start_x = (width - max_len) // 2

                pos = f"\x1b[{start_y + i};{start_x}H"
                buffer += pos + text
        
        else: 
            ...
        """


        return buffer