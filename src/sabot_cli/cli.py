"""The `sabot` command-line dispatcher.

Each command lives in its own module under `sabot_cli.commands` and has this shape:

- `NAME`: the command's name as typed on the command line.
- `register(subparsers)`: adds the command's subparser with its full argument syntax.
- `run(args) -> int`: executes the command and returns an exit code from `ExitCode`.
- All stdout goes through `sabot_cli.output`; refusals go through `output.refuse`.

A new command is imported here and added to `_COMMANDS`.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sabot_cli import output
from sabot_cli.commands import (
    compile as compile_command,
)
from sabot_cli.commands import (
    coverage,
    diff_editions,
    ingest,
    init,
    lint,
    migrate_edition,
    overlap,
    recover,
    schema,
    search,
    segment,
    traverse,
)
from sabot_cli.commands._options import Subparsers
from sabot_cli.exit_codes import ExitCode
from sabot_cli.vault.transaction import pending_journals


class CommandModule(Protocol):
    """What cli.py requires of a module under `sabot_cli.commands`."""

    NAME: str

    def register(self, subparsers: Subparsers) -> None: ...

    def run(self, args: argparse.Namespace) -> int: ...


_EXEMPT_FROM_UNFINISHED_WRITE = frozenset({"lint", "recover"})

_COMMANDS: tuple[CommandModule, ...] = (
    init,
    ingest,
    segment,
    lint,
    compile_command,
    coverage,
    diff_editions,
    migrate_edition,
    overlap,
    recover,
    schema,
    search,
    traverse,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sabot", description="Tools for building games on the Sabot rules engine."
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for command in _COMMANDS:
        command.register(subparsers)
    return parser


def _exit_status(code: object) -> int:
    if code is None:
        return int(ExitCode.OK)
    if isinstance(code, int):
        return code
    return int(ExitCode.USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code instead of raising SystemExit."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        # argparse exits for --help and usage errors; tests and callers want a return value.
        return _exit_status(stop.code)
    name = str(args.command)
    if refusal := _unfinished_write(name, args):
        return output.refuse(
            name, "transaction-incomplete", refusal, as_json=bool(getattr(args, "json", False))
        )
    return {command.NAME: command for command in _COMMANDS}[name].run(args)


def _unfinished_write(name: str, args: argparse.Namespace) -> str | None:
    """The refusal message when the vault a command reads or writes holds an unfinished write.

    `lint` reports the condition as a finding and `recover` is the way out, so neither refuses.
    """
    if name in _EXEMPT_FROM_UNFINISHED_WRITE:
        return None
    root = getattr(args, "directory", None) or getattr(args, "vault", None)
    if not isinstance(root, Path):
        return None
    if journals := pending_journals(root):
        return f"an earlier write was not finished ({journals[0]}); run sabot recover"
    return None
