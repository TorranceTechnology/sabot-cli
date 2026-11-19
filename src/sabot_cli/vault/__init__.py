"""Read Markdown notes with YAML front matter into typed objects.

This package knows nothing about rules: it does not import `rules`, `sources`, `search` or
`commands`, so it can be split out as a standalone vault reader.
"""

from sabot_cli.vault._links import find_wikilinks, parse_wikilink
from sabot_cli.vault._load import load_vault
from sabot_cli.vault._model import Link, Note, Problem, Vault
from sabot_cli.vault._yaml import load as load_yaml

__all__ = [
    "Link",
    "Note",
    "Problem",
    "Vault",
    "find_wikilinks",
    "load_vault",
    "load_yaml",
    "parse_wikilink",
]
