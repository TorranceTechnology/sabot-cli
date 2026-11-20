"""The lint checks, one function per class, and the table that names each class's severity."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sabot_cli.rules._checks._body import _check_body_link, _check_quotation_long
from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._edges import (
    _check_conflict_without_question,
    _check_edge_domain,
    _check_edge_range,
    _check_inverse_authored,
    _check_link_dangling,
    _check_link_malformed,
    _check_overrides_and_exception,
    _check_precedes_cycle,
    _check_restates_conflicts_pair,
    _check_symmetric_duplicated,
)
from sabot_cli.rules._checks._lifecycle import (
    _check_orphan,
    _check_pending,
    _check_question_open,
    _check_source_superseded,
    _check_stale,
)
from sabot_cli.rules._checks._model import Finding, Report, Severity
from sabot_cli.rules._checks._requirements import (
    _check_status_requirement,
    _check_superseded_without_successor,
    _check_type_requirement,
)
from sabot_cli.rules._checks._ruleset import _check_ruleset_invalid
from sabot_cli.rules._checks._spans import _check_span_subtree_misused, _check_span_unknown
from sabot_cli.rules._checks._structure import (
    _check_frontmatter_invalid,
    _check_id_duplicate,
    _check_id_file_mismatch,
    _check_id_malformed,
    _check_id_prefix_mismatch,
    _check_required_missing,
    _check_schema_version_mismatch,
    _check_type_unknown,
)
from sabot_cli.rules._checks._transaction import _check_transaction_incomplete
from sabot_cli.rules._checks._values import _check_attribute_unknown, _check_enum_invalid

type Check = Callable[[LintContext], Iterable[Report]]


@dataclass(frozen=True)
class LintClass:
    """A lint class: its public name, its severity and the check that finds it.

    `needs_extensions` is False for the checks that stay meaningful when the ruleset's
    extensions cannot be merged into the schema.
    """

    name: str
    severity: Severity
    check: Check
    needs_extensions: bool = True


_ERROR = Severity.ERROR
_WARNING = Severity.WARNING
_STRICT = Severity.WARNING_STRICT

# The order is the order of the table of lint classes; findings are sorted afterwards.
CLASSES: tuple[LintClass, ...] = (
    LintClass("frontmatter-invalid", _ERROR, _check_frontmatter_invalid, needs_extensions=False),
    LintClass("required-missing", _ERROR, _check_required_missing),
    LintClass("id-malformed", _ERROR, _check_id_malformed),
    LintClass("id-file-mismatch", _ERROR, _check_id_file_mismatch),
    LintClass("id-duplicate", _ERROR, _check_id_duplicate, needs_extensions=False),
    LintClass("id-prefix-mismatch", _ERROR, _check_id_prefix_mismatch),
    LintClass("type-unknown", _ERROR, _check_type_unknown),
    LintClass("enum-invalid", _ERROR, _check_enum_invalid),
    LintClass("attribute-unknown", _ERROR, _check_attribute_unknown),
    LintClass("status-requirement", _ERROR, _check_status_requirement),
    LintClass("type-requirement", _ERROR, _check_type_requirement),
    LintClass("span-unknown", _ERROR, _check_span_unknown),
    LintClass("span-subtree-misused", _ERROR, _check_span_subtree_misused),
    LintClass("link-malformed", _ERROR, _check_link_malformed),
    LintClass("link-dangling", _ERROR, _check_link_dangling),
    LintClass("edge-domain", _ERROR, _check_edge_domain),
    LintClass("edge-range", _ERROR, _check_edge_range),
    LintClass("inverse-authored", _ERROR, _check_inverse_authored),
    LintClass("symmetric-duplicated", _ERROR, _check_symmetric_duplicated),
    LintClass("precedes-cycle", _ERROR, _check_precedes_cycle),
    LintClass("conflict-without-question", _ERROR, _check_conflict_without_question),
    LintClass("restates-conflicts-pair", _ERROR, _check_restates_conflicts_pair),
    LintClass("superseded-without-successor", _ERROR, _check_superseded_without_successor),
    LintClass("schema-version-mismatch", _ERROR, _check_schema_version_mismatch),
    LintClass("ruleset-invalid", _ERROR, _check_ruleset_invalid, needs_extensions=False),
    LintClass(
        "transaction-incomplete", _ERROR, _check_transaction_incomplete, needs_extensions=False
    ),
    LintClass("quotation-long", _ERROR, _check_quotation_long),
    LintClass("overrides-and-exception", _WARNING, _check_overrides_and_exception),
    LintClass("body-link", _WARNING, _check_body_link),
    LintClass("pending", _STRICT, _check_pending),
    LintClass("stale", _STRICT, _check_stale),
    LintClass("question-open", _STRICT, _check_question_open),
    LintClass("orphan", _STRICT, _check_orphan),
    LintClass("source-superseded", _STRICT, _check_source_superseded),
)

__all__ = ["CLASSES", "Check", "Finding", "LintClass", "LintContext", "Report", "Severity"]
