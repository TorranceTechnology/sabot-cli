"""The `lint` command."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._options import Subparsers, add_json_option, add_vault_option
from sabot_cli.exit_codes import ExitCode
from sabot_cli.rules import RulesetError, load_ruleset, load_schema, merge_extensions
from sabot_cli.rules.lint import (
    LINT_CLASSES,
    Finding,
    Severity,
    effective_severity,
    lint,
    ruleset_load_failure,
)
from sabot_cli.vault import load_vault

NAME = "lint"
NOT_A_VAULT = "not-a-vault"


def register(subparsers: Subparsers) -> None:
    """Add the `lint` subparser."""
    parser = subparsers.add_parser(NAME, help="check a vault against schema v1")
    parser.add_argument(
        "--strict", action="store_true", help="treat build-readiness warnings as errors"
    )
    parser.add_argument(
        "--list-classes", action="store_true", help="list every lint class and exit"
    )
    add_vault_option(parser)
    add_json_option(parser)


def run(args: argparse.Namespace) -> int:
    """Execute the command and return its exit code."""
    as_json = bool(args.json)
    if args.list_classes:
        _report_classes(as_json=as_json)
        return int(ExitCode.OK)
    try:
        findings = _findings(args.vault, strict=bool(args.strict))
    except RulesetError as error:
        return output.refuse(NAME, error.code, error.message, as_json=as_json)
    _report(findings, as_json=as_json)
    failed = any(finding.severity is Severity.ERROR for finding in findings)
    return int(ExitCode.FINDINGS if failed else ExitCode.OK)


def _findings(root: Path, *, strict: bool) -> tuple[Finding, ...]:
    """Lint the vault at `root`. A missing `_ruleset.yaml` is refused: the directory is no vault.
    A ruleset that exists but cannot be used is a finding, because that is what an author fixes."""
    try:
        ruleset = load_ruleset(root)
        schema = load_schema(ruleset.schema_version)
    except RulesetError as error:
        if error.code == NOT_A_VAULT:
            raise
        return (ruleset_load_failure(error),)
    try:
        link_properties = merge_extensions(schema, ruleset).link_properties
    except RulesetError:
        # Extensions that cannot be merged still leave the fixed edges readable.
        link_properties = schema.link_properties
    return lint(load_vault(root, link_properties), schema, ruleset, strict=strict)


def _report(findings: Sequence[Finding], *, as_json: bool) -> None:
    errors = sum(1 for finding in findings if finding.severity is Severity.ERROR)
    warnings = len(findings) - errors
    if as_json:
        output.write_json(
            {
                "command": NAME,
                "counts": {"error": errors, "warning": warnings},
                "findings": [
                    {
                        "class": finding.code,
                        "message": finding.message,
                        "note": finding.note,
                        "path": finding.path,
                        "severity": finding.severity.value,
                    }
                    for finding in findings
                ],
                "format": output.FORMAT_VERSION,
            }
        )
        return
    output.write_text(
        "".join(
            f"{finding.severity.value} {finding.code} {finding.note or finding.path} "
            f"{finding.message}\n"
            for finding in findings
        )
    )
    output.write_diagnostic(f"sabot lint: {errors} errors, {warnings} warnings")


def _report_classes(*, as_json: bool) -> None:
    if as_json:
        output.write_json(
            {
                "classes": [
                    {
                        "class": name,
                        "severity": effective_severity(severity, False).value,
                        "strictSeverity": effective_severity(severity, True).value,
                    }
                    for name, severity in sorted(LINT_CLASSES.items())
                ],
                "command": NAME,
                "format": output.FORMAT_VERSION,
            }
        )
        return
    rows = [
        [
            name,
            "warning (error under --strict)"
            if severity is Severity.WARNING_STRICT
            else severity.value,
        ]
        for name, severity in sorted(LINT_CLASSES.items())
    ]
    output.write_text(output.format_table(["class", "severity"], rows))
