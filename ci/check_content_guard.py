"""Refuse tracked text files whose words match a guarded term.

Claim: no tracked text file or file name contains a listed term, and no text file goes unread.
Exercises: each word n-gram of 1 to 4 words in each file, read four ways, against the hashes.
Environment: Python 3.12 or later and git on PATH; no network, no third-party packages.
Real-artifact probe: run over the repository checkout; tests/test_content_guard.py plants a term.
Refusal twin: tests/test_content_guard.py::test_a_planted_term_is_refused.

The list stores SHA-256 hashes of terms, never the terms, so the guard can ship in a public
repository without naming what it guards. A term is normalised before hashing: accents are
removed, the text is lower-cased, and it is split into words of letters and digits, which are
joined by single spaces. Hash a new term with `python ci/check_content_guard.py --hash "<term>"`
and add the output to the list; a term of more than four words is refused, since no n-gram that
long is ever compared.

A file is also read with a camel-case word split into its parts (`FooBar` as `foo bar`) and
with a trailing possessive (`'s`, straight or curly) dropped, so those spellings of a term still
match. A file with a UTF-16 or UTF-32 byte order mark is decoded as such. A file that holds no
NUL byte and is not UTF-8 is reported as unreadable by the guard, which fails the run. A file
with a NUL byte and no byte order mark is binary when it is not valid UTF-8, and is not
scanned; when it is valid UTF-8 it is text holding NUL bytes (a UTF-16 file saved without its
mark looks like this only while its text is ASCII), which the guard scans with the NUL bytes
removed and also reports, since a person must decide whether the file is text. UTF-16 saved
without a mark that holds any other character is not valid UTF-8, so it is skipped as
binary. An empty term, one with no letters or digits, is refused when hashing, because it
would stand for every file.
"""

import argparse
import codecs
import hashlib
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path

MAX_TERM_WORDS = 4
DEFAULT_HASH_FILE = Path(__file__).with_name("content_guard_hashes.txt")
_WORD = re.compile(r"[^\W_]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_POSSESSIVE = re.compile(r"['\u2019]s\b", re.IGNORECASE)
_BYTE_ORDER_MARKS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def normalise_words(
    text: str, *, split_camel: bool = False, drop_possessive: bool = False
) -> list[str]:
    """Split text into lower-cased, accent-free words of letters and digits.

    `split_camel` also breaks a word where a capital follows a lower-case letter or digit, and
    `drop_possessive` removes a trailing `'s` before splitting.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    if drop_possessive:
        stripped = _POSSESSIVE.sub("", stripped)
    if split_camel:
        stripped = _CAMEL_BOUNDARY.sub(" ", stripped)
    return _WORD.findall(stripped.lower())


def term_hash(term: str) -> str:
    """Return the SHA-256 hex digest of a term in its normalised form."""
    return hashlib.sha256(" ".join(normalise_words(term)).encode("utf-8")).hexdigest()


def load_hashes(path: Path) -> frozenset[str]:
    """Read one hash per line; blank lines and lines starting with # are ignored."""
    hashes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip().lower()
        if entry and not entry.startswith("#"):
            hashes.add(entry)
    return frozenset(hashes)


def _ngram_hashes(words: Sequence[str]) -> Iterator[str]:
    for size in range(1, MAX_TERM_WORDS + 1):
        for start in range(len(words) - size + 1):
            yield hashlib.sha256(" ".join(words[start : start + size]).encode("utf-8")).hexdigest()


def matching_hashes(text: str, guarded: frozenset[str]) -> list[str]:
    """Return the guarded hashes found in text, sorted and without duplicates.

    The text is read four ways: as written, with camel-case words split, with possessives
    dropped, and with both.
    """
    found: set[str] = set()
    for split_camel in (False, True):
        for drop_possessive in (False, True):
            words = normalise_words(text, split_camel=split_camel, drop_possessive=drop_possessive)
            found.update(_ngram_hashes(words))
    return sorted(found & guarded)


def _candidate_files(root: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted(name for name in listing.stdout.decode("utf-8").split("\0") if name)


class _Unreadable(Exception):
    """A file that looks like text but cannot be decoded, so the guard cannot judge it."""


class _HoldsNul(Exception):
    """Text that holds NUL bytes; carries what could be read of it with them removed."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


def _read_text(path: Path) -> str | None:
    """The text of a file; None for a binary file.

    Raises `_Unreadable` for undecodable text and `_HoldsNul` for valid UTF-8 that holds NUL
    bytes.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for mark, encoding in _BYTE_ORDER_MARKS:
        if data.startswith(mark):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError as error:
                raise _Unreadable from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        if b"\0" in data:
            return None
        raise _Unreadable from error
    if "\0" in text:
        raise _HoldsNul(text.replace("\0", ""))
    return text


def scan(root: Path, guarded: frozenset[str]) -> list[str]:
    """Return one finding per file name or text file that matches a guarded term.

    Findings carry a hash prefix and never the matched words.
    """
    findings: list[str] = []
    for name in _candidate_files(root):
        hits = matching_hashes(name, guarded)
        try:
            text = _read_text(root / name)
        except _Unreadable:
            findings.append(f"{name}: not valid text the guard can read")
            text = None
        except _HoldsNul as nul:
            findings.append(f"{name}: text that holds NUL bytes, which the guard cannot trust")
            text = nul.text
        if text is not None:
            hits = sorted(set(hits) | set(matching_hashes(text, guarded)))
        findings.extend(f"{name}: matches guarded term {digest[:12]}" for digest in hits)
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """Scan a checkout; exit 1 on any finding."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository to scan")
    parser.add_argument(
        "--hashes",
        type=Path,
        action="append",
        help="hash list to use; repeatable (default: the shipped list)",
    )
    parser.add_argument("--hash", metavar="TERM", help="print the hash of a term and exit")
    args = parser.parse_args(argv)
    if args.hash is not None:
        length = len(normalise_words(args.hash))
        if length == 0:
            sys.stderr.write("a term needs at least one letter or digit; this one has none\n")
            return 2
        if length > MAX_TERM_WORDS:
            sys.stderr.write(
                f"a term is at most {MAX_TERM_WORDS} words, since longer terms are never "
                f"compared; this one has {length}\n"
            )
            return 2
        sys.stdout.write(term_hash(args.hash) + "\n")
        return 0
    guarded: frozenset[str] = frozenset()
    for path in args.hashes or [DEFAULT_HASH_FILE]:
        guarded |= load_hashes(path)
    findings = scan(args.root, guarded)
    for finding in findings:
        sys.stdout.write(finding + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
