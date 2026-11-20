"""Read-only questions about a loaded vault: filters, full-text ranking and edge traversal.

This package imports `vault` only. It knows no rules vocabulary: the caller supplies the edge
names it may follow as an `EdgeSpec`. Nothing here writes, reads a clock or uses the network.

Full text indexes two fields of each note: its `title` property and its body. In the body a
wikilink contributes its alias (or its target when it has none) and text inside code fences is
indexed as written. Other front-matter properties are not indexed.
"""

from sabot_cli.search._model import (
    Direction,
    EdgeSpec,
    Hit,
    Hop,
    InvalidFilterError,
    Reached,
    UnknownNoteError,
)
from sabot_cli.search._predicates import query
from sabot_cli.search._ranking import full_text
from sabot_cli.search._traversal import traverse

__all__ = [
    "Direction",
    "EdgeSpec",
    "Hit",
    "Hop",
    "InvalidFilterError",
    "Reached",
    "UnknownNoteError",
    "full_text",
    "query",
    "traverse",
]
