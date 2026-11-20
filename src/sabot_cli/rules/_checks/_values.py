"""Checks on attribute names and values against the merged schema."""

import re
from collections.abc import Iterator
from datetime import date

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.rules.schema import Attribute

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SKIPPED_VALUE_KEYS = frozenset({"id", "type"})


def _check_attribute_unknown(ctx: LintContext) -> Iterator[Report]:
    link_names = ctx.schema.link_properties
    for note in ctx.notes:
        names = ctx.type_names(note)
        for key in note.properties:
            if key in link_names:
                continue
            attribute = ctx.schema.attributes.get(key)
            if attribute is None:
                yield ctx.report(note, f"{key} is neither a schema v1 attribute nor declared")
            elif attribute.on and names and not names & set(attribute.on):
                allowed = ", ".join(attribute.on)
                yield ctx.report(
                    note, f"{key} is not allowed on {sorted(names)[0]}; it applies to {allowed}"
                )


def _check_enum_invalid(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        names = ctx.type_names(note)
        for key, value in note.properties.items():
            attribute = ctx.schema.attributes.get(key)
            if attribute is None or value is None or key in _SKIPPED_VALUE_KEYS:
                continue
            problem = _value_problem(ctx, attribute, value, names)
            if problem is not None:
                yield ctx.report(note, f"{key} {problem}")


def _value_problem(
    ctx: LintContext, attribute: Attribute, value: object, names: frozenset[str]
) -> str | None:
    match attribute.type:
        case "text":
            return None if isinstance(value, str) else "must be text"
        case "date":
            return None if _is_date(value) else "must be a date written YYYY-MM-DD"
        case "int":
            return _int_problem(attribute, value)
        case "bool":
            return None if isinstance(value, bool) else "must be true or false"
        case "enum":
            return _enum_problem(ctx, attribute, value, names)
        case "list":
            return _list_problem(ctx, attribute, value)
        case _:
            return None


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _int_problem(attribute: Attribute, value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return "must be a whole number"
    if attribute.minimum is not None and value < attribute.minimum:
        return f"must be at least {attribute.minimum}"
    return None


def _allowed_values(
    ctx: LintContext, attribute: Attribute, names: frozenset[str]
) -> tuple[str, ...] | None:
    """The values an enum accepts for this note, or None when another check owns the question."""
    if attribute.values_from == "node-types":
        return None
    if attribute.values_from == "statuses":
        return ctx.schema.statuses
    if attribute.values_from == "edge-types":
        return tuple(ctx.schema.edge_types)
    if attribute.values_by_type:
        for name in sorted(names):
            if name in attribute.values_by_type:
                return attribute.values_by_type[name]
        return None
    return attribute.values


def _enum_problem(
    ctx: LintContext, attribute: Attribute, value: object, names: frozenset[str]
) -> str | None:
    allowed = _allowed_values(ctx, attribute, names)
    if allowed is None or (isinstance(value, str) and value in allowed):
        return None
    return f"is {value!r}, which is not one of: {', '.join(allowed)}"


def _list_problem(ctx: LintContext, attribute: Attribute, value: object) -> str | None:
    if not isinstance(value, list):
        return "must be a list"
    if attribute.of in (None, "text", "spanref"):
        return None if all(isinstance(item, str) for item in value) else "must hold text only"
    if attribute.of == "id":
        pattern = re.compile(ctx.schema.attributes["id"].pattern or "")
        bad = [item for item in value if not isinstance(item, str) or not pattern.fullmatch(item)]
        return f"holds {bad[0]!r}, which is not an id" if bad else None
    if attribute.of == "construct-kind":
        bad = [item for item in value if item not in ctx.schema.construct_kinds]
        return f"holds {bad[0]!r}, which is not a construct kind" if bad else None
    return None
