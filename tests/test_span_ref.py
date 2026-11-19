"""The span-reference grammar, which `parse_span_ref` is the only parser of."""

import pytest

from sabot_cli.sources import SpanRef, SpanRefError, parse_span_ref


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("dw1#10.1.2.2", SpanRef("dw1", "10.1.2.2"), id="dotted"),
        pytest.param("dw1#103.8a", SpanRef("dw1", "103.8a"), id="lettered"),
        pytest.param(
            "rrop-v11#draw-high/rules-of-draw-high/2",
            SpanRef("rrop-v11", "draw-high/rules-of-draw-high/2"),
            id="slug-path",
        ),
        pytest.param("dw1#s-3f9a01c2", SpanRef("dw1", "s-3f9a01c2"), id="sentence-mode-hash"),
        pytest.param("dw1#1.3/s2", SpanRef("dw1", "1.3", sentence=2), id="one-sentence"),
        pytest.param("dw2#6/*", SpanRef("dw2", "6", subtree=True), id="subtree"),
    ],
)
def test_parse_span_ref_reads_each_form_of_the_grammar(text: str, expected: SpanRef) -> None:
    # Given a reference written in one form of the grammar
    # When it is parsed
    parsed = parse_span_ref(text)

    # Then its parts are separated, and `span` is the form a span file spells
    assert parsed == expected
    assert parsed.span == f"{expected.source}#{expected.locator}"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("dw1", id="no-locator-separator"),
        pytest.param("#1.1", id="no-source"),
        pytest.param("DW1#1.1", id="uppercase-source"),
        pytest.param("dw1#", id="empty-locator"),
        pytest.param("dw1#1.1/", id="trailing-slash"),
        pytest.param("dw1#1..1", id="empty-dotted-part"),
        pytest.param("dw1#1.1/*/s1", id="suffix-not-last"),
        pytest.param("dw1#a b", id="space"),
    ],
)
def test_parse_span_ref_refuses_text_outside_the_grammar(text: str) -> None:
    # Given text that is not a span reference
    # When it is parsed
    # Then it is refused
    with pytest.raises(SpanRefError):
        parse_span_ref(text)
