FONT = "Nerd Code Font"

match FONT:

    case "Nerd Code Font":
        PLAY = "󰐊"
        PAUSE = "󰏤"
        STOP = "󰓛"
        NEXT = "󰒭"
        PREVIOUS = "󰒮"

    case "Nerd Code Font 2":
        PLAY = ""
        PAUSE = ""
        STOP = ""
        NEXT = ""
        PREVIOUS = ""

    case _:
        PLAY = "▶"
        PAUSE = "⏸"
        STOP = "⏹"
        NEXT = "▹"
        PREVIOUS = "◃"