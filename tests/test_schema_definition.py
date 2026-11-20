"""The packaged schema is checked when it is loaded; these probes plant the refusals."""

import pytest

from sabot_cli.rules import load_schema
from sabot_cli.rules.schema import parse_schema


def _document(**edge_overrides: object) -> dict[str, object]:
    edge = {"domain": ["clause"], "range": ["clause"], "inverse": "refined-by", "note": "n"}
    edge.update(edge_overrides)
    return {
        "schema-version": 1,
        "statuses": ["mapped"],
        "attributes": {"tests": {"type": "list", "note": "n"}},
        "node-types": {"clause": {"prefix": "cl.", "side": "source", "note": "n"}},
        "edge-types": {"refines": edge},
        "construct-kinds": ["none"],
    }


def test_the_packaged_schema_has_no_inverse_named_like_an_attribute() -> None:
    # Given the packaged schema
    schema = load_schema()

    # When its edge, inverse and attribute names are compared
    clashes = {e.name for e in schema.edge_types.values()} | {
        e.inverse for e in schema.edge_types.values()
    }

    # Then none is an attribute name
    assert not clashes & set(schema.attributes)


def test_a_well_formed_document_loads() -> None:
    # Given a minimal document with no clash
    # When it is parsed
    schema = parse_schema(_document())

    # Then it loads
    assert schema.edge_types["refines"].inverse == "refined-by"


def test_an_inverse_named_like_an_attribute_is_refused() -> None:
    # Given a document whose inverse is the name of an attribute
    document = _document(inverse="tests")

    # When it is parsed
    # Then it is refused and the name is given
    with pytest.raises(ValueError, match="tests"):
        parse_schema(document)
