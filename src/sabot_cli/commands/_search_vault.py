"""Loading a vault for the read-only commands `search` and `traverse`."""

from pathlib import Path

from sabot_cli import output
from sabot_cli.commands._load import load_rules
from sabot_cli.search import EdgeSpec
from sabot_cli.vault import Vault, load_vault


def load_searchable(root: Path) -> tuple[Vault, EdgeSpec]:
    """Read every note and the edges the vault's schema defines, extensions included.

    Raises `RulesetError` when the ruleset cannot be loaded. A note that cannot be read is
    left out of the vault and named on stderr, so the command still answers from the rest.
    """
    rules = load_rules(root)
    edges = EdgeSpec(
        names=frozenset(rules.schema.edge_types),
        inverse_of={name: edge.inverse for name, edge in rules.schema.edge_types.items()},
    )
    # Only the names an author writes are read as links; an authored inverse is lint's to report.
    vault = load_vault(root, edges.names)
    for problem in vault.problems:
        output.write_diagnostic(f"skipped {problem.path.as_posix()}: {problem.message}")
    return vault, edges


def known_edge_names(edges: EdgeSpec) -> frozenset[str]:
    """Every name an edge goes by, in either direction."""
    return edges.names | frozenset(edges.inverse_of.values())
