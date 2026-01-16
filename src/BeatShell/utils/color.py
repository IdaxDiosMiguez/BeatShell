from __future__ import annotations
from typing import Union, Sequence

Numeric = Union[int, float]


__all__ = (
    "Color"
)


class Color(tuple):
    
    """A class to represent a color.

    Colors are stored as three values representing the degree of red, green, and blue in a color, and a
    fourth "alpha" value which defines where the color lies on a gradient of opaque to transparent.

    Example:
        ```python
        >>> from textual.color import Color
        >>> color = Color(255, 0, 0)
        >>> color
        Color(255, 0, 0, 1.0)
        >>> color = Color("#FF000000")
        >>> color
        Color(255, 0, 0, 0.0)
        ```
    """

    def __new__(cls, *value: Union[str, Sequence[Numeric], Numeric, tuple]):
  
        if len(value) == 1 and isinstance(value[0], str):
            _hex_value = value[0]
            _rgba: tuple[int, int, int, float] = cls._from_hex(_hex_value)

        elif isinstance(value, tuple):
            _rgba_value = value
            _rgba: tuple[int, int, int, float] = cls._from_rgba(_rgba_value)

        else:
            raise TypeError("Color expects RGBA or a HEX string")


        return super().__new__(cls, _rgba)

    @staticmethod
    def _from_rgba(value: tuple) -> tuple[int, int, int, float]:
        if 3 <= len(value) <= 4 and all(isinstance(v, int) for v in value[:3]):
                r, g, b = value[:3]
                a = value[3] if len(value) == 4 else 1.0

        else:
            raise ValueError("Color expects RGBA integers")

        for v in (r, g, b):
            if not isinstance(v, int) or not 0 <= v <= 255:
                raise ValueError("RGB values must be ints in 0–255")

        if not isinstance(a, (int, float)) or not 0 <= a <= 1:
            raise ValueError("Alpha must be between 0 and 1")

        return (r, g, b, float(a))

    @staticmethod
    def _from_hex(value: str)  -> tuple[int, int, int, float]:
        if not value.startswith("#"):
            raise ValueError("Hex colors must start with '#'")

        if len(value) not in (7, 9):
            raise ValueError("Hex color must be #RRGGBB or #RRGGBBAA")

        value = value.lstrip('#')

        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
        a = int(value[6:8], 16) / 255 if len(value) == 8 else 1.0

        return (r, g, b, a)


if __name__ == "__main__":
    r, g, b, a = Color("#b00001")
    text = "Hello, world!"


    buffer = f"\x1b[48;2;{r};{g};{b}m"
    buffer += text


    print(buffer)