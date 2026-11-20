"""Checks on notes that are unfinished, out of date or unconnected."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report

_RESOLVED_BY = "resolved-by"


def _check_pending(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        if note.properties.get("status") == "pending":
            yield ctx.report(note, "the note is pending")


def _check_stale(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        if note.properties.get("stale") is True:
            reason = note.properties.get("stale-reason")
            yield ctx.report(
                note, f"the note is stale ({reason})" if reason else "the note is stale"
            )


def _check_question_open(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        if ctx.known_type(note) == "open-question" and not ctx.edge_targets(note, {_RESOLVED_BY}):
            yield ctx.report(note, "the open question has no resolved-by edge")


def _check_orphan(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        if ctx.checked_type(note) == "source":
            continue
        authors_an_edge = any(link.target is not None for link in note.links)
        if not authors_an_edge and note.id not in ctx.linked_ids:
            yield ctx.report(note, "no edge leaves the note and no other note names it")


def _check_source_superseded(ctx: LintContext) -> Iterator[Report]:
    successors = {
        source.supersedes: source.key for source in ctx.ruleset.sources if source.supersedes
    }
    for note in ctx.notes:
        cited = sorted({c.ref.source for c in ctx.citations(note) if c.ref is not None})
        for key in cited:
            if key in successors:
                yield ctx.report(note, f"cites {key}, which {successors[key]} supersedes")
