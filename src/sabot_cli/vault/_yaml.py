"""A YAML loader that leaves the values an author did not mean as other types alone.

Obsidian and hand editing produce `yes`, `on`, `2026-01-15` and `1:30`; the stock safe loader
turns these into booleans, dates and base-60 integers, so a note's meaning would change with
the editor. This loader keeps them as text and recognises only `true`/`false`, plain decimal
integers and plain decimal floats.
"""

import re

import yaml

_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$")
_FLOAT = re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$")
_REPLACED_TAGS = frozenset(
    f"tag:yaml.org,2002:{name}" for name in ("bool", "int", "float", "timestamp")
)


class SafeStringLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` with the narrower scalar resolution described in the module."""


# The resolver table is a class attribute shared with the parent; assigning a filtered copy
# keeps the registrations below from leaking into every other safe load in the process.
SafeStringLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag not in _REPLACED_TAGS]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
SafeStringLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
SafeStringLoader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
SafeStringLoader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+0123456789."))


def load(text: str) -> object:
    """Parse one YAML document with `SafeStringLoader`."""
    return yaml.load(text, Loader=SafeStringLoader)
