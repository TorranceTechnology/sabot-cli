"""The `compile` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import (
    Subparsers,
    add_json_option,
    add_vault_option,
)
from sabot_cli.exit_codes import ExitCode
from sabot_cli.output import JsonValue
from sabot_cli.rules import (
    RulesetError,
    compile_graph,
    lint,
    load_ruleset,
    load_schema,
    merge_extensions,
)
from sabot_cli.rules.graph import BuildProblem, Graph
from sabot_cli.rules.lint import Severity
from sabot_cli.rules.ruleset import Ruleset
from sabot_cli.rules.schema import Schema
from sabot_cli.vault import Vault, load_vault

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


def _lint_errors(vault: Vault, schema: Schema, ruleset: Ruleset) -> tuple[BuildProblem, ...]:
    """The errors `lint` reports without `--strict`: what stops a compile."""
    return tuple(
        BuildProblem(finding.code, finding.note, finding.path, finding.message)
        for finding in lint(vault, schema, ruleset)
        if finding.severity is Severity.ERROR
    )


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    root: Path = args.vault
    try:
        ruleset = load_ruleset(root)
        schema = load_schema(ruleset.schema_version)
        vault = load_vault(root, merge_extensions(schema, ruleset).link_properties)
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    problems = _lint_errors(vault, schema, ruleset)
    if problems:
        _report_problems(problems, as_json=as_json)
        return int(ExitCode.FINDINGS)
    graph = compile_graph(vault, schema, ruleset)
    text = graph.to_json()
    target = root / DEFAULT_GRAPH_PATH if args.out is None else Path(args.out)
    shown = _shown(target, root)
    if args.check:
        current = target.read_bytes() if target.is_file() else None
        up_to_date = current == text.encode("utf-8")
        _report(graph, shown, as_json=as_json, written=False, up_to_date=up_to_date)
        return int(ExitCode.OK if up_to_date else ExitCode.FINDINGS)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    _report(graph, shown, as_json=as_json, written=True, up_to_date=True)
    return int(ExitCode.OK)


def _shown(target: Path, root: Path) -> str:
    """Name the graph file without an absolute path: vault-relative when inside, else its name."""
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return target.name


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
