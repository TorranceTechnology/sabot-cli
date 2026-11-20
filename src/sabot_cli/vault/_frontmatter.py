"""Split a note into front matter and body, and parse the front matter.

`split` is the one place that decides where the front matter ends. The reader parses what it
finds; a command that edits a note line by line splits with it too, so the two cannot disagree
about which lines are front matter.
"""

from collections.abc import Mapping

import yaml

from sabot_cli.vault import _yaml

_DELIMITER = "---"


class FrontMatterError(Exception):
    """The note has no usable front matter; the message says why."""


def split(text: str) -> tuple[list[str], int]:
    """The lines of a note and the index of the delimiter that closes its front matter.

    Lines are split on `\\n` only, so a `\\r` that ends a line is left in place for the caller to
    keep. Raises `FrontMatterError` when the first line is not a delimiter or none closes it.
    """
    lines = text.split("\n")
    if lines[0].rstrip() != _DELIMITER:
        raise FrontMatterError("the file does not start with a front matter delimiter")
    closing = next(
        (index for index in range(1, len(lines)) if lines[index].rstrip() == _DELIMITER), None
    )
    if closing is None:
        raise FrontMatterError("the front matter has no closing delimiter")
    return lines, closing


def parse(text: str) -> tuple[Mapping[str, object], str]:
    """Return the front matter mapping and the body that follows its closing delimiter."""
    lines, closing = split(text.replace("\r\n", "\n"))
    try:
        loaded = _yaml.load("\n".join(lines[1:closing]))
    except yaml.MarkedYAMLError as error:
        # +2: one for the opening delimiter, one because marks count from zero.
        where = f" at line {error.problem_mark.line + 2}" if error.problem_mark else ""
        raise FrontMatterError(
            f"the front matter is not valid YAML{where}: {error.problem or 'syntax error'}"
        ) from error
    except yaml.YAMLError as error:
        raise FrontMatterError("the front matter is not valid YAML") from error
    if not isinstance(loaded, dict):
        raise FrontMatterError("the front matter is not a mapping of keys to values")
    properties: dict[str, object] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            raise FrontMatterError(f"the front matter key {key!r} is not text")
        properties[key] = value
    return properties, "\n".join(lines[closing + 1 :])
