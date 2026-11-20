"""Apply schema v1 to a loaded vault: the schema, the ruleset file and the compiled graph.

The names exported here are the ones the commands use. The package imports `sources` and
`vault` and nothing above them: not `commands`, `cli` or `output`.
"""

from sabot_cli.rules.coverage import CoverageReport, SourceCoverage, coverage
from sabot_cli.rules.graph import BuildProblem, Graph, compile_graph
from sabot_cli.rules.lint import (
    LINT_CLASSES,
    Finding,
    Severity,
    effective_severity,
    lint,
    ruleset_load_failure,
)
from sabot_cli.rules.ruleset import (
    RULESET_FILE,
    Ruleset,
    RulesetError,
    RulesetSource,
    load_ruleset,
    ruleset_with_source,
)
from sabot_cli.rules.schema import (
    Attribute,
    EdgeType,
    NodeType,
    Requirement,
    Schema,
    load_schema,
    merge_extensions,
)

__all__ = [
    "LINT_CLASSES",
    "RULESET_FILE",
    "Attribute",
    "BuildProblem",
    "CoverageReport",
    "EdgeType",
    "Finding",
    "Graph",
    "NodeType",
    "Requirement",
    "Ruleset",
    "RulesetError",
    "RulesetSource",
    "Schema",
    "Severity",
    "SourceCoverage",
    "compile_graph",
    "coverage",
    "effective_severity",
    "lint",
    "load_ruleset",
    "load_schema",
    "merge_extensions",
    "ruleset_load_failure",
    "ruleset_with_source",
]
