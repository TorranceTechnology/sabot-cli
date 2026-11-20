"""PKG-1 and PKG-2: the console script and the shape of a refusal."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CliRunner
from invocations import COMMAND_NAMES, invocation
from sabot_cli.cli import main

# `init` writes a directory instead of reading a vault, so it has no --vault to point wrongly.
VAULT_COMMANDS = tuple(name for name in COMMAND_NAMES if name != "init")


def _console_script() -> str:
    found = shutil.which("sabot", path=str(Path(sys.executable).parent))
    assert found is not None, "the sabot console script is not installed in this environment"
    return found


def test_console_script_lists_every_command() -> None:
    # Given the installed package
    # When the console script runs with --help
    result = subprocess.run([_console_script(), "--help"], capture_output=True, text=True)

    # Then it exits 0 and names every command
    assert result.returncode == 0
    for name in COMMAND_NAMES:
        assert name in result.stdout


def test_module_entry_point_lists_every_command() -> None:
    # Given the installed package
    # When it runs as a module with --help
    result = subprocess.run(
        [sys.executable, "-m", "sabot_cli", "--help"], capture_output=True, text=True
    )

    # Then it exits 0 and names every command
    assert result.returncode == 0
    for name in COMMAND_NAMES:
        assert name in result.stdout


def test_unknown_command_is_a_usage_error(run_cli: CliRunner) -> None:
    # Given no such command
    # When it is run
    result = run_cli("no-such-command")

    # Then the exit code is the usage code and stdout is empty
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.parametrize("name", VAULT_COMMANDS)
def test_json_error_shape_on_input_refusal(name: str, run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault directory that does not exist
    missing = tmp_path / "missing"

    # When the command runs with --json
    result = run_cli(name, *invocation(name, tmp_path), "--vault", str(missing), "--json")

    # Then it exits 3 and stdout holds the error object
    assert result.returncode == 3
    document = result.json()
    assert document["command"] == name
    assert document["format"] == 1
    error = document["error"]
    assert isinstance(error, dict)
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str)
    for spelling in (str(tmp_path), tmp_path.as_posix(), json.dumps(str(tmp_path))[1:-1]):
        assert spelling not in result.stdout


@pytest.mark.parametrize(
    ("command", "option", "default"),
    [
        ("ingest", "--kind", "(default: edition)"),
        ("ingest", "--format", "(default: detected"),
        ("ingest", "--vault", "(default: the current directory)"),
        ("segment", "--numbering", "(default: dotted)"),
        ("segment", "--skip-pages", "(default: 0)"),
        ("traverse", "--direction", "(default: out)"),
        ("traverse", "--depth", "(default: 1)"),
        ("overlap", "--fail-at", "(default: 10)"),
        ("overlap", "--warn-at", "(default: 8"),
        ("compile", "--out", "(default: _index/graph.json"),
        ("recover", "--force", "(default: refuse)"),
        ("search", "--limit", "(default 20"),
    ],
)
def test_help_states_the_default_of_each_option_that_has_one(
    command: str, option: str, default: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a command whose option has a default
    # When its help is printed
    assert main([command, "--help"]) == 0
    entries = re.split(r"\n  (?=-)", capsys.readouterr().out)
    (entry,) = (e for e in entries if e.split()[0].rstrip(",") == option)

    # Then the option's own entry says what the default is
    assert default in " ".join(entry.split()), f"{command} {option} does not state its default"
