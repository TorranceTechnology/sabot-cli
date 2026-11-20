"""Names shared by every module of `sources`: the literals, the refusal and the plain results."""

from dataclasses import dataclass
from typing import Literal

type SourceFormat = Literal["pdf", "html", "text"]
type SegmentMode = Literal["numbered", "headings", "sentences", "boundaries"]
type Numbering = Literal["dotted", "dotted-letter"]

SOURCE_KEY_PATTERN = r"[a-z0-9][a-z0-9.-]*"


class SourceError(Exception):
    """A source document or its segmentation input cannot be used; `code` names the refusal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractedText:
    """The text of a source and the extractor that produced it, as recorded on the source note."""

    text: str
    extractor: str
