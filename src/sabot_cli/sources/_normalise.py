"""The two hash normalisers.

Both are English-specific: the pronoun table and the quote characters assume English prose.
Every module that hashes text calls these, so a span hashes the same in every mode.
"""

import hashlib
import re
import unicodedata

HASH_LENGTH = 16

# Dotted numbers with an optional lettered tail (`4.2a`), or a bare number with its trailing dot.
LEADING_NUMBER = re.compile(r"^(?:\d+(?:\.\d+)+[a-z]?\.?|\d+\.)(?=\s|$)\s*")

_PRONOUNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (r"\b(?:his or her|his/her|his|her)\b", "their"),
        (r"\bhers\b", "theirs"),
        (r"\b(?:he or she|he/she|he|she)\b", "they"),
        (r"\b(?:himself|herself)\b", "themself"),
        (r"\bhim\b", "them"),
    )
)
_QUOTE_CHARACTERS = (
    "'\"`"
    "\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
    "\N{SINGLE LOW-9 QUOTATION MARK}\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}"
    "\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
    "\N{DOUBLE LOW-9 QUOTATION MARK}\N{DOUBLE HIGH-REVERSED-9 QUOTATION MARK}"
)
_QUOTES = dict.fromkeys(map(ord, _QUOTE_CHARACTERS), None)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def strip_leading_number(text: str) -> str:
    """Remove a leading rule number such as `4.2a` or `7.` together with the space after it."""
    return LEADING_NUMBER.sub("", text.lstrip(), count=1)


def normalise(text: str) -> str:
    """NFKC, leading number removed, whitespace collapsed, case folded."""
    return _collapse(strip_leading_number(unicodedata.normalize("NFKC", text))).casefold()


def editorial(text: str) -> str:
    """`normalise`, then pronouns unified and quotes and punctuation dropped.

    A change that only swaps a pronoun or a punctuation mark leaves this form unchanged, which
    is how an editorial edit is told from a substantive one without the source text.
    """
    folded = normalise(text)
    for pattern, replacement in _PRONOUNS:
        folded = pattern.sub(replacement, folded)
    unquoted = folded.translate(_QUOTES)
    spaced = "".join(" " if unicodedata.category(c).startswith("P") else c for c in unquoted)
    return _collapse(spaced)


def digest(normalised: str) -> str:
    """The first 16 hex characters of the SHA-256 of an already normalised string."""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:HASH_LENGTH]
