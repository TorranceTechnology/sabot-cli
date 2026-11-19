"""DET-1 and DET-2: output is the same on every run and carries no machine-specific paths.

A command that still is a stub cannot be judged, so its cases are strict expected failures.
When a command is implemented its cases start passing, the strict mark turns that into a
failure, and the fix is to delete the command from `STUB_COMMANDS`.
"""

from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from invocations import COMMAND_NAMES, invocation

STUB_COMMANDS = frozenset(COMMAND_NAMES) - {
    "init",
    "compile",
    "schema",
    "lint",
    "coverage",
    "ingest",
    "segment",
}

COMMANDS = [
    pytest.param(
        name,
        marks=pytest.mark.xfail(
            strict=True,
            reason=f"{name} is still a stub; remove it from STUB_COMMANDS when implemented",
        ),
    )
    if name in STUB_COMMANDS
    else name
    for name in COMMAND_NAMES
]


def _run_json(run_cli: CliRunner, name: str, vault: Path, workdir: Path) -> CliResult:
    arguments = invocation(name, workdir)
    vault_options = () if name == "init" else ("--vault", str(vault))
    result = run_cli(name, *arguments, *vault_options, "--json")
    assert not (result.returncode == 3 and '"not-implemented"' in result.stdout), (
        f"{name} is not implemented"
    )
    return result


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_is_byte_identical_across_runs(
    name: str,
    run_cli: CliRunner,
    driftwood_vault: Path,
    driftwood_vault_twin: Path,
    tmp_path: Path,
) -> None:
    # Given two copies of the vault whose files were created in different orders
    first_work = tmp_path / "work-first"
    second_work = tmp_path / "work-second"
    first_work.mkdir()
    second_work.mkdir()

    # When the command runs on each
    first = _run_json(run_cli, name, driftwood_vault, first_work)
    second = _run_json(run_cli, name, driftwood_vault_twin, second_work)

    # Then stdout, the exit code and every file left in the vault are byte-identical
    assert first.returncode == second.returncode
    assert first.stdout == second.stdout
    if name == "init":
        assert _snapshot(first_work) == _snapshot(second_work)
    else:
        assert _snapshot(driftwood_vault) == _snapshot(driftwood_vault_twin)


@pytest.mark.parametrize("name", COMMANDS)
def test_outputs_carry_no_absolute_paths(
    name: str, run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given a vault under a temporary directory
    # When the command runs in both output formats
    json_result = _run_json(run_cli, name, driftwood_vault, tmp_path)
    arguments = invocation(name, tmp_path / "text")
    vault_options = () if name == "init" else ("--vault", str(driftwood_vault))
    text_result = run_cli(name, *arguments, *vault_options)

    # Then neither stream names the temporary directory
    for output in (json_result.stdout, text_result.stdout, text_result.stderr):
        assert str(tmp_path) not in output
        assert tmp_path.as_posix() not in output
