import argparse

from BeatShell.app import App
from BeatShell.ui import BeatShellSimpleUI

DEFAULT_MEDIA_SONG = "/home/idaxdm/Music/Cookin Soul/Cookin Soul - SHOWTIME/03 - Palante.mp3"


def main():

    arg_parser = argparse.ArgumentParser(
        prog="BeatShell",
        usage="beatshell [options]",
        description="Beatshell is a simple, open source lightweight music visualizer for your Terminal"
    )

    arg_parser.add_argument("-F", "--file", type=str)
    arg_parser.add_argument("-S", "--simple", action="store_true")
    arg_parser.add_argument("-C", "--centered", action="store_true")
    arg_parser.add_argument("-B", "--boxed", action="store_true")

    args = arg_parser.parse_args()
    
    ui = BeatShellSimpleUI(
        args.centered,
        args.boxed
        ) if args.simple else None

    BS = App(DEFAULT_MEDIA_SONG, ui)
    BS.run()


if __name__ == "__name__":
    main()