"""PKG-3: the import direction that keeps each layer usable without the ones above it."""

import ast
import importlib
from pathlib import Path

import pytest

from sabot_cli.rules import LINT_CLASSES

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "sabot_cli"

# Each lower layer may not import these sibling packages, or the command line that sits above
# them (`commands`, `cli` and `output`). From the bottom: `vault`; then `sources`, `rules` and
# `search`; then the commands. `rules` reads span files through `sources`.
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "vault": ("rules", "sources", "search", "commands", "cli", "output"),
    "search": ("rules", "sources", "commands", "cli", "output"),
    "sources": ("rules", "search", "commands", "cli", "output"),
    "rules": ("search", "commands", "cli", "output"),
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
    """Return `file: imported-name` for each forbidden import in a layer."""
    layer_dir = root / layer
    assert layer_dir.is_dir(), f"the {layer} layer is missing from {root}"
    found: list[str] = []
    for source in sorted(layer_dir.rglob("*.py")):
        for name in sorted(_imported_names(source, root)):
            for target in forbidden:
                prefix = f"{root.name}.{target}"
                if name == prefix or name.startswith(prefix + "."):
                    found.append(f"{source.relative_to(root).as_posix()}: {name}")
    return found


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_each_layer_imports_only_downward(layer: str) -> None:
    # Given the layer's source files
    # When their imports are read
    found = violations(PACKAGE_ROOT, layer, FORBIDDEN_IMPORTS[layer])

    # Then none reaches a package above it, and none reaches the command line
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


def test_the_vault_package_names_no_lint_class() -> None:
    # Given the lint classes, which are the rules layer's vocabulary
    # When the vault's reader modules are searched for one as a string literal; the transaction
    # module is left out because its refusal code `transaction-incomplete` is also a lint class
    # by design, so that `lint` reports what every other command refuses
    readers = [
        source
        for source in sorted((PACKAGE_ROOT / "vault").rglob("*.py"))
        if source.name != "transaction.py"
    ]
    quoted = [
        f"{source.name}: {name}"
        for source in readers
        for name in LINT_CLASSES
        if f'"{name}"' in source.read_text(encoding="utf-8")
    ]

    # Then none is found, so the vault stays a reader that knows nothing of rules
    assert quoted == []


def _names_imported_from(package: str) -> set[str]:
    imported: set[str] = set()
    for source in [*PACKAGE_ROOT.rglob("*.py"), *Path(__file__).parent.rglob("*.py")]:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == package:
                imported.update(alias.name for alias in node.names)
    return imported


@pytest.mark.parametrize("package", ["sabot_cli.rules", "sabot_cli.sources"])
def test_every_exported_name_is_used_by_a_caller(package: str) -> None:
    # Given the names a package exports
    exported = set(importlib.import_module(package).__all__)

    # When the imports of that package are collected from the source and the tests
    imported = _names_imported_from(package)

    # Then every exported name is imported by name somewhere, so the surface holds no extras
    assert sorted(exported - imported) == []
