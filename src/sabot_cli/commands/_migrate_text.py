"""The textual edit `migrate-edition` makes to a note's front matter.

The edit is by line so that every line of a note it does not mean to change stays
byte-identical, and it touches the front matter only.
"""

import re
from collections.abc import Mapping, Sequence

from sabot_cli.sources import SourceError, StaleReason
from sabot_cli.vault import FrontMatterError, parse_front_matter, split_front_matter

_SOURCE_KEY = "source"
_STALE = "stale"
_STALE_REASON = "stale-reason"


def rewrite_note_text(
    text: str,
    repoints: Mapping[str, str],
    stale_reason: StaleReason | None,
) -> str:
    """The note with `source` entries re-pointed and, when asked, marked stale.

    Only front matter lines change: an entry's reference text is replaced in place, and the
    `stale` and `stale-reason` lines are replaced or added. The result is parsed again, and
    `SourceError` `migration-rewrite-invalid` is raised if anything other than those three
    properties differs, or the body moved, so a note is never written half-edited.
    """
    try:
        lines, closing = split_front_matter(text)
    except FrontMatterError as error:
        # The vault reader parsed this front matter already, so this is a note that changed
        # since; it is refused like any other note that cannot be rewritten safely.
        raise SourceError(
            "migration-rewrite-invalid", "a note has no front matter to edit"
        ) from error
    carriage = "\r" if lines[0].endswith("\r") else ""
    if repoints:
        _rewrite_citations(lines, closing, repoints)
    if stale_reason is not None:
        closing = _set_stale(lines, closing, stale_reason, carriage)
    rewritten = "\n".join(lines)
    _check_rewrite(text, rewritten, repoints, stale_reason)
    return rewritten


def _key_line(lines: Sequence[str], closing: int, key: str) -> int | None:
    for index in range(1, closing):
        if lines[index].startswith(f"{key}:"):
            return index
    return None


def _rewrite_citations(lines: list[str], closing: int, repoints: Mapping[str, str]) -> None:
    start = _key_line(lines, closing, _SOURCE_KEY)
    if start is None:
        return
    end = start + 1
    while end < closing and (not lines[end] or lines[end][0] in " \t-"):
        end += 1
    # A reference is delimited by anything that cannot be part of one, so `dw1#4.1` never
    # matches inside `dw1#4.10` or `dw1#4.1/s2`.
    pattern = re.compile(
        "(?<![A-Za-z0-9.#/*-])("
        + "|".join(re.escape(raw) for raw in sorted(repoints, key=len, reverse=True))
        + ")(?![A-Za-z0-9.#/*-])"
    )
    for index in range(start, end):
        content, comment = _split_comment(lines[index])
        lines[index] = pattern.sub(lambda match: repoints[match.group(1)], content) + comment


def _split_comment(line: str) -> tuple[str, str]:
    """A front matter line as (content, comment); a `#` starts a comment only outside quotes
    and after whitespace, so the `#` of a reference is never one."""
    quote = ""
    for position, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == "#" and (position == 0 or line[position - 1] in " \t"):
            return line[:position], line[position:]
    return line, ""


def _set_stale(lines: list[str], closing: int, reason: str, carriage: str) -> int:
    """Set `stale: true` and `stale-reason`; return the closing delimiter's new index."""
    stale_at = _key_line(lines, closing, _STALE)
    reason_at = _key_line(lines, closing, _STALE_REASON)
    if stale_at is not None:
        lines[stale_at] = f"{_STALE}: true{carriage}"
    if reason_at is not None:
        lines[reason_at] = f"{_STALE_REASON}: {reason}{carriage}"
    if stale_at is None and reason_at is None:
        lines[closing:closing] = [
            f"{_STALE}: true{carriage}",
            f"{_STALE_REASON}: {reason}{carriage}",
        ]
        return closing + 2
    if stale_at is None:
        lines.insert(closing, f"{_STALE}: true{carriage}")
        return closing + 1
    if reason_at is None:
        lines.insert(stale_at + 1, f"{_STALE_REASON}: {reason}{carriage}")
        return closing + 1
    return closing


def _parsed(text: str) -> tuple[Mapping[str, object], str]:
    try:
        return parse_front_matter(text)
    except FrontMatterError as error:
        raise SourceError(
            "migration-rewrite-invalid", f"the rewritten front matter cannot be read: {error}"
        ) from error


def _check_rewrite(
    before: str, after: str, repoints: Mapping[str, str], stale_reason: StaleReason | None
) -> None:
    old_properties, old_body = _parsed(before)
    new_properties, new_body = _parsed(after)
    expected = dict(old_properties)
    cited = expected.get(_SOURCE_KEY)
    if repoints and isinstance(cited, list):
        expected[_SOURCE_KEY] = [repoints.get(c, c) if isinstance(c, str) else c for c in cited]
    if stale_reason is not None:
        expected[_STALE] = True
        expected[_STALE_REASON] = stale_reason
    if new_properties != expected or new_body != old_body:
        raise SourceError(
            "migration-rewrite-invalid",
            "the citations could not be rewritten line by line; edit the note by hand",
        )
