"""Checks on what a note's status and type require of it."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext, is_present
from sabot_cli.rules._checks._model import Report

# `superseded` has a class of its own, so the status rule does not report it a second time.
_STATUS_WITH_OWN_CLASS = "superseded"
_IMPLEMENTED_BY = "implemented-by"
_DEFAULT_SUCCESSOR_EDGES = ("refines", "resolved-by")


def _check_status_requirement(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        status = note.properties.get("status")
        requirement = (
            ctx.schema.status_requirements.get(status) if isinstance(status, str) else None
        )
        if requirement is None or status == _STATUS_WITH_OWN_CLASS:
            continue
        for name in requirement.attributes:
            if not is_present(note.properties.get(name)):
                yield ctx.report(note, f"a {status} note needs {name}")
        if requirement.any_edge and not ctx.edge_targets(note, set(requirement.any_edge)):
            yield ctx.report(
                note, f"a {status} note needs one of: {', '.join(requirement.any_edge)}"
            )
        if status == "refused" and ctx.edge_targets(note, {_IMPLEMENTED_BY}):
            yield ctx.report(note, "a refused note has no implemented-by edge")


def _check_type_requirement(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        checked = ctx.checked_type(note)
        requirement = ctx.schema.type_requirements.get(checked) if checked else None
        if requirement is None:
            continue
        for name in requirement.attributes:
            if not is_present(note.properties.get(name)):
                yield ctx.report(note, f"a {checked} note needs {name}")
        if requirement.any_edge and not ctx.edge_targets(note, set(requirement.any_edge)):
            yield ctx.report(
                note, f"a {checked} note needs one of: {', '.join(requirement.any_edge)}"
            )
        if requirement.any_inbound_edge and not any(
            ctx.inbound_by_edge.get((name, note.id)) for name in requirement.any_inbound_edge
        ):
            yield ctx.report(
                note,
                f"a {checked} note needs another note to name it with: "
                f"{', '.join(requirement.any_inbound_edge)}",
            )


def _check_superseded_without_successor(ctx: LintContext) -> Iterator[Report]:
    rule = ctx.schema.status_requirements.get(_STATUS_WITH_OWN_CLASS)
    successor_edges = rule.any_edge if rule and rule.any_edge else _DEFAULT_SUCCESSOR_EDGES
    for note in ctx.notes:
        if note.properties.get("status") == _STATUS_WITH_OWN_CLASS and not ctx.edge_targets(
            note, set(successor_edges)
        ):
            yield ctx.report(
                note,
                f"a superseded note links its successor with one of: {', '.join(successor_edges)}",
            )
