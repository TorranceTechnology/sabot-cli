"""Checks a segmentation must pass before anything is written."""

import itertools
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sabot_cli.sources._records import SpanRecord
from sabot_cli.sources._spanref import locator_problem

SELF_CHECKS: tuple[str, ...] = (
    "duplicate-locator",
    "locator-invalid",
    "no-spans",
    "non-monotone",
    "page-decreasing",
)
_NUMBER_KEY = re.compile(r"^(\d+(?:\.\d+)*)([a-z]?)$")


@dataclass(frozen=True)
class CheckResult:
    """One named check: it passed when it has no failures; each failure names a place."""

    name: str
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether the check found nothing."""
        return not self.failures


def _number_key(locator: str) -> tuple[tuple[int, ...], str] | None:
    """The sort key of a numbered locator's last part, or None for a slug or hash."""
    match = _NUMBER_KEY.match(locator.rsplit("/", 1)[-1])
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split(".")), match.group(2)


def run_self_checks(records: Sequence[SpanRecord]) -> tuple[CheckResult, ...]:
    """Run every check in `SELF_CHECKS` over records in document order."""
    spans = [record for record in records if record.kind == "span"]
    counts = Counter(record.locator for record in records)
    by_parent: dict[str | None, list[SpanRecord]] = {}
    for span in spans:
        by_parent.setdefault(span.parent, []).append(span)
    descending: list[str] = []
    for siblings in by_parent.values():
        keyed = [(span, _number_key(span.locator)) for span in siblings]
        numbered = [(span, key) for span, key in keyed if key is not None]
        descending += [
            later.locator
            for (_, earlier), (later, key) in itertools.pairwise(numbered)
            if earlier is not None and key is not None and key < earlier
        ]
    pages = [
        later.locator for earlier, later in itertools.pairwise(spans) if later.page < earlier.page
    ]
    uncitable = sorted({span.locator for span in spans if locator_problem(span.locator)})
    return (
        CheckResult("duplicate-locator", tuple(sorted(n for n, c in counts.items() if c > 1))),
        CheckResult("locator-invalid", tuple(uncitable)),
        CheckResult("no-spans", () if spans else ("the segmentation produced no spans",)),
        CheckResult("non-monotone", tuple(sorted(set(descending)))),
        CheckResult("page-decreasing", tuple(sorted(set(pages)))),
    )


def merge(*groups: Sequence[CheckResult]) -> tuple[CheckResult, ...]:
    """Combine results that share a name, listing each failure once, ordered by name."""
    combined: dict[str, set[str]] = {}
    for group in groups:
        for result in group:
            combined.setdefault(result.name, set()).update(result.failures)
    return tuple(
        CheckResult(name, tuple(sorted(found))) for name, found in sorted(combined.items())
    )
