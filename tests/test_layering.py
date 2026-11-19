"""PKG-3: the import direction that keeps `vault` and `search` usable as a standalone tool."""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "sabot_cli"

# Each lower layer may not import these sibling packages (or the CLI that sits above them).
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "vault": ("rules", "sources", "search", "commands", "cli"),
    "search": ("rules", "sources", "commands", "cli"),
}


def _package_of(directory: Path, root: Path) -> list[str]:
    return [root.name, *directory.relative_to(root).parts]


def _imported_names(source: Path, root: Path) -> set[str]:
    """Dotted names a module imports, with relative imports resolved against its package."""
    package = _package_of(source.parent, root)
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = [*base, *(node.module.split(".") if node.module else [])]
            names.add(".".join(module))
            # `from sabot_cli import rules` imports a submodule through the package name.
            names.update(".".join([*module, alias.name]) for alias in node.names)
    return names


def violations(root: Path, layer: str, forbidden: tuple[str, ...]) -> list[str]:
    """Return `file: imported-name` for each forbidden import in a layer; empty if it is absent."""
    layer_dir = root / layer
    if not layer_dir.is_dir():
        # The layers land after the scaffold; absence passes so this test bites on arrival.
        return []
    found: list[str] = []
    for source in sorted(layer_dir.rglob("*.py")):
        for name in sorted(_imported_names(source, root)):
            for target in forbidden:
                prefix = f"{root.name}.{target}"
                if name == prefix or name.startswith(prefix + "."):
                    found.append(f"{source.relative_to(root).as_posix()}: {name}")
    return found


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_vault_and_search_do_not_import_rules_or_sources(layer: str) -> None:
    # Given the layer's source files
    # When their imports are read
    found = violations(PACKAGE_ROOT, layer, FORBIDDEN_IMPORTS[layer])

    # Then none reaches a package above it
    assert found == [], f"{layer} imports upward: {found}"


@pytest.mark.parametrize(
    "statement",
    [
        "from sabot_cli.rules import lint",
        "from sabot_cli import sources",
        "import sabot_cli.rules.schema",
        "from ..rules import schema",
        "from .. import sources",
    ],
)
def test_the_check_flags_an_upward_import(statement: str, tmp_path: Path) -> None:
    # Given a package whose `vault` layer holds one upward import
    root = tmp_path / "sabot_cli"
    (root / "vault").mkdir(parents=True)
    (root / "vault" / "leak.py").write_text(statement + "\n", encoding="utf-8")

    # When the layer is checked
    found = violations(root, "vault", FORBIDDEN_IMPORTS["vault"])

    # Then the import is reported
    assert found, f"{statement!r} was not flagged"
