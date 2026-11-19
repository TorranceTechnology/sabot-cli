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


type _Loader = type[yaml.SafeLoader] | type[yaml.CSafeLoader]


def _narrow_resolution(loader: _Loader) -> None:
    # The resolver table is a class attribute shared with the parent; assigning a filtered copy
    # keeps the registrations below from leaking into every other safe load in the process.
    loader.yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in resolvers if tag not in _REPLACED_TAGS]
        for first, resolvers in loader.yaml_implicit_resolvers.items()
    }
    loader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
    loader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
    loader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+0123456789."))


class PureSafeStringLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` with the narrower scalar resolution described in the module."""


_narrow_resolution(PureSafeStringLoader)


def _c_loader() -> type[yaml.CSafeLoader] | None:
    # PyYAML defines `CSafeLoader` only when it was built against libyaml; a wheel for a
    # platform without one, or a source build, has the pure-Python loader alone.
    if not yaml.__with_libyaml__:
        return None

    class CSafeStringLoader(yaml.CSafeLoader):
        """`yaml.CSafeLoader` with the same scalar resolution as the pure-Python loader."""

    _narrow_resolution(CSafeStringLoader)
    return CSafeStringLoader


# The C loader is faster on a large vault and resolves scalars through the same
# table, so the two are interchangeable; a test holds them to identical results.
C_SAFE_STRING_LOADER = _c_loader()
SafeStringLoader: _Loader = C_SAFE_STRING_LOADER or PureSafeStringLoader


def load(text: str) -> object:
    """Parse one YAML document with `SafeStringLoader`."""
    return yaml.load(text, Loader=SafeStringLoader)
