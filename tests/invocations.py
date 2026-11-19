"""One valid invocation of each command, for tests that run every command."""

from collections.abc import Mapping
from pathlib import Path

from conftest import FIXTURES

# Every command, in the order the help lists them.
COMMAND_NAMES: tuple[str, ...] = (
    "init",
    "ingest",
    "segment",
    "lint",
    "compile",
    "coverage",
    "diff-editions",
    "migrate-edition",
    "overlap",
    "schema",
    "search",
    "traverse",
)

RULES_E1 = FIXTURES / "rules-e1.txt"
RULES_E2 = FIXTURES / "rules-e2.txt"


def invocation(name: str, workdir: Path) -> tuple[str, ...]:
    """Arguments for `name` that are valid against the fixture vault, minus --vault and --json."""
    arguments: Mapping[str, tuple[str, ...]] = {
        "init": (str(workdir / "new-vault"), "--ruleset", "driftwood"),
        "ingest": (
            str(RULES_E1),
            "--key",
            "dwx",
            "--label",
            "Driftwood extra",
            "--title",
            "Driftwood extra",
            "--edition",
            "1",
            "--retrieved",
            "2026-01-01",
            "--format",
            "text",
        ),
        "segment": (str(RULES_E1), "--source", "dw1", "--mode", "numbered"),
        "lint": (),
        "compile": (),
        "coverage": (),
        "diff-editions": ("dw1", "dw2"),
        "migrate-edition": ("dw1", "dw2", "--dry-run"),
        "overlap": ("--source-text", str(RULES_E1)),
        "schema": (),
        "search": ("tide",),
        "traverse": (
            "ph.sail",
            "--direction",
            "both",
        ),
    }
    return arguments[name]
