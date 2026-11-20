"""Checks on a note's identity and shape: front matter, id, type, schema version."""

import re
from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext, is_present
from sabot_cli.rules._checks._model import Report
from sabot_cli.vault import ProblemKind
from sabot_cli.vault.paths import reserved_device_name, windows_unusable_entries


def _check_frontmatter_invalid(ctx: LintContext) -> Iterator[Report]:
    for problem in ctx.vault.problems:
        if problem.kind is ProblemKind.UNREADABLE:
            yield Report(problem.path.as_posix(), None, problem.message)


def _check_id_duplicate(ctx: LintContext) -> Iterator[Report]:
    for problem in ctx.vault.problems:
        if problem.kind is ProblemKind.DUPLICATE_ID:
            # A problem carries no id; the file name is what a duplicate usually shares.
            yield Report(problem.path.as_posix(), problem.path.stem, problem.message)


def _check_required_missing(ctx: LintContext) -> Iterator[Report]:
    required = [name for name, attribute in ctx.schema.attributes.items() if attribute.required]
    for note in ctx.notes:
        for name in required:
            if not is_present(note.properties.get(name)):
                yield ctx.report(note, f"the required attribute {name} is missing")


def _check_id_malformed(ctx: LintContext) -> Iterator[Report]:
    pattern = re.compile(ctx.schema.attributes["id"].pattern or "")
    for note in ctx.notes:
        value = note.properties.get("id")
        if value is None:
            continue
        if not isinstance(value, str) or not pattern.fullmatch(value):
            yield ctx.report(note, f"the id {value!r} does not match {pattern.pattern}")


def _check_id_reserved_name(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        declared = note.properties.get("id")
        if isinstance(declared, str) and reserved_device_name(declared):
            yield ctx.report(
                note,
                f"the id {declared!r} starts with a name Windows reserves for a device, "
                "so a note file named for it cannot be checked out there",
            )
    notes_by_path = {note.path.as_posix(): note for note in ctx.notes}
    for relative, name, is_directory, reason in windows_unusable_entries(ctx.vault.root):
        kind = "directory" if is_directory else "file"
        message = f"the {kind} name {name} {reason}, so it cannot be checked out there"
        if (owner := notes_by_path.get(relative)) is not None:
            yield ctx.report(owner, message)
        else:
            yield Report(relative, None, message)


def _check_id_file_mismatch(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        declared = note.properties.get("id")
        if isinstance(declared, str) and declared != note.path.stem:
            yield ctx.report(
                note, f"the id {declared!r} differs from the file name {note.path.name}"
            )


def _check_id_prefix_mismatch(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        declared_type = ctx.known_type(note)
        declared_id = note.properties.get("id")
        if declared_type is None or not isinstance(declared_id, str):
            continue
        prefix = ctx.schema.node_types[declared_type].prefix
        if not declared_id.startswith(prefix):
            yield ctx.report(
                note,
                f"the id {declared_id!r} does not start with {prefix!r}, "
                f"the prefix of {declared_type}",
            )


def _check_type_unknown(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        value = note.properties.get("type")
        if value is not None and ctx.known_type(note) is None:
            yield ctx.report(note, f"the type {value!r} is neither a fixed node type nor declared")


def _check_schema_version_mismatch(ctx: LintContext) -> Iterator[Report]:
    expected = ctx.ruleset.schema_version
    for note in ctx.notes:
        value = note.properties.get("schema")
        if isinstance(value, int) and not isinstance(value, bool) and value != expected:
            yield ctx.report(
                note, f"schema is {value}, but the ruleset declares schemaVersion {expected}"
            )
