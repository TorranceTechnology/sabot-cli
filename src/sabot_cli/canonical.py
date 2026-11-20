"""The JSON form every document the tools write has, and the types that describe it.

This module sits below `rules` and `sources` so that they can write canonical JSON without
importing the command line; `output` re-exports these names for the commands.
"""

import json
from collections.abc import Mapping, Sequence

FORMAT_VERSION = 1

type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None


def canonical_json(document: Mapping[str, JsonValue]) -> str:
    """Serialise a document to its canonical text, with a trailing newline."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
