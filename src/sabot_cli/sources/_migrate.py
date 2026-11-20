"""Decide what a new edition does to a note's citations, and edit a note's front matter to match.

The decision is pure: it reads the classification of the old edition's spans and the two span
files. The edit is textual so that every line of a note it does not mean to change stays
byte-identical, and it touches the front matter only.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import yaml

from sabot_cli.sources._diff import EditionDiff
from sabot_cli.sources._records import SpanIndex, SpanRecord
from sabot_cli.sources._spanref import SpanRef, parse_span_ref
from sabot_cli.sources._types import SourceError
from sabot_cli.vault import load_yaml

type Outcome = Literal["clean", "editorial", "substantive", "removed"]
type StaleReason = Literal["editorial", "substantive", "removed"]

# Higher is more severe; a note takes the most severe outcome of the spans it cites.
SEVERITY: Mapping[str, int] = {"clean": 0, "editorial": 1, "substantive": 2, "removed": 3}

_DELIMITER = "---"
_SOURCE_KEY = "source"
_STALE = "stale"
_STALE_REASON = "stale-reason"


@dataclass(frozen=True)
class _Judgement:
    outcome: Outcome
    new_ref: str
    affected: bool


@dataclass(frozen=True)
class NoteMigration:
    """What migrating one note does.

    `repoints` maps each old reference text to its new text and is empty when the note keeps
    its citations (a substantive or removed span is cited). `reason` is the stale reason the
    outcome calls for, or None when the note stays unmarked. `affected` is the re-review test of
    `diff-editions`: a cited span is substantive, removed or moved.
    """

    note_id: str
    outcome: Outcome
    repoints: Mapping[str, str]
    affected: bool
    unresolved: tuple[str, ...]

    @property
    def reason(self) -> StaleReason | None:
        """The `stale-reason` for the outcome; None for a clean note."""
        return None if self.outcome == "clean" else self.outcome


def plan_note_migration(
    note_id: str,
    citations: Sequence[str],
    diff: EditionDiff,
    old: SpanIndex,
    new: SpanIndex,
) -> NoteMigration | None:
    """The migration of a note's citations to the old edition, or None when it cites none.

    A citation that is not a span reference, or names a span the old span file lacks, is left
    alone and listed in `unresolved`; lint reports it on its own. When any resolved citation
    is substantive or removed the note keeps every citation on the old edition, so a reviewer
    finds the note's evidence on one edition, and the stale reason is the most severe outcome.
    """
    judged: dict[str, _Judgement] = {}
    unresolved: list[str] = []
    for raw in citations:
        try:
            ref = parse_span_ref(raw)
        except ValueError:
            continue
        if ref.source != diff.old_key:
            continue
        judgement = _judge(ref, diff, old, new)
        if judgement is None:
            unresolved.append(raw)
        else:
            judged[raw] = judgement
    if not judged and not unresolved:
        return None
    outcome: Outcome = max(
        (j.outcome for j in judged.values()), key=SEVERITY.__getitem__, default="clean"
    )
    repoints = {raw: j.new_ref for raw, j in judged.items()} if SEVERITY[outcome] <= 1 else {}
    return NoteMigration(
        note_id,
        outcome,
        repoints,
        any(j.affected for j in judged.values()),
        tuple(sorted(unresolved)),
    )


def _judge(ref: SpanRef, diff: EditionDiff, old: SpanIndex, new: SpanIndex) -> _Judgement | None:
    root = old.get(ref.span)
    if root is None or root.kind != "span":
        return None
    if ref.sentence is not None and f"{ref.span}/s{ref.sentence}" not in old:
        return None
    if ref.subtree:
        return _judge_subtree(root, diff, old, new)
    fate = diff.fate_of(root.locator)
    if fate is None:
        return None
    kind, target = fate
    if kind in ("substantive", "removed"):
        return _Judgement(kind, "", True)
    locator = root.locator if target is None else target
    suffix = "" if ref.sentence is None else f"/s{ref.sentence}"
    if suffix and not _same_sentence(
        old, f"{ref.span}{suffix}", new, f"{diff.new_key}#{locator}{suffix}"
    ):
        # An editorial edit to the span can move a sentence boundary, so the number alone does
        # not say that the note still cites the same sentence.
        return _Judgement("substantive", "", kind == "moved")
    return _Judgement(
        "editorial" if kind == "editorial" else "clean",
        f"{diff.new_key}#{locator}{suffix}",
        kind == "moved",
    )


def _same_sentence(old: SpanIndex, old_span: str, new: SpanIndex, new_span: str) -> bool:
    """Whether two sentence records exist and carry the same editorial hash."""
    before, after = old.get(old_span), new.get(new_span)
    return (
        before is not None
        and after is not None
        and before.editorial_sha256 == after.editorial_sha256
    )


def _judge_subtree(
    root: SpanRecord, diff: EditionDiff, old: SpanIndex, new: SpanIndex
) -> _Judgement | None:
    """A `/*` citation covers a span and everything beneath it; it moves with the root only
    when every member is still beneath the new root, otherwise that member counts as removed."""
    root_fate = diff.fate_of(root.locator)
    if root_fate is None:
        return None
    kind, target = root_fate
    if kind in ("substantive", "removed"):
        return _Judgement(kind, "", True)
    new_root = root.locator if target is None else target
    members = [root, *old.subtree_of(root.span)]
    outcome: Outcome = "clean"
    affected = kind == "moved"
    for member in members:
        fate = diff.fate_of(member.locator)
        if fate is None:
            continue
        member_kind, member_target = fate
        if member_kind in ("substantive", "removed"):
            outcome = _worse(outcome, member_kind)
            affected = True
            continue
        landed = member.locator if member_target is None else member_target
        if member_kind == "moved":
            affected = True
        if not _is_under(new, diff.new_key, landed, new_root):
            outcome = _worse(outcome, "removed")
        elif member_kind == "editorial":
            outcome = _worse(outcome, "editorial")
    return _Judgement(outcome, f"{diff.new_key}#{new_root}/*", affected)


def _worse(current: Outcome, candidate: Outcome) -> Outcome:
    return candidate if SEVERITY[candidate] > SEVERITY[current] else current


def _is_under(index: SpanIndex, key: str, locator: str, ancestor: str) -> bool:
    """Whether `locator` is `ancestor` or has it among its parents in the span file."""
    seen: set[str] = set()
    current: str | None = locator
    while current is not None and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        record = index.get(f"{key}#{current}")
        current = None if record is None else record.parent
    return False


def stale_reason_to_write(
    properties: Mapping[str, object], reason: StaleReason | None
) -> StaleReason | None:
    """The reason to set on a note, or None when nothing needs to change.

    A reason never softens: a note already stale for a more severe reason keeps it.
    """
    if reason is None:
        return None
    existing = properties.get(_STALE_REASON)
    if (
        properties.get(_STALE) is True
        and isinstance(existing, str)
        and SEVERITY.get(existing, -1) >= SEVERITY[reason]
    ):
        return None
    return reason


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
    lines = text.split("\n")
    if lines[0].lstrip("﻿").rstrip() != _DELIMITER:
        raise SourceError("note-front-matter-invalid", "a note has no front matter to edit")
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip() == _DELIMITER), None)
    if closing is None:
        raise SourceError("note-front-matter-invalid", "a note has no front matter to edit")
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


def _front_matter(text: str) -> tuple[dict[str, object], list[str]]:
    lines = text.replace("\r\n", "\n").split("\n")
    closing = next(i for i in range(1, len(lines)) if lines[i].rstrip() == _DELIMITER)
    try:
        loaded = load_yaml("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise SourceError(
            "migration-rewrite-invalid", "the rewritten front matter is not YAML"
        ) from error
    if not isinstance(loaded, dict):
        raise SourceError(
            "migration-rewrite-invalid", "the rewritten front matter is not a mapping"
        )
    return {str(k): v for k, v in loaded.items()}, lines[closing + 1 :]


def _check_rewrite(
    before: str, after: str, repoints: Mapping[str, str], stale_reason: StaleReason | None
) -> None:
    old_properties, old_body = _front_matter(before)
    new_properties, new_body = _front_matter(after)
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
