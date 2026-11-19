"""Check a loaded vault against schema v1 and the ruleset's extensions.

Lint reads front matter and span files. It reads prose only for the two body checks,
`quotation-long` and `body-link`.
"""

from collections.abc import Mapping
from types import MappingProxyType

from sabot_cli.rules._checks import CLASSES, Finding, LintContext, Severity
from sabot_cli.rules.ruleset import RULESET_FILE, Ruleset, RulesetError
from sabot_cli.rules.schema import Schema, merge_extensions
from sabot_cli.vault import Vault

__all__ = [
    "LINT_CLASSES",
    "Finding",
    "Severity",
    "effective_severity",
    "lint",
    "ruleset_load_failure",
]

LINT_CLASSES: Mapping[str, Severity] = MappingProxyType(
    {lint_class.name: lint_class.severity for lint_class in CLASSES}
)

_RULESET_INVALID = "ruleset-invalid"


def lint(
    vault: Vault, schema: Schema, ruleset: Ruleset, strict: bool = False
) -> tuple[Finding, ...]:
    """Every finding for a vault, sorted by (path, class, message).

    `schema` is the fixed schema; the ruleset's extensions are merged here. When they cannot
    be merged only the checks that need no merged schema run, because a type or attribute the
    ruleset failed to declare would otherwise be reported on every note that uses it. With
    `strict`, the classes marked as build-readiness warnings are returned as errors.
    """
    try:
        merged = merge_extensions(schema, ruleset)
    except RulesetError:
        merged = schema
        extensions_merged = False
    else:
        extensions_merged = True
    context = LintContext(vault, schema, merged, ruleset)
    findings: set[Finding] = set()
    for lint_class in CLASSES:
        if lint_class.needs_extensions and not extensions_merged:
            continue
        severity = effective_severity(lint_class.severity, strict)
        findings.update(
            Finding(lint_class.name, severity, report.note, report.path, report.message)
            for report in lint_class.check(context)
        )
    return tuple(sorted(findings, key=lambda f: (f.path, f.code, f.message)))


def ruleset_load_failure(error: RulesetError) -> Finding:
    """The finding for a ruleset or schema version that could not be loaded at all."""
    return Finding(_RULESET_INVALID, Severity.ERROR, None, RULESET_FILE, error.message)


def effective_severity(severity: Severity, strict: bool) -> Severity:
    """The severity a class has in a run: `WARNING_STRICT` is an error under `--strict`."""
    if severity is Severity.WARNING_STRICT:
        return Severity.ERROR if strict else Severity.WARNING
    return severity
