from __future__ import annotations

from typing import Protocol, Dict

from pathlib import Path
from mutagen.easyid3 import EasyID3


__all__ = (
    "MetadataParser",
    "MP3Parser"
)


DEFAULT_METADATA = {
    "title": "Unknown Title",
    "artist": "Unknown Artist",
    "album": "Unknown Album",
    "date": "Unknown Year"
}


class MetadataParser():
    def __init__(self, template: Dict[str, str]=DEFAULT_METADATA):
        self.template = template

    def parse(self, audio_path: Path | str) -> Dict[str, str]:
        raise NotImplementedError("Children classes must implement the parse method.")


class MP3Parser(MetadataParser):

    def parse(self, audio_path: Path | str) -> Dict[str, str]:  #type: ignore
        if isinstance(audio_path, str):
            audio_path = Path(audio_path)

        try:
            audio_file = EasyID3(audio_path)
            return {
                tag: audio_file.get(tag, [default])[0]          #type: ignore
                for tag, default in self.template.items()
            }

        except Exception:
            return self.template