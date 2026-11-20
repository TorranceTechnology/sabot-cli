"""The one way the tools read a text file: UTF-8, with a leading byte order mark dropped.

Notes, the ruleset, span files and extracted source text are all read here, so a file that
one command reads is read the same way by every other, and a rewrite can put back the mark it
found.
"""

from pathlib import Path
from typing import Literal

BOM = b"\xef\xbb\xbf"


class TextFileError(Exception):
    """A text file cannot be turned into text; `reason` says whether it was read or decoded."""

    def __init__(self, reason: Literal["unreadable", "undecodable"], name: str, detail: str):
        super().__init__(f"{name} {detail}")
        self.reason = reason
        self.name = name
        self.detail = detail


def decode_text(raw: bytes, name: str) -> str:
    """The text of `raw`, with a leading byte order mark dropped; `name` is for the message."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise TextFileError("undecodable", name, "is not valid UTF-8") from error


def read_text(path: Path) -> str:
    """The text of a file. Raises `TextFileError` when it cannot be read or is not UTF-8."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise TextFileError("unreadable", path.name, f"cannot be read: {error.strerror}") from error
    return decode_text(raw, path.name)


def encode_text(text: str, like: bytes) -> bytes:
    """`text` as UTF-8, starting with a byte order mark when `like` (the bytes it replaces) does."""
    return (BOM if like.startswith(BOM) else b"") + text.encode("utf-8")
