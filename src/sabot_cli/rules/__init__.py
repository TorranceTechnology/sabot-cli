"""Apply schema v1 to a loaded vault: the schema, the ruleset file and the compiled graph."""

from sabot_cli.rules.graph import Graph, compile_graph
from sabot_cli.rules.ruleset import Ruleset, RulesetError, load_ruleset, register_source
from sabot_cli.rules.schema import Schema, load_schema, merge_extensions, ruleset_problems

__all__ = [
    "Graph",
    "Ruleset",
    "RulesetError",
    "Schema",
    "compile_graph",
    "load_ruleset",
    "load_schema",
    "merge_extensions",
    "register_source",
    "ruleset_problems",
]
