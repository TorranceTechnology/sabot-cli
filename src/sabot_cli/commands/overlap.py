"""The `overlap` command."""

import argparse
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import RulesetError, load_ruleset
from sabot_cli.sources import (
    NoteOverlap,
    OverlapReport,
    SourceError,
    overlap,
    read_text_file,
)
from sabot_cli.vault import load_vault

NAME = "overlap"


def register(subparsers: Subparsers) -> None:
    """Add the `overlap` subparser."""
    parser = subparsers.add_parser(NAME, help="detect copied source wording in note bodies")
    parser.add_argument("paths", nargs="*", type=Path, help="notes to check (default: every note)")
    parser.add_argument(
        "--source-text", required=True, type=Path, metavar="PATH", help="extracted source text"
    )
    parser.add_argument(
        "--fail-at", type=int, default=10, metavar="WORDS", help="run length that fails"
    )
    parser.add_argument(
        "--warn-at", type=int, default=8, metavar="WORDS", help="run length that warns"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code: 1 when a run reaches `--fail-at`."""
    as_json = bool(args.json)
    vault: Path = args.vault
    try:
        load_ruleset(vault)
        source_path: Path = args.source_text
        if _is_inside(source_path, vault):
            raise SourceError(
                "source-text-inside-vault",
                "the source text must be kept outside the vault, which may be shared",
            )
        source = read_text_file(source_path)
        loaded = load_vault(vault, frozenset())
        bodies = _select_bodies(
            vault, {n.path.as_posix(): n.body for n in loaded.notes.values()}, args.paths
        )
        report = overlap(source, bodies, int(args.fail_at), int(args.warn_at))
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    except SourceError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    unreadable = sorted(problem.path.as_posix() for problem in loaded.problems)
    if as_json:
        output.write_json(_document(report, unreadable))
    else:
        output.write_text(_text(report, unreadable))
    return int(ExitCode.FINDINGS if report.failed else ExitCode.OK)


def _is_inside(path: Path, directory: Path) -> bool:
    return directory.resolve() in (path.resolve(), *path.resolve().parents)


def _select_bodies(vault: Path, bodies: dict[str, str], paths: list[Path]) -> dict[str, str]:
    """All bodies, or those of the named notes. A path is read as given, then under the vault."""
    if not paths:
        return bodies
    chosen: dict[str, str] = {}
    root = vault.resolve()
    for path in paths:
        candidate = path if path.exists() else vault / path
        try:
            relative = candidate.resolve().relative_to(root).as_posix()
        except ValueError as error:
            raise SourceError(
                "path-outside-vault", f"{path.name} is not under the vault"
            ) from error
        if relative not in bodies:
            raise SourceError("path-not-a-note", f"{relative} is not a note the vault could read")
        chosen[relative] = bodies[relative]
    return chosen


def _entry(note: NoteOverlap) -> dict[str, JsonValue]:
    return {
        "bodyEnd": note.body_end,
        "bodyStart": note.body_start,
        "path": note.path,
        "status": note.status,
        "words": note.length,
    }


def _document(report: OverlapReport, unreadable: list[str]) -> dict[str, JsonValue]:
    return {
        "command": NAME,
        "failAt": report.fail_at,
        "failed": len(report.failed),
        "format": output.FORMAT_VERSION,
        "notes": [_entry(note) for note in report.notes],
        "unreadable": list(unreadable),
        "warnAt": report.warn_at,
        "warned": len(report.warned),
    }


def _text(report: OverlapReport, unreadable: list[str]) -> str:
    rows = [
        [
            note.path,
            str(note.length),
            "-" if note.body_start is None else f"{note.body_start}-{note.body_end}",
            note.status,
        ]
        for note in report.notes
    ]
    table = output.format_table(["note", "words", "body offsets", "status"], rows)
    summary = f"failed: {len(report.failed)}\nwarned: {len(report.warned)}\n"
    skipped = "".join(f"unreadable: {path}\n" for path in unreadable)
    return table + "\n" + summary + skipped
