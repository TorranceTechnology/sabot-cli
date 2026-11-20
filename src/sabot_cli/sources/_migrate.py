"""Decide what a new edition does to a note's citations.

The decision is pure: it reads the classification of the old edition's spans and the two span
files. Editing a note to match is the `migrate-edition` command's work.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sabot_cli.sources._diff import EditionDiff
from sabot_cli.sources._records import SpanIndex, SpanRecord
from sabot_cli.sources._spanref import SpanRef, parse_span_ref

type Outcome = Literal["clean", "editorial", "substantive", "removed"]
type StaleReason = Literal["editorial", "substantive", "removed"]

# Higher is more severe; a note takes the most severe outcome of the spans it cites.
SEVERITY: Mapping[str, int] = {"clean": 0, "editorial": 1, "substantive": 2, "removed": 3}

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

    def reason_to_write(self, properties: Mapping[str, object]) -> StaleReason | None:
        """The reason to set on the note, or None when nothing needs to change.

        A reason never softens: a note already stale for a more severe reason keeps it.
        """
        reason = self.reason
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
    if ref.sentence is not None and ref.record_span not in old:
        return None
    if ref.subtree:
        return _judge_subtree(root, diff, old, new)
    fate = diff.fate_of(root.locator)
    if fate is None:
        return None
    kind, target = fate
    if kind in ("substantive", "removed"):
        return _Judgement(kind, "", True)
    landed = SpanRef(diff.new_key, root.locator if target is None else target, ref.sentence)
    if ref.sentence is not None and not _same_sentence(
        old, ref.record_span, new, landed.record_span
    ):
        # An editorial edit to the span can move a sentence boundary, so the number alone does
        # not say that the note still cites the same sentence.
        return _Judgement("substantive", "", kind == "moved")
    return _Judgement(
        "editorial" if kind == "editorial" else "clean", landed.record_span, kind == "moved"
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
