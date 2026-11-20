"""The content guard refuses guarded terms and names none of them.

The term used here is invented; its hash lives only in this test's own hash list.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent
GUARD = REPOSITORY / "ci" / "check_content_guard.py"
INVENTED_TERM = "zorblax quillfeather"


def _run_guard(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GUARD), *arguments], capture_output=True, text=True)


def _hash_of(term: str) -> str:
    result = _run_guard("--hash", term)
    assert result.returncode == 0
    return result.stdout.strip()


@pytest.fixture
def scratch_repository(tmp_path: Path) -> Path:
    """An empty git repository plus a hash list that guards only the invented term."""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (tmp_path / "hashes.txt").write_text(
        f"# test-only list\n{_hash_of(INVENTED_TERM)}\n", encoding="utf-8"
    )
    return repository


def _scan(repository: Path) -> subprocess.CompletedProcess[str]:
    return _run_guard("--root", str(repository), "--hashes", str(repository.parent / "hashes.txt"))


@pytest.mark.parametrize(
    "text",
    [
        "A deck called Zorblax Quillfeather appears here.",
        "zorblax\n  QUILLFEATHER, split across lines",
        "mentions zorblax-quillfeather with a hyphen",
    ],
)
def test_a_planted_term_is_refused(scratch_repository: Path, text: str) -> None:
    # Given a tracked file holding the term in some spelling
    (scratch_repository / "notes.md").write_text(text + "\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it exits 1, names the file, and does not print the term
    assert result.returncode == 1
    assert "notes.md" in result.stdout
    assert "zorblax" not in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    "text",
    [
        "ZorblaxQuillfeather appears fused",
        "the Zorblax\u2019s Quillfeather, with a curly apostrophe",
        "the Zorblax's Quillfeather, with a straight one",
        "zorblaxQuillfeather in mixed case",
    ],
)
def test_camel_case_and_possessive_spellings_are_refused(
    scratch_repository: Path, text: str
) -> None:
    # Given a tracked file spelling the term fused in camel case, or with a possessive
    (scratch_repository / "notes.md").write_bytes((text + "\n").encode("utf-8"))

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it exits 1, names the file, and does not print the term
    assert result.returncode == 1
    assert "notes.md" in result.stdout
    assert "zorblax" not in (result.stdout + result.stderr).lower()


def test_a_utf16_file_holding_the_term_is_refused(scratch_repository: Path) -> None:
    # Given a UTF-16 file, which holds NUL bytes, with the term in it
    (scratch_repository / "notes.txt").write_bytes(f"Zorblax {INVENTED_TERM}\n".encode("utf-16"))

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it is read as text and refused
    assert result.returncode == 1
    assert "notes.txt" in result.stdout


def test_a_text_file_that_is_not_utf8_is_reported_not_skipped(scratch_repository: Path) -> None:
    # Given a Latin-1 file, which is text but not UTF-8
    (scratch_repository / "latin.txt").write_bytes("caf\xe9 au lait\n".encode("latin-1"))

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then the guard fails and names the file it could not read
    assert result.returncode == 1
    assert result.stdout.startswith("latin.txt:")


def test_a_term_longer_than_the_comparison_window_is_refused() -> None:
    # Given a term of five words, which no four-word n-gram could ever match
    # When it is hashed
    result = _run_guard("--hash", "one two three four five")

    # Then the guard refuses, prints no hash, and says why
    assert result.returncode == 2
    assert result.stdout == ""
    assert "at most 4 words" in result.stderr


def test_a_guarded_file_name_is_refused(scratch_repository: Path) -> None:
    # Given a file whose name holds the term
    (scratch_repository / "zorblax-quillfeather.md").write_text("harmless\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then it exits 1
    assert result.returncode == 1


def test_a_single_word_of_the_term_alone_is_allowed(scratch_repository: Path) -> None:
    # Given a file with only one word of a two-word term
    (scratch_repository / "notes.md").write_text("zorblax is fine alone\n", encoding="utf-8")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then nothing is reported
    assert result.returncode == 0
    assert result.stdout == ""


def test_a_binary_file_is_not_scanned(scratch_repository: Path) -> None:
    # Given a binary file, not valid UTF-8, whose bytes contain the term
    (scratch_repository / "blob.bin").write_bytes(b"\xff\0" + INVENTED_TERM.encode() + b"\0\xfe")

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then nothing is reported
    assert result.returncode == 0


def test_utf8_text_holding_a_nul_byte_is_reported_and_still_scanned(
    scratch_repository: Path,
) -> None:
    # Given a file that is valid UTF-8 apart from one NUL byte, beside a clean file with one
    (scratch_repository / "stray.txt").write_bytes(b"nothing to see\0 here\n")
    (scratch_repository / "planted.txt").write_bytes(f"{INVENTED_TERM}\0\n".encode())

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then both are reported for the NUL, and the planted term is found through it
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert "planted.txt: text that holds NUL bytes, which the guard cannot trust" in lines
    assert "stray.txt: text that holds NUL bytes, which the guard cannot trust" in lines
    assert any(line.startswith("planted.txt: matches guarded term") for line in lines)
    assert not any(line.startswith("stray.txt: matches") for line in lines)


def test_utf16_text_saved_without_a_byte_order_mark_is_not_skipped(
    scratch_repository: Path,
) -> None:
    # Given a UTF-16 file with no byte order mark that holds the term
    (scratch_repository / "wide.txt").write_bytes(f"{INVENTED_TERM}\n".encode("utf-16-le"))

    # When the guard scans the repository
    result = _scan(scratch_repository)

    # Then the term is found
    assert result.returncode == 1
    assert any(
        line.startswith("wide.txt: matches guarded term") for line in result.stdout.splitlines()
    )


@pytest.mark.parametrize("term", ["", "   ", "---", "\u00b7"])
def test_a_term_with_no_letter_or_digit_is_refused_when_hashing(term: str) -> None:
    # Given a term that normalises to nothing, which would stand for every file
    # When it is hashed
    result = _run_guard(f"--hash={term}")

    # Then the guard refuses, prints no hash, and says why
    assert result.returncode == 2
    assert result.stdout == ""
    assert "at least one letter or digit" in result.stderr


def test_the_repository_passes_its_own_guard() -> None:
    # Given the checkout and the shipped hash list
    # When the guard scans it
    result = _run_guard("--root", str(REPOSITORY))

    # Then nothing is reported
    assert result.returncode == 0, result.stdout
