"""Cola de reproducción con aleatorio y repetición."""

from __future__ import annotations

import random

from .models import Track


class PlayQueue:
    def __init__(self) -> None:
        self.tracks: list[Track] = []
        self.index = -1
        self.repeat = "off"  # off | all | one
        self._shuffle = False
        self._original: list[Track] | None = None
        self.version = 0  # cambia en cada modificación (para refrescar la vista)

    # ----------------------------------------------------------------- consultas
    @property
    def current(self) -> Track | None:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    def peek_next(self) -> Track | None:
        i = self._next_index(auto=True)
        return self.tracks[i] if i is not None else None

    def __len__(self) -> int:
        return len(self.tracks)

    def _touch(self) -> None:
        self.version += 1

    # ---------------------------------------------------------------- navegación
    def _next_index(self, auto: bool) -> int | None:
        if not self.tracks:
            return None
        if auto and self.repeat == "one" and self.current:
            return self.index
        nxt = self.index + 1
        if nxt < len(self.tracks):
            return nxt
        if self.repeat in ("all", "one"):
            return 0
        return None

    def advance(self, auto: bool = False) -> Track | None:
        i = self._next_index(auto)
        if i is None:
            return None
        self.index = i
        self._touch()
        return self.current

    def back(self) -> Track | None:
        if not self.tracks:
            return None
        if self.index > 0:
            self.index -= 1
        elif self.repeat == "all":
            self.index = len(self.tracks) - 1
        self._touch()
        return self.current

    def jump(self, index: int) -> Track | None:
        if 0 <= index < len(self.tracks):
            self.index = index
            self._touch()
        return self.current

    # --------------------------------------------------------------- modificar
    def replace(self, tracks: list[Track], start: int = 0) -> Track | None:
        self.tracks = [t.copy() for t in tracks]
        self._original = None
        self.index = start if self.tracks else -1
        if self._shuffle and self.tracks:
            first = self.tracks.pop(self.index)
            self._original = [first] + self.tracks[:]
            random.shuffle(self.tracks)
            self.tracks.insert(0, first)
            self.index = 0
        self._touch()
        return self.current

    def add(self, tracks: list[Track]) -> None:
        new = [t.copy() for t in tracks]
        if self._shuffle and self._original is not None:
            self._original.extend(new)
        self.tracks.extend(new)
        self._touch()

    def insert_next(self, tracks: list[Track]) -> int:
        """Inserta después de la actual. Devuelve el índice de la primera insertada."""
        new = [t.copy() for t in tracks]
        pos = self.index + 1 if self.index >= 0 else len(self.tracks)
        self.tracks[pos:pos] = new
        if self._original is not None:
            self._original.extend(new)
        self._touch()
        return pos

    def remove(self, index: int) -> None:
        if not (0 <= index < len(self.tracks)):
            return
        t = self.tracks.pop(index)
        if self._original is not None:
            self._original = [x for x in self._original if x.uid != t.uid]
        if index < self.index:
            self.index -= 1
        elif index == self.index:
            self.index = index - 1  # la siguiente en sonar será la que ocupó su lugar
        self._touch()

    def move(self, index: int, delta: int) -> int:
        if not (0 <= index < len(self.tracks)):
            return index
        j = max(0, min(len(self.tracks) - 1, index + delta))
        if j == index:
            return j
        cur = self.current
        self.tracks.insert(j, self.tracks.pop(index))
        if cur is not None:
            self.index = next(i for i, t in enumerate(self.tracks) if t.uid == cur.uid)
        self._touch()
        return j

    def clear(self, keep_current: bool = True) -> None:
        cur = self.current if keep_current else None
        self.tracks = [cur] if cur else []
        self.index = 0 if cur else -1
        self._original = None
        self._touch()

    # ---------------------------------------------------------------- aleatorio
    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @shuffle.setter
    def shuffle(self, on: bool) -> None:
        if on == self._shuffle:
            return
        self._shuffle = on
        cur = self.current
        if on:
            self._original = self.tracks[:]
            upcoming = self.tracks[self.index + 1:]
            random.shuffle(upcoming)
            self.tracks = self.tracks[: self.index + 1] + upcoming
        else:
            if self._original is not None:
                alive = {t.uid for t in self.tracks}
                self.tracks = [t for t in self._original if t.uid in alive]
            self._original = None
            if cur is not None:
                self.index = next((i for i, t in enumerate(self.tracks) if t.uid == cur.uid), 0)
        self._touch()

    def cycle_repeat(self) -> str:
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self._touch()
        return self.repeat
