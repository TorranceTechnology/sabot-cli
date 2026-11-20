"""SEG-1 to SEG-10: `segment` turns text into a span file of locations and hashes."""

import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES, CliRunner
from source_vaults import (
    error_code,
    require_pdftotext,
    snapshot,
    vault_with_sources,
    write_text,
)
from support.minimal_pdf import write_pdf
from support.plant import plant
from support.vault_state import vault_state

RULES_E1 = FIXTURES / "rules-e1.txt"
RULES_E2 = FIXTURES / "rules-e2.txt"
HANDBOOK = FIXTURES / "handbook.md.txt"
SHEET = FIXTURES / "sheet.txt"
SHEET_INSERTED = FIXTURES / "sheet-inserted.txt"
SHEET_BOUNDARIES = FIXTURES / "sheet.boundaries.jsonl"

_DOT_LEADER_LINE = re.compile(r"\.{4,}")
_PAGE_FOOTER_LINE = re.compile(r"(?m)^\s*- \d+ -\s*$")
_RULE_LINE = re.compile(r"(?m)^\d+(?:\.\d+)*[a-z]?\.?\s{2,}\S")
_BOUNDARIES_DEFECTS = {
    "boundaries-gap": "gap",
    "boundaries-overlap": "overlap",
    "boundaries-offset-out-of-range": "offset-out-of-range",
    "boundaries-duplicate-locator": "duplicate-locator",
    "boundaries-missing-parent": "missing-parent",
    "boundaries-record-malformed": "record-malformed",
    "boundaries-offset-order": "offset-order",
    "boundaries-locator-malformed": "locator-malformed",
    "boundaries-child-outside-parent": "child-outside-parent",
    "boundaries-parent-cycle": "parent-cycle",
    "boundaries-page-decreasing": "page-decreasing",
}
# A span that is its own parent also overlaps the children it shares its range with.
_ALSO_NAMED = {"boundaries-parent-cycle": ("overlap",)}


def _segment_rules(
    run_cli: CliRunner, vault: Path, text: Path = RULES_E1, *extra: str, source: str = "dw1"
) -> tuple[int, dict[str, object]]:
    result = run_cli(
        "segment", str(text), "--source", source, "--mode", "numbered",
        "--numbering", "dotted-letter", *extra, "--vault", str(vault), "--json",
    )  # fmt: skip
    return result.returncode, result.json()


def _records(vault: Path, key: str) -> list[dict[str, object]]:
    path = vault / "spans" / f"{key}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _without_position(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {name: value for name, value in record.items() if name not in ("offset", "page")}
        for record in records
    ]


def _span_locators(vault: Path, key: str) -> list[str]:
    return [str(r["locator"]) for r in _records(vault, key) if r["kind"] == "span"]


def _failed(document: dict[str, object]) -> dict[str, list[str]]:
    checks = document["checks"]
    assert isinstance(checks, list)
    return {c["name"]: c["failures"] for c in checks if c["status"] == "failed"}


def test_numbered_mode_emits_one_span_per_rule(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given Driftwood edition one, whose numbered items can be counted from the text itself
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    expected = len(_RULE_LINE.findall(RULES_E1.read_text(encoding="utf-8")))

    # When it is segmented in numbered mode with lettered rules
    code, document = _segment_rules(run_cli, vault)

    # Then there is one span per item, and the span file agrees with the reported count
    assert code == 0
    assert document["spans"] == expected == 51
    assert len(_span_locators(vault, "dw1")) == expected


def test_span_file_holds_no_source_words(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a segmented source
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    _segment_rules(run_cli, vault)
    source = RULES_E1.read_text(encoding="utf-8")
    written = (vault / "spans" / "dw1.jsonl").read_text(encoding="utf-8")

    # When every record is read
    records = _records(vault, "dw1")

    # Then every value is a number, a locator-shaped token or a hash, and no sentence is present
    for record in records:
        assert "text" not in record
        for value in record.values():
            if isinstance(value, str):
                assert re.fullmatch(r"[a-z0-9#./-]+|[0-9a-f]{16}", value), value
    sentences = re.split(r"(?<=\.)\s+", " ".join(source.split()))
    assert not [s for s in sentences if len(s) > 20 and s in written]


def test_wrapped_line_starting_with_a_digit_is_not_a_span(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given rule 5.4, whose wrapped second line begins "3 units of weight"
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})

    # When edition one is segmented
    code, _ = _segment_rules(run_cli, vault)

    # Then the line joins rule 5.4 and creates no span of its own
    records = {r["locator"]: r for r in _records(vault, "dw1") if r["kind"] == "span"}
    assert code == 0
    assert records["5.4"]["words"] == 47
    assert list(records).count("3") == 1


def test_contents_dot_leaders_and_page_numbers_are_dropped(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given edition one, and a copy with its contents entries and "- n -" page footers deleted
    original = RULES_E1.read_text(encoding="utf-8")
    cleaned = "".join(
        line
        for line in original.splitlines(keepends=True)
        if not _DOT_LEADER_LINE.search(line) and not _PAGE_FOOTER_LINE.match(line)
    )
    assert _DOT_LEADER_LINE.search(original)
    assert _PAGE_FOOTER_LINE.search(original)
    cleaned_path = tmp_path / "rules-e1-cleaned.txt"
    write_text(cleaned_path, cleaned)
    with_noise = vault_with_sources(run_cli, tmp_path / "with-noise", {"dw1": RULES_E1})
    without_noise = vault_with_sources(run_cli, tmp_path / "without-noise", {"dw1": RULES_E1})

    # When both are segmented with no page skipped, so the contents page is read
    code, _ = _segment_rules(run_cli, with_noise, RULES_E1, "--skip-pages", "0")
    cleaned_code, _ = _segment_rules(run_cli, without_noise, cleaned_path, "--skip-pages", "0")

    # Then the spans, with their hashes, are identical but for where they sit in the text: the
    # entries made no span and the footer text is in no span's words
    assert (code, cleaned_code) == (0, 0)
    assert _without_position(_records(with_noise, "dw1")) == _without_position(
        _records(without_noise, "dw1")
    )


@pytest.mark.parametrize(
    ("defect", "check", "place"),
    [
        pytest.param("segmenter-duplicate-locator", "duplicate-locator", "4.3", id="duplicate"),
        pytest.param("segmenter-non-monotone", "non-monotone", "4.4", id="non-monotone"),
    ],
)
def test_self_check_refuses_duplicate_and_non_monotone_numbers(
    run_cli: CliRunner, tmp_path: Path, defect: str, check: str, place: str
) -> None:
    # Given edition one with one planted numbering defect
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(RULES_E1, work / "rules-e1.txt")
    plant(work, defect)
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    before = snapshot(vault)

    # When it is segmented
    code, document = _segment_rules(run_cli, vault, work / "rules-e1.txt")

    # Then the check is named with the place, and no file is written
    assert code == 1
    assert _failed(document) == {check: [place]}
    assert document["written"] is False
    assert snapshot(vault) == before


def test_sentence_mode_ids_survive_an_inserted_sentence(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a sheet and the same sheet with one sentence inserted
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"sh": SHEET, "shi": SHEET_INSERTED})

    # When both are segmented in sentences mode
    for key, text in (("sh", SHEET), ("shi", SHEET_INSERTED)):
        result = run_cli(
            "segment", str(text), "--source", key, "--mode", "sentences", "--vault", str(vault)
        )
        assert result.returncode == 0
    before = _span_locators(vault, "sh")
    after = _span_locators(vault, "shi")

    # Then every earlier sentence keeps its locator and exactly one is new
    assert len(after) == len(before) + 1
    assert set(before) < set(after)
    assert all(re.fullmatch(r"s-[0-9a-f]{8}", locator) for locator in after)


def test_headings_mode_and_dotted_letter_numbering(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given the heading handbook, and edition one with lettered rules
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"hb": HANDBOOK, "dw1": RULES_E1})

    # When each is segmented in the matching mode
    headings = run_cli(
        "segment", str(HANDBOOK), "--source", "hb", "--mode", "headings", "--vault", str(vault)
    )
    code, _ = _segment_rules(run_cli, vault)

    # Then locators are slug paths with numbered items as children, and 4.2a sits under 4.2
    assert headings.returncode == 0
    assert code == 0
    handbook = {r["locator"]: r for r in _records(vault, "hb") if r["kind"] == "span"}
    assert handbook["the-turn/ebb"]["parent"] == "the-turn"
    assert handbook["the-turn/ebb/1"]["parent"] == "the-turn/ebb"
    assert handbook["winning/3"]["parent"] == "winning"
    rules = {r["locator"]: r for r in _records(vault, "dw1") if r["kind"] == "span"}
    assert rules["4.2a"]["parent"] == "4.2"
    assert rules["4.2"]["parent"] == "4"


def test_boundaries_mode_hashes_agent_proposed_spans(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given the unnumbered sheet and a valid boundaries file
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"sh": SHEET})
    arguments = (
        "segment", str(SHEET), "--source", "sh", "--mode", "boundaries",
        "--boundaries", str(SHEET_BOUNDARIES), "--vault", str(vault),
    )  # fmt: skip
    proposed = [json.loads(line) for line in SHEET_BOUNDARIES.read_text().splitlines()]

    # When it is segmented, twice
    first = run_cli(*arguments, "--json")
    written = (vault / "spans" / "sh.jsonl").read_bytes()
    second = run_cli(*arguments, "--json")

    # Then there is one span per proposed record with its locator, and the bytes repeat
    assert first.returncode == 0
    assert _span_locators(vault, "sh") == [r["locator"] for r in proposed if not r.get("skip")]
    assert (vault / "spans" / "sh.jsonl").read_bytes() == written
    assert second.stdout == first.stdout
    # And the vault keeps the boundaries file and the source note names the mode
    assert (vault / "spans" / "sh.boundaries.jsonl").read_bytes() == SHEET_BOUNDARIES.read_bytes()
    assert "segmenter: boundaries\n" in (vault / "sources" / "src.sh.md").read_text()


@pytest.mark.parametrize("defect", sorted(_BOUNDARIES_DEFECTS))
def test_boundaries_mode_refuses_gaps_overlaps_and_bad_offsets(
    run_cli: CliRunner, tmp_path: Path, defect: str
) -> None:
    # Given the sheet and a boundaries file with one planted defect
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(SHEET, work / "sheet.txt")
    shutil.copy(SHEET_BOUNDARIES, work / "sheet.boundaries.jsonl")
    plant(work, defect)
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"sh": SHEET})
    before = snapshot(vault)

    # When it is segmented in boundaries mode
    result = run_cli(
        "segment", str(work / "sheet.txt"), "--source", "sh", "--mode", "boundaries",
        "--boundaries", str(work / "sheet.boundaries.jsonl"), "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then exactly the planted defect is named, and nothing is written
    assert result.returncode == 1
    expected = sorted([_BOUNDARIES_DEFECTS[defect], *_ALSO_NAMED.get(defect, ())])
    assert sorted(_failed(result.json())) == expected
    assert snapshot(vault) == before


def test_rule_based_result_round_trips_through_emitted_boundaries(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given edition one segmented in numbered mode, and that result emitted as boundaries
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    emitted = tmp_path / "emitted.boundaries.jsonl"
    code, document = _segment_rules(run_cli, vault, RULES_E1, "--emit-boundaries", str(emitted))
    assert code == 0
    assert document["boundariesEmitted"] is True
    numbered = _records(vault, "dw1")

    # When the emitted file is segmented in boundaries mode
    result = run_cli(
        "segment", str(RULES_E1), "--source", "dw1", "--mode", "boundaries",
        "--boundaries", str(emitted), "--vault", str(vault),
    )  # fmt: skip

    # Then the span hashes, and the locators they belong to, are the same
    assert result.returncode == 0
    again = _records(vault, "dw1")
    assert {(r["locator"], r["sha256"]) for r in again} == {
        (r["locator"], r["sha256"]) for r in numbered
    }


def test_emitted_boundaries_are_written_even_when_a_check_fails(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given edition one with a duplicated rule number, so numbered mode fails its self-check
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(RULES_E1, work / "rules-e1.txt")
    plant(work, "segmenter-duplicate-locator")
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    emitted = tmp_path / "repair.boundaries.jsonl"

    # When it is segmented with --emit-boundaries
    code, _ = _segment_rules(
        run_cli, vault, work / "rules-e1.txt", "--emit-boundaries", str(emitted)
    )

    # Then the boundaries file exists to be repaired, and the vault has no span file
    assert code == 1
    assert emitted.is_file()
    assert not (vault / "spans" / "dw1.jsonl").exists()


def test_segment_output_is_byte_identical_across_runs(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given two vaults holding the same source
    first = vault_with_sources(run_cli, tmp_path / "first", {"dw1": RULES_E1})
    second = vault_with_sources(run_cli, tmp_path / "second", {"dw1": RULES_E1})

    # When the same segmentation runs in each
    _segment_rules(run_cli, first)
    _segment_rules(run_cli, second)

    # Then the span files are byte-identical
    assert (first / "spans" / "dw1.jsonl").read_bytes() == (
        second / "spans" / "dw1.jsonl"
    ).read_bytes()


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        pytest.param(
            ("--source", "nope", "--mode", "sentences"), "source-not-ingested", id="no-note"
        ),
        pytest.param(
            ("--source", "../x", "--mode", "sentences"), "source-key-invalid", id="bad-key"
        ),
        pytest.param(
            ("--source", "sh", "--mode", "boundaries"), "boundaries-mismatch", id="no-file"
        ),
        pytest.param(
            ("--source", "sh", "--mode", "sentences", "--boundaries", str(SHEET_BOUNDARIES)),
            "boundaries-mismatch",
            id="unused-file",
        ),
        pytest.param(
            ("--source", "sh", "--mode", "numbered", "--skip-pages", "-1"),
            "skip-pages-invalid",
            id="negative-skip",
        ),
    ],
)
def test_segment_refuses_unusable_input_and_writes_nothing(
    run_cli: CliRunner, tmp_path: Path, arguments: tuple[str, ...], code: str
) -> None:
    # Given a vault holding the sheet as source sh
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"sh": SHEET})
    before = snapshot(vault)

    # When segment is given input it cannot use
    result = run_cli("segment", str(SHEET), *arguments, "--vault", str(vault), "--json")

    # Then it refuses with the named code and writes nothing
    assert result.returncode == 3
    assert error_code(result) == code
    assert snapshot(vault) == before


def test_segment_refuses_text_that_is_not_utf8(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault and a text file that is not UTF-8
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"sh": SHEET})
    broken = tmp_path / "broken.txt"
    broken.write_bytes(b"\xff\xfe not text")
    before = vault_state(vault)

    # When it is segmented
    result = run_cli(
        "segment", str(broken), "--source", "sh", "--mode", "sentences", "--vault", str(vault),
        "--json",
    )  # fmt: skip

    # Then segment refuses naming the encoding, and the vault is untouched
    assert result.returncode == 3
    assert error_code(result) == "text-undecodable"
    assert vault_state(vault) == before


def test_both_pdf_extractors_yield_the_same_span_locators(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a generated PDF rules document: a title page, then numbered rules on later pages
    require_pdftotext()
    numbers = [f"{section}.{rule}" for section in (1, 2, 3) for rule in range(1, 21)]
    rules = [f"{number}  The tide rule {number} applies." for number in numbers]
    pdf = tmp_path / "rules.pdf"
    write_pdf(pdf, ["Harbour rules", *[""] * 49, *rules])
    records: dict[str, list[dict[str, object]]] = {}

    # When it is ingested and segmented once with each extractor
    for extractor in ("pypdf", "pdftotext"):
        vault = vault_with_sources(run_cli, tmp_path / extractor)
        text_out = tmp_path / f"{extractor}.txt"
        ingest = run_cli(
            "ingest", str(pdf), "--key", "dw1", "--label", "R", "--title", "Rules",
            "--edition", "1", "--retrieved", "2026-11-19", "--extractor", extractor,
            "--text-out", str(text_out), "--vault", str(vault),
        )  # fmt: skip
        assert ingest.returncode == 0, ingest.stderr
        code, _ = _segment_rules(run_cli, vault, text_out)
        assert code == 0
        records[extractor] = _records(vault, "dw1")

    # Then both span files hold the same locators, pages and primary hashes
    def identity(record: dict[str, object]) -> tuple[object, ...]:
        return (record["locator"], record["page"], record["sha256"], record["parent"])

    assert [identity(r) for r in records["pypdf"]] == [identity(r) for r in records["pdftotext"]]
    assert [r["locator"] for r in records["pypdf"] if r["kind"] == "span"] == numbers


@pytest.mark.parametrize(
    ("key", "text"),
    [pytest.param("dw1", RULES_E1, id="dw1"), pytest.param("dw2", RULES_E2, id="dw2")],
)
def test_the_fixture_span_files_are_what_the_tool_produces(
    run_cli: CliRunner, driftwood_vault: Path, key: str, text: Path
) -> None:
    # Given the fixture vault with its committed span file for an edition
    committed = (driftwood_vault / "spans" / f"{key}.jsonl").read_bytes()

    # When the edition's text is segmented again with the settings its source note records
    code, _ = _segment_rules(run_cli, driftwood_vault, text, source=key)

    # Then the span file is byte-identical to the committed one
    assert code == 0
    assert (driftwood_vault / "spans" / f"{key}.jsonl").read_bytes() == committed


def test_text_with_no_numbered_rule_makes_no_spans_and_writes_nothing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault and text with no numbered rule in it
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    unnumbered = tmp_path / "prose.txt"
    write_text(unnumbered, "Boats wait in the harbour until the tide turns.\n")
    before = vault_state(vault)

    # When it is segmented in numbered mode
    code, document = _segment_rules(run_cli, vault, unnumbered)

    # Then the no-spans check fails and nothing is written
    assert code == 1
    assert _failed(document) == {"no-spans": ["the segmentation produced no spans"]}
    assert vault_state(vault) == before


def test_segment_refuses_a_text_file_that_does_not_exist(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a registered source and a path with no file behind it
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    before = vault_state(vault)

    # When it is segmented
    code, document = _segment_rules(run_cli, vault, tmp_path / "missing.txt")

    # Then segment refuses with file-unreadable and the vault is untouched
    assert code == 3
    assert document["error"]["code"] == "file-unreadable"  # type: ignore[index]
    assert vault_state(vault) == before


@pytest.mark.parametrize(
    "note_bytes",
    [
        pytest.param(b"\xff\xfe not text", id="not-utf-8"),
        pytest.param(b"no front matter at all\n", id="no-front-matter"),
    ],
)
def test_segment_refuses_a_source_note_it_cannot_update(
    run_cli: CliRunner, tmp_path: Path, note_bytes: bytes
) -> None:
    # Given a registered source whose note is not a readable note
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    (vault / "sources" / "src.dw1.md").write_bytes(note_bytes)
    before = vault_state(vault)

    # When its text is segmented
    code, document = _segment_rules(run_cli, vault)

    # Then segment refuses with source-note-invalid before replacing any span file
    assert code == 3
    assert document["error"]["code"] == "source-note-invalid"  # type: ignore[index]
    assert vault_state(vault) == before
