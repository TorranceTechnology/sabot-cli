"""Rules the tools state once: the source key, span files, text files and front matter."""

from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from invocations import RULES_E1
from sabot_cli.sources import SourceError, check_key
from sabot_cli.vault import (
    FrontMatterError,
    TextFileError,
    decode_text,
    encode_text,
    read_text,
    split_front_matter,
)
from source_vaults import vault_with_sources, write_text


def _error(result: CliResult) -> tuple[int, str, str]:
    error = result.json()["error"]
    return result.returncode, str(error["code"]), str(error["message"])  # type: ignore[index]


def test_every_command_refuses_a_bad_source_key_in_the_same_words(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the refusal of the one key rule
    with pytest.raises(SourceError) as refusal:
        check_key("BAD")
    expected = (3, "source-key-invalid", refusal.value.message)
    vault = ["--vault", str(driftwood_vault), "--json"]

    # When each command that takes a source key is given a key the rule refuses
    refused = [
        _error(run_cli("ingest", str(RULES_E1), "--key", "BAD", *vault)),
        _error(run_cli("segment", str(RULES_E1), "--source", "BAD", "--mode", "numbered", *vault)),
        _error(run_cli("diff-editions", "BAD", "dw2", *vault)),
        _error(run_cli("migrate-edition", "dw1", "BAD", "--dry-run", *vault)),
    ]

    # Then each gives that refusal and no other wording
    assert refused == [expected] * 4


def test_every_reader_of_a_span_file_reports_a_missing_file_alike(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition one's span file removed
    (driftwood_vault / "spans" / "dw1.jsonl").unlink()
    vault = ["--vault", str(driftwood_vault), "--json"]

    # When coverage, diff-editions and lint each need it
    coverage = _error(run_cli("coverage", *vault))
    diff = _error(run_cli("diff-editions", "dw1", "dw2", *vault))
    findings = run_cli("lint", *vault).json()["findings"]

    # Then they give the same code and message, and lint repeats that message for each citation
    assert coverage == diff
    assert coverage[:2] == (3, "span-file-missing")
    cannot_be_checked = {f["message"] for f in findings if f["class"] == "span-unknown"}  # type: ignore[index, union-attr]
    assert cannot_be_checked
    assert all(str(message).endswith(f": {coverage[2]}") for message in cannot_be_checked)


def test_every_reader_of_a_span_file_reports_a_broken_file_alike(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition one's span file holding a line that is not a record
    (driftwood_vault / "spans" / "dw1.jsonl").write_bytes(b"not json\n")
    vault = ["--vault", str(driftwood_vault), "--json"]

    # When coverage, diff-editions and lint each read it
    coverage = _error(run_cli("coverage", *vault))
    diff = _error(run_cli("diff-editions", "dw1", "dw2", *vault))
    findings = run_cli("lint", *vault).json()["findings"]

    # Then they give the same code and message, and lint repeats that message for each citation
    assert coverage == diff
    assert coverage[:2] == (3, "span-file-invalid")
    cannot_be_checked = {f["message"] for f in findings if f["class"] == "span-unknown"}  # type: ignore[index, union-attr]
    assert cannot_be_checked
    assert all(str(message).endswith(f": {coverage[2]}") for message in cannot_be_checked)


def test_segmenting_text_with_a_byte_order_mark_gives_the_same_spans(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given one numbered text saved twice, the second time with a byte order mark
    body = "1.1 First rule.\n1.2 Second rule.\n"
    plain = write_text(tmp_path / "plain.txt", body)
    marked = tmp_path / "marked.txt"
    marked.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    vault = vault_with_sources(run_cli, tmp_path / "vault", {"dw1": plain, "dw2": plain})

    # When each is segmented into its own source
    first = run_cli(
        "segment", str(plain), "--source", "dw1", "--mode", "numbered", "--vault", str(vault)
    )
    second = run_cli(
        "segment", str(marked), "--source", "dw2", "--mode", "numbered", "--vault", str(vault)
    )

    # Then both read the same text, so the span files match apart from the key
    assert (first.returncode, second.returncode) == (0, 0)
    spans = [
        (vault / "spans" / f"{key}.jsonl").read_text(encoding="utf-8") for key in ("dw1", "dw2")
    ]
    assert spans[0] == spans[1].replace("dw2", "dw1")


def test_read_text_drops_a_byte_order_mark_and_names_what_went_wrong(tmp_path: Path) -> None:
    # Given a file with a byte order mark, an invalid file and a missing one
    marked = tmp_path / "marked.txt"
    marked.write_bytes(b"\xef\xbb\xbfhello\r\nthere")
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe\x00")

    # When each is read
    # Then the first gives its text without the mark, and the others say why they cannot
    assert read_text(marked) == "hello\r\nthere"
    with pytest.raises(TextFileError) as undecodable:
        read_text(invalid)
    assert (undecodable.value.reason, str(undecodable.value)) == (
        "undecodable",
        "invalid.txt is not valid UTF-8",
    )
    with pytest.raises(TextFileError) as unreadable:
        read_text(tmp_path / "missing.txt")
    assert unreadable.value.reason == "unreadable"
    assert str(unreadable.value).startswith("missing.txt cannot be read: ")


def test_encode_text_puts_back_the_mark_the_original_had() -> None:
    # Given original bytes with and without a byte order mark
    marked, plain = b"\xef\xbb\xbfold", b"old"

    # When new text is encoded like each
    # Then only the marked original gives a marked result, and decoding it round-trips
    assert encode_text("new", like=marked) == b"\xef\xbb\xbfnew"
    assert encode_text("new", like=plain) == b"new"
    assert decode_text(encode_text("new", like=marked), "x") == "new"


def test_split_front_matter_finds_the_closing_line_and_keeps_line_endings() -> None:
    # Given a CRLF note
    text = "---\r\nid: a\r\n---\r\nbody\r\n"

    # When it is split
    lines, closing = split_front_matter(text)

    # Then the closing delimiter is found and each line keeps its carriage return
    assert closing == 2
    assert lines[:3] == ["---\r", "id: a\r", "---\r"]


@pytest.mark.parametrize(
    "text",
    ["no front matter\n", "---\nid: a\nno closing line\n", ""],
    ids=["no-opening", "no-closing", "empty"],
)
def test_split_front_matter_refuses_text_without_a_front_matter_block(text: str) -> None:
    # Given text with no complete front matter block
    # When it is split
    # Then it is refused
    with pytest.raises(FrontMatterError):
        split_front_matter(text)
