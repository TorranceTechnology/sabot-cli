"""ING-1 to ING-6: `ingest` records a source and refuses input it cannot register."""

import hashlib
import json
from pathlib import Path

import pypdf
import pytest

from conftest import FIXTURES, CliResult, CliRunner
from sabot_cli.vault import load_vault
from source_vaults import (
    RETRIEVED,
    error_code,
    ingest_arguments,
    require_pdftotext,
    snapshot,
    vault_with_sources,
    write_text,
)
from support.filesystem import require_case_insensitive
from support.minimal_pdf import write_pdf
from support.vault_state import vault_state

RULES_E1 = FIXTURES / "rules-e1.txt"
RULES_E2 = FIXTURES / "rules-e2.txt"


def _note_properties(vault: Path, key: str) -> dict[str, object]:
    note = load_vault(vault, frozenset()).notes[f"src.{key}"]
    return dict(note.properties)


def _registered_keys(vault: Path) -> list[str]:
    lines = (vault / "_ruleset.yaml").read_text(encoding="utf-8").splitlines()
    return [line.split(":", 1)[1].strip() for line in lines if line.startswith("  - key:")]


def test_ingest_records_hash_and_bytes_of_a_text_source(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given an empty vault and a text file
    vault = vault_with_sources(run_cli, tmp_path / "vault")

    # When the file is ingested
    result = run_cli(*ingest_arguments(RULES_E1, "dw1"), "--vault", str(vault), "--json")

    # Then the source note holds its SHA-256 and byte count, and the key is registered
    data = RULES_E1.read_bytes()
    assert result.returncode == 0
    properties = _note_properties(vault, "dw1")
    assert properties["sha256"] == hashlib.sha256(data).hexdigest()
    assert properties["bytes"] == len(data)
    assert properties["retrieved"] == RETRIEVED
    assert result.json()["sha256"] == properties["sha256"]
    assert _registered_keys(vault) == ["dw1"]


def _ingest_pdf(
    run_cli: CliRunner, pdf: Path, vault: Path, text_out: Path, *options: str
) -> CliResult:
    return run_cli(
        "ingest", str(pdf), "--key", "pdf1", "--label", "P", "--title", "Sample PDF",
        "--edition", "1", "--retrieved", RETRIEVED, "--text-out", str(text_out),
        "--vault", str(vault), "--json", *options,
    )  # fmt: skip


def test_ingest_extracts_a_pdf_with_the_default_extractor(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a generated PDF and no --extractor option
    pdf = tmp_path / "sample.pdf"
    write_pdf(pdf, ["Harbour rules sample", "", "1. Tides", "1.1 The tide rises."])
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    text_out = tmp_path / "out" / "sample.txt"

    # When it is ingested
    result = _ingest_pdf(run_cli, pdf, vault, text_out)

    # Then the text-out file holds its words and the note records pypdf, its version and mode
    assert result.returncode == 0, result.stderr
    text = text_out.read_text(encoding="utf-8")
    assert "1.1 The tide rises." in text
    assert text.endswith("\f")
    extractor = _note_properties(vault, "pdf1")["extractor"]
    assert extractor == f"pypdf {pypdf.__version__} layout"
    assert result.json()["extractor"] == extractor


def test_ingest_extracts_a_pdf_with_pdftotext_when_selected(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a generated PDF and an environment that either has pdftotext or may skip it
    require_pdftotext()
    pdf = tmp_path / "sample.pdf"
    write_pdf(pdf, ["Harbour rules sample", "", "1. Tides", "1.1 The tide rises."])
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    text_out = tmp_path / "out" / "sample.txt"

    # When it is ingested with --extractor pdftotext
    result = _ingest_pdf(run_cli, pdf, vault, text_out, "--extractor", "pdftotext")

    # Then the text-out file holds its words and the note names pdftotext as the extractor
    assert result.returncode == 0, result.stderr
    assert "The tide rises." in text_out.read_text(encoding="utf-8")
    extractor = _note_properties(vault, "pdf1")["extractor"]
    assert isinstance(extractor, str)
    assert extractor.startswith("pdftotext ")
    assert extractor.endswith(" -layout")


def test_ingest_refuses_pdftotext_when_it_is_absent(
    run_cli: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a PDF and an environment without pdftotext on the path
    pdf = tmp_path / "sample.pdf"
    write_pdf(pdf, ["A line"])
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    # When the PDF is ingested with --extractor pdftotext
    result = run_cli(
        "ingest", str(pdf), "--key", "pdf1", "--label", "P", "--title", "T", "--edition", "1",
        "--retrieved", RETRIEVED, "--extractor", "pdftotext", "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then ingest refuses with the missing extractor and writes nothing
    assert result.returncode == 3
    assert error_code(result) == "extractor-missing"
    assert snapshot(vault) == before


def test_ingest_refuses_a_pdf_pypdf_cannot_read(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a file that starts like a PDF but holds nothing pypdf can parse
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.4\nthis is not a document\n")
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)

    # When it is ingested with the default extractor
    result = _ingest_pdf(run_cli, pdf, vault, tmp_path / "out.txt")

    # Then ingest refuses with an extraction failure and writes nothing
    assert result.returncode == 3
    assert error_code(result) == "extraction-failed"
    assert snapshot(vault) == before
    assert not (tmp_path / "out.txt").exists()


def test_ingest_extracts_html_text_without_markup(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a saved web page with markup, a script and an entity
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>T</title><script>var x = 1;</script></head>"
        "<body><h1>Tides</h1><p>The tide  rises &amp; falls.</p>"
        "<p>Second<br>line</p></body></html>",
        encoding="utf-8",
    )
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    text_out = tmp_path / "page.txt"

    # When it is ingested
    result = run_cli(
        "ingest", str(page), "--key", "web1", "--label", "W", "--title", "Web", "--edition", "1",
        "--retrieved", RETRIEVED, "--text-out", str(text_out), "--vault", str(vault),
    )  # fmt: skip

    # Then the text holds the visible words, one block per paragraph, and none of the markup
    assert result.returncode == 0
    assert text_out.read_text(encoding="utf-8") == (
        "Tides\n\nThe tide rises & falls.\n\nSecond\nline\n"
    )


def test_ingest_refuses_text_output_inside_the_vault(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given an empty vault
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)

    # When the text output is placed under the vault
    result = run_cli(
        *ingest_arguments(RULES_E1, "dw1"), "--text-out", str(vault / "spans" / "text.txt"),
        "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then ingest refuses and nothing is written
    assert result.returncode == 3
    assert error_code(result) == "text-out-inside-vault"
    assert snapshot(vault) == before


def test_ingest_refuses_a_duplicate_key(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault in which dw1 is registered
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    before = snapshot(vault)

    # When another file is ingested under the same key
    result = run_cli(*ingest_arguments(RULES_E2, "dw1"), "--vault", str(vault), "--json")

    # Then it is refused and the vault is unchanged
    assert result.returncode == 3
    assert error_code(result) == "source-key-registered"
    assert snapshot(vault) == before


def test_verify_detects_a_different_file(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a registered source and a copy of the file that differs by one byte
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    changed = tmp_path / "changed.txt"
    changed.write_bytes(RULES_E1.read_bytes() + b" ")
    before = snapshot(vault)

    # When each file is verified against the registered hash
    same = run_cli("ingest", str(RULES_E1), "--key", "dw1", "--verify", "--vault", str(vault))
    different = run_cli(
        "ingest", str(changed), "--key", "dw1", "--verify", "--vault", str(vault), "--json"
    )

    # Then the original matches, the changed file does not, and nothing is written
    assert same.returncode == 0
    assert different.returncode == 1
    assert different.json()["match"] is False
    assert snapshot(vault) == before


def test_store_copies_the_original_under_its_hash_name(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault and a store directory outside it
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    store = tmp_path / "store"
    digest = hashlib.sha256(RULES_E1.read_bytes()).hexdigest()

    # When the file is ingested with --store
    result = run_cli(
        *ingest_arguments(RULES_E1, "dw1"), "--store", str(store), "--vault", str(vault)
    )

    # Then the store holds <key>.<hash16>.<ext> with identical bytes
    assert result.returncode == 0
    stored = store / f"dw1.{digest[:16]}.txt"
    assert stored.read_bytes() == RULES_E1.read_bytes()


def test_store_inside_the_vault_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)

    # When the store is a folder of the vault
    result = run_cli(
        *ingest_arguments(RULES_E1, "dw1"), "--store", str(vault / "sources" / "originals"),
        "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then ingest refuses and nothing is written
    assert result.returncode == 3
    assert error_code(result) == "store-inside-vault"
    assert snapshot(vault) == before


def test_registration_adds_the_key_and_a_supersedes_only_when_asked(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with dw1 registered
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})

    # When a later edition is ingested without --supersedes, and a third with it
    plain = run_cli(*ingest_arguments(RULES_E2, "dw2"), "--vault", str(vault))
    third = tmp_path / "third.txt"
    third.write_text("third edition\n", encoding="utf-8")
    superseding = run_cli(
        *ingest_arguments(third, "dw3"), "--supersedes", "dw1", "--vault", str(vault)
    )

    # Then both keys are registered, and only dw3 carries a supersedes entry
    assert plain.returncode == 0
    assert superseding.returncode == 0
    ruleset = (vault / "_ruleset.yaml").read_text(encoding="utf-8")
    assert _registered_keys(vault) == ["dw1", "dw2", "dw3"]
    assert ruleset.count("supersedes:") == 1
    assert "key: dw3\n    note: src.dw3\n    supersedes: dw1\n" in ruleset
    assert "supersedes" not in _note_properties(vault, "dw2")
    assert _note_properties(vault, "dw3")["supersedes"] == "dw1"


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        pytest.param(
            ("--extractor", "nosuch", "--format", "pdf"), "extractor-unknown", id="extractor"
        ),
        pytest.param(("--supersedes", "zz9"), "source-key-unknown", id="supersedes"),
        pytest.param(("--amends", "zz9"), "source-key-unknown", id="amends"),
        pytest.param(("--effective", "2026-13-01"), "date-invalid", id="effective-date"),
        pytest.param(("--title", "two\nlines"), "value-invalid", id="title-line-break"),
    ],
)
def test_ingest_refuses_invalid_options_and_writes_nothing(
    run_cli: CliRunner, tmp_path: Path, extra: tuple[str, ...], code: str
) -> None:
    # Given a vault holding dw1
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    before = snapshot(vault)
    base = list(ingest_arguments(RULES_E2, "dw2"))
    for option in extra[::2]:
        if option in base:
            index = base.index(option)
            del base[index : index + 2]

    # When an option is invalid
    result = run_cli(*base, *extra, "--vault", str(vault), "--json")

    # Then ingest refuses with the named code and nothing is written
    assert result.returncode == 3
    assert error_code(result) == code
    assert snapshot(vault) == before


def test_ingest_refuses_a_file_that_does_not_exist(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault and a path with no file behind it
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = vault_state(vault)

    # When it is ingested
    result = run_cli(
        *ingest_arguments(tmp_path / "missing.txt", "dw1"), "--vault", str(vault), "--json"
    )

    # Then ingest refuses with file-unreadable and the vault is untouched
    assert result.returncode == 3
    assert error_code(result) == "file-unreadable"
    assert vault_state(vault) == before


def test_ingest_refuses_a_key_whose_source_note_already_exists(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault holding a source note for a key that the ruleset does not register
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    (vault / "sources").mkdir(exist_ok=True)
    write_text(vault / "sources" / "src.dw1.md", "left by hand\n")
    before = vault_state(vault)

    # When a file is ingested under that key
    result = run_cli(*ingest_arguments(RULES_E1, "dw1"), "--vault", str(vault), "--json")

    # Then ingest refuses rather than overwrite the note, and the vault is untouched
    assert result.returncode == 3
    assert error_code(result) == "source-note-exists"
    assert vault_state(vault) == before


def test_verify_refuses_a_key_with_no_source_note(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault with no source registered
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = vault_state(vault)

    # When a file is verified against a key nobody ingested
    result = run_cli(
        "ingest", str(RULES_E1), "--key", "dw1", "--verify", "--vault", str(vault), "--json"
    )

    # Then it refuses with source-not-found
    assert result.returncode == 3
    assert error_code(result) == "source-not-found"
    assert vault_state(vault) == before


def test_verify_refuses_a_source_note_that_records_no_hash(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a registered source whose note has lost its sha256 line
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": RULES_E1})
    note = vault / "sources" / "src.dw1.md"
    lines = note.read_text(encoding="utf-8").splitlines(keepends=True)
    write_text(note, "".join(line for line in lines if not line.startswith("sha256:")))
    before = vault_state(vault)

    # When a file is verified against it
    result = run_cli(
        "ingest", str(RULES_E1), "--key", "dw1", "--verify", "--vault", str(vault), "--json"
    )

    # Then it refuses with source-note-invalid
    assert result.returncode == 3
    assert error_code(result) == "source-note-invalid"
    assert vault_state(vault) == before


def test_ingest_refuses_a_file_of_unknown_format(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a file with no recognisable format
    blob = tmp_path / "rules.bin"
    blob.write_bytes(b"\x00\x01")
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = vault_state(vault)

    # When it is ingested without --format
    result = run_cli(
        "ingest", str(blob), "--key", "x1", "--label", "L", "--title", "T", "--edition", "1",
        "--retrieved", RETRIEVED, "--vault", str(vault), "--json",
    )  # fmt: skip

    # Then ingest refuses naming the undetectable format, and the vault is untouched
    assert result.returncode == 3
    assert error_code(result) == "format-undetectable"
    assert vault_state(vault) == before


def test_ingest_without_registration_fields_is_a_usage_error(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)

    # When ingest has only a file and a key, and no --verify
    result = run_cli("ingest", str(RULES_E1), "--key", "dw1", "--vault", str(vault))

    # Then it is a usage error that names the missing options, and nothing is written
    assert result.returncode == 2
    assert "--label" in result.stderr
    assert snapshot(vault) == before


def test_text_out_is_reported_by_file_name_without_a_directory(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an empty vault and a text output path deep under a temporary directory
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    text_out = tmp_path / "private" / "extracted" / "rules.txt"

    # When the file is ingested with --text-out, in both output formats
    as_json = run_cli(
        *ingest_arguments(RULES_E1, "dw1"), "--text-out", str(text_out), "--vault", str(vault),
        "--json",
    )  # fmt: skip
    as_text = run_cli(
        *ingest_arguments(RULES_E2, "dw2"), "--text-out", str(text_out), "--vault", str(vault)
    )

    # Then neither output carries the directory, only the file's name
    assert as_json.json()["textOut"] == "rules.txt"
    assert "text-out: rules.txt\n" in as_text.stdout
    for spelling in (str(tmp_path), tmp_path.as_posix(), json.dumps(str(tmp_path))[1:-1]):
        assert spelling not in as_json.stdout + as_text.stdout


def test_ingest_refuses_a_text_output_spelled_in_another_letter_case(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a file system that ignores letter case and an empty vault
    require_case_insensitive(tmp_path)
    vault = vault_with_sources(run_cli, tmp_path / "vault")
    before = snapshot(vault)
    leak = tmp_path / "VAULT" / "leak.txt"

    # When the text output names the vault in capitals
    result = run_cli(
        *ingest_arguments(RULES_E1, "dw1"), "--text-out", str(leak), "--vault", str(vault),
        "--json",
    )  # fmt: skip

    # Then it is refused as inside the vault and nothing is written
    assert result.returncode == 3
    assert error_code(result) == "text-out-inside-vault"
    assert snapshot(vault) == before
