"""The update loop of a new edition: migrate, review the stale notes, then lint and cover."""

from pathlib import Path

from conftest import CliResult, CliRunner
from support.plant import register_second_edition

# What a reviewer settles after reading each note the migration left stale: the note's whole
# `source` list in the second edition. The two removed rules are replaced by the new rule 6.7a
# (for the launch bonus) or dropped (for the win condition, whose rule 7.3 is gone).
REVIEWED_CITATIONS = {
    "cl.setup": ["dw2#3.1", "dw2#3.2", "dw2#3.3", "dw2#3.4"],
    "act.launch-boat": ["dw2#5.1", "dw2#5.1a"],
    "cl.launch-bonus": ["dw2#6.7", "dw2#6.7a"],
    "win.first-to-target": ["dw2#7.2"],
}


def _run(run_cli: CliRunner, command: str, vault: Path, *options: str) -> CliResult:
    return run_cli(command, "--vault", str(vault), "--json", *options)


def _review(vault: Path, note_id: str, citations: list[str]) -> None:
    """Replace a note's `source` list and clear its stale marks, as a reviewer would."""
    (path,) = vault.rglob(f"{note_id}.md")
    kept: list[str] = []
    in_source_list = False
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith(("stale:", "stale-reason:")):
            continue
        if line.startswith("source:"):
            kept += [line, *(f'  - "{citation}"' for citation in citations)]
            in_source_list = True
            continue
        if in_source_list and line.startswith("  - "):
            continue
        in_source_list = False
        kept.append(line)
    path.write_bytes("\n".join(kept).encode("utf-8"))


def test_lint_and_coverage_pass_once_the_stale_notes_are_reviewed(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the fixture migrated to its second edition
    register_second_edition(driftwood_vault)
    migrated = _run(run_cli, "migrate-edition", driftwood_vault, "dw1", "dw2")
    assert migrated.returncode == 0

    # When the migration is the last thing that touched the vault
    # Then it still has work for a reviewer: stale notes and notes citing the old edition
    assert _run(run_cli, "lint", driftwood_vault, "--strict").returncode == 1

    # When the reviewer settles each stale note on the new edition
    for note_id, citations in REVIEWED_CITATIONS.items():
        _review(driftwood_vault, note_id, citations)

    # Then strict lint and coverage both pass, so the loop can finish
    lint = _run(run_cli, "lint", driftwood_vault, "--strict")
    assert (lint.returncode, lint.json()["findings"]) == (0, [])
    coverage = _run(run_cli, "coverage", driftwood_vault)
    assert (coverage.returncode, coverage.json()["complete"]) == (0, True)
