"""Checks on the span references a note cites."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.sources import span_file_relative

_SUBTREE_STATUSES = frozenset({"refused", "structural"})


def _check_span_unknown(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for citation in ctx.citations(note):
            ref = citation.ref
            if ref is None:
                yield ctx.report(
                    note, f"{citation.raw!r} is not a span reference: {citation.problem}"
                )
                continue
            index, problem = ctx.span_index(ref.source)
            if index is None:
                yield ctx.report(note, f"{citation.raw} cannot be checked: {problem}")
                continue
            if ref.record_span not in index:
                yield ctx.report(note, f"{citation.raw} is not in {span_file_relative(ref.source)}")


def _check_span_subtree_misused(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        if note.properties.get("status") in _SUBTREE_STATUSES:
            continue
        for citation in ctx.citations(note):
            if citation.ref is not None and citation.ref.subtree:
                yield ctx.report(
                    note,
                    f"{citation.raw} cites a subtree; only refused and structural notes may",
                )
