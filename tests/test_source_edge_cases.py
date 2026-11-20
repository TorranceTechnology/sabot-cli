"""Edge cases of segmentation, edition diffing and migration that earlier tests missed."""

import json
from pathlib import Path

import pytest

from conftest import CliRunner
from sabot_cli.sources import SpanIndex, SpanRecord, diff_editions
from sabot_cli.vault import load_vault
from source_vaults import (
    segmented_vault,
    snapshot,
    vault_with_sources,
    write_text,
)
from support.vault_state import vault_state

BOM = b"\xef\xbb\xbf"


def _segment(
    run_cli: CliRunner, vault: Path, text: Path, mode: str, *extra: str, key: str = "dw1"
) -> tuple[int, dict[str, object]]:
    result = run_cli(
        "segment", str(text), "--source", key, "--mode", mode, *extra,
        "--vault", str(vault), "--json",
    )  # fmt: skip
    return result.returncode, result.json()


def _spans(vault: Path, key: str = "dw1") -> list[dict[str, object]]:
    path = vault / "spans" / f"{key}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _span_locators(vault: Path, key: str = "dw1") -> list[str]:
    return [str(record["locator"]) for record in _spans(vault, key) if record["kind"] == "span"]


# The source note is read the way the vault reader reads it.
def test_segment_updates_a_source_note_that_starts_with_a_byte_order_mark(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose source note was saved with a byte order mark
    text = write_text(tmp_path / "rules.txt", "1.1 First rule.\n1.2 Second rule.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})
    note = vault / "sources" / "src.dw1.md"
    note.write_bytes(BOM + note.read_bytes())

    # When the text is segmented
    code, document = _segment(run_cli, vault, text, "numbered")

    # Then the span file is written, the note records the segmenter and keeps its mark
    assert code == 0
    assert document["written"] is True
    assert note.read_bytes().startswith(BOM)
    assert load_vault(vault, frozenset()).notes["src.dw1"].properties["segmenter"] == "numbered"


def test_segment_keeps_the_line_endings_of_a_crlf_source_note(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a source note saved with CRLF line endings
    text = write_text(tmp_path / "rules.txt", "1.1 First rule.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})
    note = vault / "sources" / "src.dw1.md"
    note.write_bytes(note.read_bytes().replace(b"\n", b"\r\n"))

    # When the text is segmented
    _segment(run_cli, vault, text, "numbered")

    # Then every line of the note still ends in CRLF
    data = note.read_bytes()
    assert b"segmenter: numbered\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")


# A text source has no page breaks, so no page is a title page.
def test_numbered_mode_keeps_text_that_has_no_page_breaks(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a numbered text with no form feeds
    text = write_text(tmp_path / "plain.txt", "1. First rule.\n2. Second rule.\n3. Third rule.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})

    # When it is segmented with the default options
    code, document = _segment(run_cli, vault, text, "numbered")

    # Then every rule is a span
    assert code == 0
    assert document["spans"] == 3


def test_skip_pages_applies_only_to_text_with_page_breaks(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a text of two pages and one of a single page, each numbered
    paged = write_text(tmp_path / "paged.txt", "1. Title page rule.\n\f2. Second page rule.\n")
    plain = write_text(tmp_path / "plain.txt", "1. First rule.\n2. Second rule.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": paged, "dw2": plain})

    # When each is segmented asking to skip one page
    _, with_pages = _segment(run_cli, vault, paged, "numbered", "--skip-pages", "1")
    _, without_pages = _segment(run_cli, vault, plain, "numbered", "--skip-pages", "1", key="dw2")

    # Then the page break is honoured and the unpaginated text is not discarded
    assert with_pages["spans"] == 1
    assert without_pages["spans"] == 2


# Every generated locator must be citable.
@pytest.mark.parametrize("heading", ["S2", "日本"], ids=["reads-as-sentence", "no-ascii"])
def test_headings_mode_refuses_a_heading_that_cannot_be_cited(
    run_cli: CliRunner, tmp_path: Path, heading: str
) -> None:
    # Given a heading whose slug is not a valid locator
    text = write_text(tmp_path / "doc.md.txt", f"# {heading}\nSome prose.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})
    before = snapshot(vault)

    # When it is segmented in headings mode
    code, document = _segment(run_cli, vault, text, "headings")

    # Then the locator-invalid check fails and nothing is written
    checks = {check["name"]: check for check in document["checks"]}  # type: ignore[attr-defined, union-attr]
    assert code == 1
    assert checks["locator-invalid"]["status"] == "failed"
    assert snapshot(vault) == before


def test_heading_slugs_drop_accents_instead_of_splitting_the_word(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a heading with an accented letter
    text = write_text(tmp_path / "doc.md.txt", "# Über Rules\nSome prose.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})

    # When it is segmented in headings mode
    code, _ = _segment(run_cli, vault, text, "headings")

    # Then the slug is the unaccented word
    assert code == 0
    assert _span_locators(vault) == ["uber-rules"]


def test_sentences_mode_numbers_a_sentence_that_repeats(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a text in which one sentence appears twice
    text = write_text(
        tmp_path / "doc.txt", "Shuffle the deck. Deal five cards. Shuffle the deck.\n"
    )
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})

    # When it is segmented in sentences mode
    code, document = _segment(run_cli, vault, text, "sentences")

    # Then every sentence has its own locator, the repeat counting up from the first
    locators = _span_locators(vault)
    assert code == 0
    assert document["spans"] == 3
    assert len(set(locators)) == 3
    assert locators[2] == f"{locators[0]}-2"


# A wrapped line that begins with a decimal is not a rule.
def test_a_wrapped_decimal_is_not_a_rule_number(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a rule whose second line begins "2.5 times", with the first line cut mid-sentence
    text = write_text(
        tmp_path / "rules.txt",
        "1.1 Each boat carries a load worth a bonus of\n2.5 times its weight.\n1.2 Next rule.\n",
    )
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": text})

    # When it is segmented
    code, _ = _segment(run_cli, vault, text, "numbered")

    # Then the wrapped line joins rule 1.1 and starts no span of its own
    assert code == 0
    assert _span_locators(vault) == ["1.1", "1.2"]


# A sentence citation survives only when the same sentence is still there.
def test_a_sentence_citation_moves_only_to_the_same_sentence(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given two editions whose rule 4.1 differs by punctuation, which moves a sentence boundary
    first = write_text(tmp_path / "e1.txt", "4.1 Draw a card. Then discard one. Play it now.\n")
    second = write_text(tmp_path / "e2.txt", "4.1 Draw a card, then discard one. Play it now.\n")
    vault = segmented_vault(run_cli, tmp_path / "vault", {"dw1": first, "dw2": second})
    write_text(
        vault / "clauses" / "cl.second-step.md",
        "---\nid: cl.second-step\ntype: clause\ntitle: Second step\nstatus: mapped\n"
        'source:\n  - "dw1#4.1/s2"\n---\n\nParaphrase.\n',
    )

    # When the vault is migrated to the second edition
    result = run_cli("migrate-edition", "dw1", "dw2", "--vault", str(vault), "--json")

    # Then the citation is not moved onto a different sentence: the note is stale, unchanged
    outcomes = result.json()["outcomes"]
    assert result.returncode == 0
    assert outcomes["stale-substantive"] == ["cl.second-step"]  # type: ignore[index]
    note = load_vault(vault, frozenset()).notes["cl.second-step"]
    assert note.properties["source"] == ["dw1#4.1/s2"]


# Spans with no words are never the same span moved.
def _record(locator: str, words: int, sha: str, position: int) -> SpanRecord:
    return SpanRecord(
        span=f"s#{locator}",
        source="s",
        locator=locator,
        kind="span",
        parent=None,
        page=1,
        offset=position,
        words=words,
        sentences=1,
        position=position,
        sha256=sha,
        editorial_sha256=sha,
    )


def test_two_spans_with_no_words_are_not_paired_as_a_move() -> None:
    # Given an old edition with a parent holding only its children and a new edition whose
    # parent of another name also holds no text of its own
    empty = "e3b0c44298fc1c14"
    old = SpanIndex((_record("danger", 0, empty, 0), _record("rest", 3, "aaaa", 1)))
    new = SpanIndex((_record("scoring", 0, empty, 0), _record("rest", 3, "aaaa", 1)))

    # When the editions are diffed
    diff = diff_editions(old, new)

    # Then the empty parents are a removal and an addition, not a move
    assert diff.moved == ()
    assert diff.removed == ("danger",)
    assert diff.added == ("scoring",)


# A boundaries file may skip text between two spans.
def test_diff_with_texts_reads_a_boundaries_edition_that_skips_text_between_spans(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given two editions segmented from boundaries files that skip a line between two leaves
    first_text = "Alpha rule text.\nPAGE NOISE\nBeta rule text.\n"
    second_text = "Alpha rule text.\nPAGE NOISE\nBeta rule text changed.\n"
    sources = {}
    for key, body in (("dw1", first_text), ("dw2", second_text)):
        sources[key] = write_text(tmp_path / f"{key}.txt", body)
    vault = vault_with_sources(run_cli, tmp_path / "vault", sources)
    for key, body in (("dw1", first_text), ("dw2", second_text)):
        alpha_end = body.index("\n")
        noise_start, beta_start = alpha_end + 1, body.index("Beta")
        records = [
            {"locator": "alpha", "parent": None, "start": 0, "end": alpha_end, "page": 1},
            {"skip": True, "reason": "noise", "start": noise_start, "end": beta_start - 1},
            {
                "locator": "beta", "parent": None, "start": beta_start,
                "end": len(body.rstrip("\n")), "page": 1,
            },
        ]  # fmt: skip
        boundaries = write_text(
            tmp_path / f"{key}.boundaries.jsonl",
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        )
        code, _ = _segment_boundaries(run_cli, vault, sources[key], boundaries, key)
        assert code == 0

    # When they are diffed with the extracted texts
    result = run_cli(
        "diff-editions", "dw1", "dw2", "--old-text", str(sources["dw1"]),
        "--new-text", str(sources["dw2"]), "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then the diff is made rather than refused for a text mismatch
    assert result.returncode == 0, result.stdout
    assert result.json()["classes"]["substantive"] == ["beta"]  # type: ignore[index]


def _segment_boundaries(
    run_cli: CliRunner, vault: Path, text: Path, boundaries: Path, key: str
) -> tuple[int, dict[str, object]]:
    return _segment(run_cli, vault, text, "boundaries", "--boundaries", str(boundaries), key=key)


# A note whose front matter does not parse is reported, not silently skipped.
def test_migrate_reports_notes_it_could_not_read(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a note whose front matter is not YAML
    broken = driftwood_vault / "clauses" / "cl.broken.md"
    write_text(broken, "---\nid: [unclosed\n---\n\nBody.\n")

    # When the vault is migrated
    result = run_cli(
        "migrate-edition", "dw1", "dw2", "--dry-run", "--vault", str(driftwood_vault), "--json"
    )

    # Then the note is named under `unparsed`, and the run still reports its other outcomes
    assert result.returncode == 0
    assert result.json()["unparsed"] == ["clauses/cl.broken.md"]
    assert "error" not in result.json()


# A comment in the citation block is not a citation.
def test_migrate_leaves_a_reference_in_a_yaml_comment_alone(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note whose source block carries a comment naming a citation
    note = next(driftwood_vault.rglob("cl.formats.md"))
    text = note.read_text(encoding="utf-8")
    write_text(note, text.replace('  - "dw1#7.1"\n', '  - "dw1#7.1" # was dw1#7.1 in my notes\n'))

    # When it is migrated
    run_cli("migrate-edition", "dw1", "dw2", "--vault", str(driftwood_vault))

    # Then the citation moved and the comment still reads as written
    rewritten = note.read_text(encoding="utf-8")
    assert '  - "dw2#7.1" # was dw1#7.1 in my notes\n' in rewritten


def _segment_locators_of(
    run_cli: CliRunner, tmp_path: Path, text: str, mode: str = "numbered", *extra: str
) -> tuple[int, list[str]]:
    source = write_text(tmp_path / "rules.txt", text)
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})
    code, _ = _segment(run_cli, vault, source, mode, *extra)
    return code, _span_locators(vault) if (vault / "spans" / "dw1.jsonl").exists() else []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "1. Setup\n1.1 Each player takes five cards.\n\nCombat\n\n"
            "6.1 d6 rolls decide each attack.\n6.2 The attacker moves first.\n",
            ["1", "1.1", "6.1", "6.2"],
            id="after-blank-lines",
        ),
        pytest.param(
            "2. Setup\n2.1 the dealer shuffles the deck.\n2.2 The dealer deals.\n",
            ["2", "2.1", "2.2"],
            id="first-child-of-open-rule",
        ),
        pytest.param(
            "4.1 Boats carry cargo\n4.2 mooring-cleat sizes follow harbour law.\n",
            ["4.1", "4.2"],
            id="next-sibling-of-open-rule",
        ),
        pytest.param(
            "7.1 Players may rest\n7.2 mAh-rated lanterns are allowed.\n7.3 Lanterns burn oil.\n",
            ["7.1", "7.2", "7.3"],
            id="next-sibling-lowercase-start",
        ),
        pytest.param(
            "4.2 Cargo is stowed in order\n4.3 the last crate goes first.\n",
            ["4.2", "4.3"],
            id="next-sibling-after-no-sentence-end",
        ),
        pytest.param(
            "5.3 Cargo is stowed in order\n5.3.1 the last crate goes first.\n",
            ["5.3", "5.3.1"],
            id="first-child-decimal",
        ),
        pytest.param(
            "4.1 Boats carry cargo.\n4.1.1 Heavy cargo rides low.\n4.2 the tide decides.\n",
            ["4.1", "4.1.1", "4.2"],
            id="next-sibling-of-an-ancestor",
        ),
        pytest.param(
            "Harbour rules overview\n1.1 first rule applies here.\n1.2 Second rule.\n",
            ["1.1", "1.2"],
            id="after-an-unnumbered-first-line",
        ),
        pytest.param(
            "1.1 Each boat carries a load worth a bonus of\n\n3.5 times its weight.\n3.6 Next.\n",
            ["1.1", "3.5", "3.6"],
            id="a-blank-line-ends-a-sentence-of-rules",
        ),
    ],
)
def test_a_numbered_line_is_a_rule_unless_it_continues_the_line_before(
    text: str, expected: list[str], run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given rules where a line begins with a number that the list reaches next, follows a
    # blank line or opens the text, though it reads like a decimal inside a sentence
    # When the text is segmented in numbered mode
    code, locators = _segment_locators_of(run_cli, tmp_path, text, "numbered")

    # Then every numbered line is a span of its own
    assert code == 0
    assert locators == expected


def test_a_numbered_line_that_is_a_rule_under_a_heading_is_not_merged(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a heading with numbered rules under it, the second reading like a wrapped decimal
    text = "# Hand\n4.1 a limit of cards applies.\n4.2 the limit counts held cards.\n"

    # When the text is segmented in headings mode
    code, locators = _segment_locators_of(run_cli, tmp_path, text, "headings")

    # Then both rules are spans beneath the heading
    assert code == 0
    assert locators == ["hand", "hand/4.1", "hand/4.2"]


def test_a_wrapped_decimal_across_a_page_number_is_still_one_rule(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a rule that breaks across a page, with a page number between its two halves
    text = (
        "1.1 Each boat carries a load worth a bonus of\n- 3 -\n2.5 times its weight.\n1.2 Next.\n"
    )

    # When it is segmented
    code, locators = _segment_locators_of(run_cli, tmp_path, text, "numbered")

    # Then the page number does not separate them, so the decimal stays inside rule 1.1
    assert code == 0
    assert locators == ["1.1", "1.2"]


@pytest.mark.parametrize("mode", ["numbered", "headings", "sentences"])
def test_every_mode_reports_the_numbered_continuations_check(
    mode: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a text of numbered rules under a heading, none of which wraps onto another
    source = write_text(
        tmp_path / "rules.txt", "# Hand\n4.1 A limit of cards applies.\n4.2 It counts.\n"
    )
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})

    # When it is segmented in a mode
    code, document = _segment(run_cli, vault, source, mode)

    # Then the check is listed and passed, and no continuation is reported
    checks = {check["name"]: check for check in document["checks"]}  # type: ignore[attr-defined, union-attr]
    assert code == 0
    assert checks["numbered-continuations"] == {
        "failures": [],
        "name": "numbered-continuations",
        "status": "passed",
    }
    assert document["continuations"] == []
    assert "rule-text-unspanned" not in checks


@pytest.mark.parametrize(
    ("text", "numbering", "spans", "continuations"),
    [
        pytest.param(
            "2. Damage\n2.4 The attacker deals damage multiplied by\n"
            "2.5 times the defense of the target.\n3. Next\n3.1 Later.\n",
            "dotted",
            ["2", "2.4", "2.5", "3", "3.1"],
            [],
            id="a wrapped decimal that is the next sibling is split as a rule",
        ),
        pytest.param(
            "3. Setup\n3.1 shuffle the deck\n3.3 deal five cards to each player.\n4. Play\n",
            "dotted",
            ["3", "3.1", "4"],
            [{"line": 3, "number": "3.3", "span": "3.1"}],
            id="a skipped number is read as a continuation",
        ),
        pytest.param(
            "4. Combat\n4.2 Blocking\n4.2a the attacker picks\n4.2c the defender may block.\n",
            "dotted-letter",
            ["4", "4.2", "4.2a"],
            [{"line": 4, "number": "4.2c", "span": "4.2a"}],
            id="a skipped letter is read as a continuation",
        ),
        pytest.param(
            "12. End\n12.4 the game ends when the last card is played.\n",
            "dotted",
            ["12"],
            [{"line": 2, "number": "12.4", "span": "12"}],
            id="a first child that is not .1 is read as a continuation",
        ),
        pytest.param(
            "4. Combat\n4.2 Blocking\n4.2c The attacker picks one of\n5. their cards.\n",
            "dotted-letter",
            ["4", "4.2", "4.2c", "5"],
            [],
            id="the next sibling of an ancestor is a rule",
        ),
    ],
)
def test_each_reading_of_a_numbered_line_is_a_span_or_is_listed_as_a_continuation(
    text: str,
    numbering: str,
    spans: list[str],
    continuations: list[dict[str, object]],
    run_cli: CliRunner,
    tmp_path: Path,
) -> None:
    # Given numbered text in which a line begins with a number the heuristic must judge
    source = write_text(tmp_path / "rules.txt", text)
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})

    # When it is segmented
    code, document = _segment(run_cli, vault, source, "numbered", "--numbering", numbering)

    # Then the decision is pinned: the line is a span of its own, or it is listed with its
    # line, its number and the span it joined
    assert code == 0
    assert _span_locators(vault) == spans
    assert document["continuations"] == continuations


def test_a_continuation_is_a_notice_that_lists_it_and_does_not_stop_the_run(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given text in which one numbered line reads as a continuation
    source = write_text(tmp_path / "rules.txt", "3.1 shuffle the deck\n3.3 deal five cards.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})

    # When it is segmented, with text output
    result = run_cli(
        "segment", str(source), "--source", "dw1", "--mode", "numbered", "--vault", str(vault)
    )

    # Then the span file is written, and the output names the line, the number and the span
    assert result.returncode == 0
    assert (vault / "spans" / "dw1.jsonl").is_file()
    assert "check numbered-continuations: notice\n" in result.stdout
    assert "  line 2: 3.3 read as text of 3.1\n" in result.stdout


@pytest.mark.parametrize("mode", ["numbered", "headings"])
def test_strict_numbering_fails_when_a_numbered_line_was_read_as_a_continuation(
    mode: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given text in which one numbered line reads as a continuation (under a heading in the
    # headings mode)
    source = write_text(
        tmp_path / "rules.txt", "# Setup\n3.1 shuffle the deck\n3.3 deal five cards.\n"
    )
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})
    before = vault_state(vault)

    # When it is segmented with --strict-numbering
    code, document = _segment(run_cli, vault, source, mode, "--strict-numbering")

    # Then the check fails, nothing is written and the document says what was read how
    checks = {check["name"]: check for check in document["checks"]}  # type: ignore[attr-defined, union-attr]
    assert code == 1
    assert document["written"] is False
    assert checks["numbered-continuations"]["status"] == "failed"
    assert document["continuations"]
    assert vault_state(vault) == before


def test_strict_numbering_passes_when_no_numbered_line_was_read_as_a_continuation(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given text with no continuation
    source = write_text(tmp_path / "rules.txt", "1.1 First rule.\n1.2 Second rule.\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": source})

    # When it is segmented with --strict-numbering
    code, document = _segment(run_cli, vault, source, "numbered", "--strict-numbering")

    # Then it succeeds
    assert code == 0
    assert document["written"] is True


def test_headings_mode_numbers_a_heading_that_repeats(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a text whose headings repeat a name, one of them beside a heading that already
    # has the next suffix
    text = (
        "# Notes\n1.1 First note.\n# Boats\n2.1 A boat.\n# Notes\n3.1 Second note.\n"
        "# Notes\n4.1 Third note.\n# Notes 2\n5.1 A heading that reads like a suffix.\n"
    )

    # When it is segmented in headings mode
    code, locators = _segment_locators_of(run_cli, tmp_path, text, "headings")

    # Then every heading has its own locator and the run passes its checks
    headings = [locator for locator in locators if "/" not in locator]
    assert code == 0
    assert headings == ["notes", "boats", "notes-2", "notes-3", "notes-2-2"]
    assert len(set(locators)) == len(locators)
