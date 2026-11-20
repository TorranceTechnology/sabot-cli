"""What a check reports and what lint returns."""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How seriously a class is taken.

    `WARNING_STRICT` is a class severity only: such a class is a warning that `--strict` raises
    to an error. A finding itself is always `ERROR` or `WARNING`.
    """

    ERROR = "error"
    WARNING = "warning"
    WARNING_STRICT = "warning-strict"


@dataclass(frozen=True)
class Report:
    """One thing a check found; lint adds the class and the severity."""

    path: str
    note: str | None
    message: str


@dataclass(frozen=True)
class Finding:
    """One lint finding: its class, effective severity, the note and file it lies in."""

    code: str
    severity: Severity
    note: str | None
    path: str
    message: str
