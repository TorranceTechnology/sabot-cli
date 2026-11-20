"""Read Markdown notes with YAML front matter into typed objects, and write them in transactions.

This package knows nothing about rules: it does not import `rules`, `sources`, `search` or
`commands`, so it can be split out as a standalone vault reader.
"""

from sabot_cli.vault._frontmatter import FrontMatterError
from sabot_cli.vault._frontmatter import parse as parse_front_matter
from sabot_cli.vault._frontmatter import split as split_front_matter
from sabot_cli.vault._links import find_wikilinks, parse_wikilink
from sabot_cli.vault._load import load_vault
from sabot_cli.vault._model import Link, Note, Problem, ProblemKind, Vault
from sabot_cli.vault._yaml import load as load_yaml
from sabot_cli.vault.text import TextFileError, decode_text, encode_text, read_text

__all__ = [
    "FrontMatterError",
    "Link",
    "Note",
    "Problem",
    "ProblemKind",
    "TextFileError",
    "Vault",
    "decode_text",
    "encode_text",
    "find_wikilinks",
    "load_vault",
    "load_yaml",
    "parse_front_matter",
    "parse_wikilink",
    "read_text",
    "split_front_matter",
]
