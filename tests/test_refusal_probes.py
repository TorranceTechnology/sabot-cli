"""Every refusal code the source can emit is named by at least one test.

A tool that refuses input has a test that plants the refusal. The codes are found by reading
the source: every kebab-case string literal, and every string passed as the code of a refusal.
A literal that is not a refusal code is listed in `NOT_REFUSAL_CODES` with what it is instead.
This is a tripwire for a code added without a probe; it cannot tell that the probe asserts it.
"""

import ast
from pathlib import Path

import pytest

# The error classes whose first argument is a code; the others take a message alone.
CODED_ERRORS = {"RulesetError", "SourceError", "TransactionError"}

SOURCE = Path(__file__).parent.parent / "src" / "sabot_cli"
TESTS = Path(__file__).parent

NOT_REFUSAL_CODES = {
    "any-edge": "a schema domain keyword",
    "any-inbound-edge": "a schema domain keyword",
    "archive-url": "a source note property",
    "construct-kind": "a schema attribute name",
    "construct-kinds": "a schema section name",
    "diff-editions": "a command name",
    "dotted-letter": "a numbering option value",
    "duplicate-id": "a vault problem kind that lint reports as `id-duplicate`",
    "edge-types": "a schema section name",
    "html-stdlib": "an extractor name",
    "migrate-edition": "a command name",
    "node-types": "a schema section name",
    "schema-version": "a ruleset key",
    "stale-editorial": "a migration outcome name",
    "stale-reason": "a note property",
    "stale-removed": "a migration outcome name",
    "stale-substantive": "a migration outcome name",
    "status-requirements": "a schema section name",
    "text-out": "an output row name",
    "type-requirements": "a schema section name",
    "utf-8": "an encoding name",
    "utf-8-sig": "an encoding name",
    "values-by-type": "a schema key",
    "values-from": "a schema key",
    "warning-strict": "a lint severity name",
}


def _is_kebab(value: str) -> bool:
    parts = value.split("-")
    return len(parts) > 1 and all(part.isalnum() and part.islower() for part in parts)


def _call_name(node: ast.Call) -> str:
    function = node.func
    return function.id if isinstance(function, ast.Name) else getattr(function, "attr", "")


def _codes_in_source() -> set[str]:
    codes: set[str] = set()
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _is_kebab(node.value):
                    codes.add(node.value)
            elif isinstance(node, ast.Call):
                name = _call_name(node)
                position = 1 if name == "refuse" else 0 if name in CODED_ERRORS else None
                if position is not None and len(node.args) > position:
                    argument = node.args[position]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        codes.add(argument.value)
    return codes - set(NOT_REFUSAL_CODES)


def _quoted_in_tests() -> str:
    own = Path(__file__).name
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(TESTS.rglob("*.py")) if path.name != own
    )


def test_every_refusal_code_in_the_source_is_named_by_a_test() -> None:
    # Given every refusal code the source can emit
    codes = _codes_in_source()
    assert len(codes) > 40, "the scan found too few codes to be reading the source"

    # When the tests are searched for each, quoted
    text = _quoted_in_tests()
    unprobed = sorted(code for code in codes if f'"{code}"' not in text)

    # Then none is missing
    assert unprobed == [], f"no test names these refusal codes: {unprobed}"


@pytest.mark.parametrize("code", sorted(NOT_REFUSAL_CODES))
def test_a_listed_non_code_still_appears_in_the_source(code: str) -> None:
    # Given a literal listed as not being a refusal code
    appears = any(
        f'"{code}"' in path.read_text(encoding="utf-8") for path in sorted(SOURCE.rglob("*.py"))
    )

    # When the source is searched for it
    # Then it is found, so the list holds no entry the source has dropped
    assert appears, f"{code!r} no longer appears in the source; remove it from the list"
