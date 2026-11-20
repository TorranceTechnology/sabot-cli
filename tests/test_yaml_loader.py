"""The vault's YAML loader types scalars narrowly, and its C and pure-Python bases agree."""

import pytest
import yaml

from sabot_cli.testing import C_SAFE_STRING_LOADER, PureSafeStringLoader

_CASES: dict[str, object] = {
    "a: yes": {"a": "yes"},
    "a: No": {"a": "No"},
    "a: on": {"a": "on"},
    "a: OFF": {"a": "OFF"},
    "a: 2026-01-15": {"a": "2026-01-15"},
    "a: 2026-01-15 10:30:00": {"a": "2026-01-15 10:30:00"},
    "a: 1:30": {"a": "1:30"},
    "a: 1_000": {"a": "1_000"},
    "a: 0x1f": {"a": "0x1f"},
    "a: true\nb: False\nc: FALSE": {"a": True, "b": False, "c": False},
    "a: 42\nb: -7\nc: +3\nd: 0": {"a": 42, "b": -7, "c": 3, "d": 0},
    "a: 1.5\nb: -.5\nc: 2.\nd: 1.0e3\ne: 1e3": {
        "a": 1.5,
        "b": -0.5,
        "c": 2.0,
        "d": 1000.0,
        "e": "1e3",
    },
    "a: 007": {"a": "007"},
    "a: ~\nb: null": {"a": None, "b": None},
    "a: [x, 1, true]\nb:\n  - y\n  - 2026-02-01": {"a": ["x", 1, True], "b": ["y", "2026-02-01"]},
    "a: \"yes\"\nb: '1'": {"a": "yes", "b": "1"},
}


def _loaders() -> list[type]:
    loaders: list[type] = [PureSafeStringLoader]
    if C_SAFE_STRING_LOADER is not None:
        loaders.append(C_SAFE_STRING_LOADER)
    return loaders


@pytest.mark.parametrize("loader", _loaders(), ids=lambda loader: loader.__name__)
@pytest.mark.parametrize("text", list(_CASES))
def test_loader_keeps_ambiguous_scalars_as_text(loader: type, text: str) -> None:
    # Given a YAML document holding values an editor may write as booleans, dates or base-60
    # When it is parsed with either loader base
    loaded = yaml.load(text, Loader=loader)

    # Then only true/false, plain decimal integers and plain decimal floats are typed
    assert loaded == _CASES[text]
    assert {type(value) for value in loaded.values()} == {type(v) for v in _CASES[text].values()}


def test_c_and_pure_loaders_give_identical_results() -> None:
    # Given every case and a libyaml-backed PyYAML (otherwise there is nothing to compare)
    if C_SAFE_STRING_LOADER is None:
        pytest.skip("PyYAML was built without libyaml")

    # When each document is parsed with both bases
    for text in _CASES:
        pure = yaml.load(text, Loader=PureSafeStringLoader)
        native = yaml.load(text, Loader=C_SAFE_STRING_LOADER)

        # Then the results and the types of their values are equal
        assert pure == native
        assert repr(pure) == repr(native)


def test_c_and_pure_loaders_both_refuse_malformed_documents() -> None:
    # Given a document that is not valid YAML
    if C_SAFE_STRING_LOADER is None:
        pytest.skip("PyYAML was built without libyaml")

    # When each loader parses it, then each raises a YAML error the callers catch
    for loader in (PureSafeStringLoader, C_SAFE_STRING_LOADER):
        with pytest.raises(yaml.YAMLError):
            yaml.load("a: [unclosed", Loader=loader)


_DUPLICATE_KEYS = [
    "a: 1\na: 2",
    "a: 1\nb: 2\na: 1",
    "a:\n  b: 1\n  b: 2",
    "a: {b: 1, b: 2}",
    "a:\n  - {x: 1, x: 1}",
    "a: 1\n'a': 2",
]


@pytest.mark.parametrize("loader", _loaders(), ids=lambda loader: loader.__name__)
@pytest.mark.parametrize("text", _DUPLICATE_KEYS)
def test_loader_refuses_a_mapping_that_names_a_key_twice(loader: type, text: str) -> None:
    # Given a document with a repeated key, at the top, nested or in a flow mapping
    # When it is parsed with either loader base
    # Then the load fails and the message names the key
    with pytest.raises(yaml.YAMLError) as refused:
        yaml.load(text, Loader=loader)
    assert "duplicate key" in str(refused.value)


@pytest.mark.parametrize("loader", _loaders(), ids=lambda loader: loader.__name__)
def test_loader_still_accepts_the_same_key_in_different_mappings(loader: type) -> None:
    # Given the same key in two sibling mappings, which is not a repeat
    # When the document is parsed
    loaded = yaml.load("a: {x: 1}\nb: {x: 2}\nc:\n  - {x: 3}\n  - {x: 4}", Loader=loader)

    # Then it loads
    assert loaded == {"a": {"x": 1}, "b": {"x": 2}, "c": [{"x": 3}, {"x": 4}]}
