"""Wikilink parsing: the one place that decides what a link value looks like."""

import re

# `[[target]]`, optionally `[[target#heading]]` or `[[target|alias]]`; only the target is kept.
_WIKILINK = re.compile(r"^\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]$")
_ANYWHERE = re.compile(r"\[\[[^\[\]]*\]\]")


def parse_wikilink(raw: str) -> str | None:
    """Return the target id of a wikilink, or None when `raw` is not exactly one wikilink."""
    match = _WIKILINK.match(raw.strip())
    if match is None:
        return None
    target = match.group(1).strip()
    return target or None


def find_wikilinks(text: str) -> tuple[str, ...]:
    """Return every wikilink written in a piece of text, as written, in order."""
    return tuple(_ANYWHERE.findall(text))
