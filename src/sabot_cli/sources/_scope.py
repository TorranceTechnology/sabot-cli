"""Which spans lie under a scope prefix, and the ancestry that decides it."""

from collections.abc import Mapping, Sequence

from sabot_cli.sources._records import SpanRecord
from sabot_cli.sources._spanref import locator_under


def ancestry(record: SpanRecord, by_locator: Mapping[str, SpanRecord]) -> list[str]:
    """The locators of the record and of each ancestor, nearest first, so `4.2a` lies under 4.2."""
    locators: list[str] = []
    current: SpanRecord | None = record
    while current is not None and current.locator not in locators:
        locators.append(current.locator)
        current = by_locator.get(current.parent) if current.parent is not None else None
    return locators


def in_scope(
    record: SpanRecord, by_locator: Mapping[str, SpanRecord], scope: Sequence[str]
) -> bool:
    """Whether the record or an ancestor lies under one of the scope prefixes; an empty scope
    holds every record."""
    if not scope:
        return True
    return any(
        locator_under(locator, prefix)
        for locator in ancestry(record, by_locator)
        for prefix in scope
    )
