"""The `compile` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import Rules, load_rules
from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
)
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import (
    BuildProblem,
    Graph,
    RulesetError,
    Severity,
    compile_graph,
    lint,
)
from sabot_cli.vault import Vault
from sabot_cli.vault.paths import display_path, relative_within
from sabot_cli.vault.transaction import Transaction, TransactionError, atomic_write

NAME = "compile"
DEFAULT_GRAPH_PATH = Path("_index") / "graph.json"


def register(subparsers: Subparsers) -> None:
    """Add the `compile` subparser."""
    parser = subparsers.add_parser(NAME, help="write the machine graph")
    parser.add_argument("--out", type=Path, metavar="PATH", help="where to write the graph")
    parser.add_argument(
        "--check", action="store_true", help="fail if the written graph is out of date"
    )
    add_vault_option(parser)
    add_json_option(parser)


def _lint_errors(vault: Vault, rules: Rules) -> tuple[BuildProblem, ...]:
    """The errors `lint` reports without `--strict`: what stops a compile."""
    return tuple(
        BuildProblem(finding.code, finding.note, finding.path, finding.message)
        for finding in lint(vault, rules.fixed_schema, rules.ruleset)
        if finding.severity is Severity.ERROR
    )


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code.

    A graph written inside the vault goes through a transaction, which also holds the write
    lock while the vault is read; a graph written elsewhere is staged and renamed into place.
    """
    as_json = bool(args.json)
    root: Path = args.vault
    target = root / DEFAULT_GRAPH_PATH if args.out is None else Path(args.out)
    inside = relative_within(target, root)
    if args.check or inside is None:
        return _compile(args, target, None, as_json=as_json)
    try:
        with Transaction(root) as transaction:
            return _compile(args, target, transaction, as_json=as_json)
    except TransactionError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)


def _compile(
    args: argparse.Namespace, target: Path, transaction: Transaction | None, *, as_json: bool
) -> int:
    root: Path = args.vault
    try:
        rules = load_rules(root)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    vault = rules.load_notes(root)
    problems = _lint_errors(vault, rules)
    if problems:
        _report_problems(problems, as_json=as_json)
        return int(ExitCode.FINDINGS)
    graph = compile_graph(vault, rules.fixed_schema, rules.ruleset)
    text = graph.to_json()
    shown = display_path(target, root)
    if args.check:
        current = target.read_bytes() if target.is_file() else None
        up_to_date = current == text.encode("utf-8")
        _report(graph, shown, as_json=as_json, written=False, up_to_date=up_to_date)
        return int(ExitCode.OK if up_to_date else ExitCode.FINDINGS)
    if refusal := _out_refusal(target, root, vault):
        return output.refuse(NAME, refusal[0], refusal[1], as_json=as_json)
    try:
        if transaction is not None:
            inside = relative_within(target, root)
            assert inside is not None  # run() opens a transaction only for a target inside
            transaction.put(inside.as_posix(), text.encode("utf-8"))
            transaction.commit()
        else:
            atomic_write(target, text.encode("utf-8"))
    except OSError as error:
        return output.refuse(
            NAME, "write-failed", f"a file could not be written: {error.strerror}", as_json=as_json
        )
    _report(graph, shown, as_json=as_json, written=True, up_to_date=True)
    return int(ExitCode.OK)


def _out_refusal(target: Path, root: Path, vault: Vault) -> tuple[str, str] | None:
    """Why `--out` may not be written, as (code, message): it names a directory or a note."""
    if target.is_dir():
        return "out-is-a-directory", "--out names a directory; name a file"
    inside = relative_within(target, root)
    if inside is None:
        return None
    notes = {note.path for note in vault.notes.values()} | {p.path for p in vault.problems}
    if inside in notes:
        return "out-is-a-note", "--out names a note of the vault; the graph would overwrite it"
    return None


def _report_problems(problems: Sequence[BuildProblem], *, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "compiled": False,
                "counts": {"error": len(problems)},
                "findings": [
                    {
                        "class": p.code,
                        "message": p.message,
                        "note": p.note,
                        "path": p.path,
                        "severity": "error",
                    }
                    for p in problems
                ],
                "format": output.FORMAT_VERSION,
            }
        )
        return
    output.write_text("".join(f"error {p.code} {p.note or p.path} {p.message}\n" for p in problems))
    output.write_diagnostic("sabot compile: the vault has errors; nothing was written")


def _report(graph: Graph, path: str, *, as_json: bool, written: bool, up_to_date: bool) -> None:
    by_kind: dict[str, JsonValue] = {
        kind: len(entries)
        for kind, entries in graph.extensions.items()
        if isinstance(entries, dict)
    }
    authored = graph.edges_by_type(derived=False)
    derived = graph.edges_by_type(derived=True)
    if as_json:
        output.write_json(
            {
                "authoredEdges": sum(authored.values()),
                "authoredEdgesByType": dict(authored),
                "command": NAME,
                "compiled": written,
                "derivedEdges": sum(derived.values()),
                "derivedEdgesByType": dict(derived),
                "extensionCount": graph.extension_count,
                "extensionsByKind": by_kind,
                "format": output.FORMAT_VERSION,
                "notes": len(graph.nodes),
                "notesByType": dict(graph.notes_by_type()),
                "path": path,
                "upToDate": up_to_date,
                "vaultDigest": graph.vault_digest,
            }
        )
        return
    if written:
        headline = f"wrote {path}"
    else:
        headline = f"{path} is {'up to date' if up_to_date else 'out of date'}"
    rows = [
        ["notes", str(len(graph.nodes))],
        ["authored edges", str(sum(authored.values()))],
        ["derived edges", str(sum(derived.values()))],
        ["extensions", str(graph.extension_count)],
        ["vault digest", graph.vault_digest],
    ]
    output.write_text(f"{headline}\n{output.format_table(['summary', 'count'], rows)}")
