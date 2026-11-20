"""Front-matter filters: `--type`, `--status` and `--where` expressions."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from sabot_cli.search._model import InvalidFilterError
from sabot_cli.vault import Note, Vault, parse_wikilink

_KEY = r"[^=~!:\s]+"
_COMPARISON = re.compile(rf"^({_KEY})(!=|=|~)(.*)$", re.DOTALL)
_PRESENCE = re.compile(rf"^(has|missing):({_KEY})$")
_FORMS = "key=value, key!=value, key~text, has:key or missing:key"


class _Operator(Enum):
    EQUALS = "="
    NOT_EQUALS = "!="
    CONTAINS = "~"
    HAS = "has"
    MISSING = "missing"


@dataclass(frozen=True)
class _Filter:
    key: str
    operator: _Operator
    operand: str


def query(
    vault: Vault, types: Sequence[str], statuses: Sequence[str], where: Sequence[str]
) -> tuple[Note, ...]:
    """The notes passing the filters, sorted by id.

    Conditions on one property are alternatives and conditions on different properties must
    all hold. `types` and `statuses` are conditions on the properties `type` and `status`, so
    they combine with a `where` expression on the same property by the same rule; `has:key` and
    `missing:key` are conditions on `key`. No condition at all does not restrict. A
    link-valued property compares by target id, a list property by its items, and a property
    holding nothing (absent, null or an empty list) is absent: `key!=value` accepts it,
    `key=value`, `key~text` and `has:key` do not.
    Raises `InvalidFilterError` for an expression that is not one of the supported forms.
    """
    conditions = [
        *(_Filter("type", _Operator.EQUALS, value) for value in types),
        *(_Filter("status", _Operator.EQUALS, value) for value in statuses),
        *(_parse(expression) for expression in where),
    ]
    by_property: dict[str, list[_Filter]] = {}
    for condition in conditions:
        by_property.setdefault(condition.key, []).append(condition)
    passing = (
        note
        for note in vault.notes.values()
        if all(any(_holds(note, one) for one in group) for group in by_property.values())
    )
    return tuple(sorted(passing, key=lambda note: note.id))


def _parse(expression: str) -> _Filter:
    presence = _PRESENCE.match(expression)
    if presence is not None:
        return _Filter(presence.group(2), _Operator(presence.group(1)), "")
    comparison = _COMPARISON.match(expression)
    if comparison is None:
        raise InvalidFilterError(f"cannot read the filter {expression!r}; use {_FORMS}")
    operator = _Operator(comparison.group(2))
    operand = comparison.group(3)
    if operator is not _Operator.CONTAINS:
        operand = parse_wikilink(operand) or operand
    return _Filter(comparison.group(1), operator, operand)


def _holds(note: Note, rule: _Filter) -> bool:
    values = _values(note.properties.get(rule.key))
    match rule.operator:
        case _Operator.EQUALS:
            return rule.operand in values
        case _Operator.NOT_EQUALS:
            return rule.operand not in values
        case _Operator.CONTAINS:
            needle = rule.operand.casefold()
            return any(needle in value.casefold() for value in values)
        case _Operator.HAS:
            return bool(values)
        case _Operator.MISSING:
            return not values


def _values(raw: object) -> tuple[str, ...]:
    """The comparable texts a property holds: each list item, a wikilink as its target id."""
    if raw is None:
        return ()
    if isinstance(raw, list):
        return tuple(text for item in raw for text in _values(item))
    return (_text(raw),)


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return parse_wikilink(value) or value
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)
