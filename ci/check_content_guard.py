"""Refuse tracked text files whose words match a guarded term.

Claim: no tracked text file, and no tracked file name, contains a term whose hash is listed.
Exercises: every lower-cased word n-gram of 1 to 4 words in each file against the hash lists.
Environment: Python 3.12 or later and git on PATH; no network, no third-party packages.
Real-artifact probe: run over the repository checkout; tests/test_content_guard.py plants a term.
Refusal twin: tests/test_content_guard.py::test_a_planted_term_is_refused.

The list stores SHA-256 hashes of terms, never the terms, so the guard can ship in a public
repository without naming what it guards. A term is normalised before hashing: accents are
removed, the text is lower-cased, and it is split into words of letters and digits, which are
joined by single spaces. Hash a new term with `python ci/check_content_guard.py --hash "<term>"`
and add the output to the list.
"""

import argparse
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


def normalise_words(text: str) -> list[str]:
    """Split text into lower-cased, accent-free words of letters and digits."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
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
    """Return the guarded hashes found in text, sorted and without duplicates."""
    return sorted(set(_ngram_hashes(normalise_words(text))) & guarded)


def _candidate_files(root: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted(name for name in listing.stdout.decode("utf-8").split("\0") if name)


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(root: Path, guarded: frozenset[str]) -> list[str]:
    """Return one finding per file name or text file that matches a guarded term.

    Findings carry a hash prefix and never the matched words.
    """
    findings: list[str] = []
    for name in _candidate_files(root):
        hits = matching_hashes(name, guarded)
        text = _read_text(root / name)
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
