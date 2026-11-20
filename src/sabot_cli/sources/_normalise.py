"""The two hash normalisers.

`editorial` is English-specific: its pronoun table assumes English prose. `normalise` is not.
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


def _collapse(text: str) -> str:
    return " ".join(text.split())


def strip_leading_number(text: str) -> str:
    """Remove a leading rule number such as `4.2a` or `7.` together with the space after it."""
    return LEADING_NUMBER.sub("", text.lstrip(), count=1)


def normalise(text: str) -> str:
    """NFKC, leading number removed, whitespace collapsed, case folded."""
    return _collapse(strip_leading_number(unicodedata.normalize("NFKC", text))).casefold()


def editorial(text: str) -> str:
    """`normalise`, pronouns unified, then only the letters and digits kept.

    Every other character is removed, whitespace included, so two extractions of one document
    that differ only in spacing, hyphenation at a line break or punctuation give the same form.
    That is how an editorial edit is told from a substantive one without the source text. The
    cost is that words run together: texts differing only in where a space falls are not told
    apart.
    """
    folded = normalise(text)
    for pattern, replacement in _PRONOUNS:
        folded = pattern.sub(replacement, folded)
    return "".join(c for c in folded if _is_letter_or_digit(c))


def _is_letter_or_digit(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("L") or category == "Nd"


def digest(normalised: str) -> str:
    """The first 16 hex characters of the SHA-256 of an already normalised string."""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:HASH_LENGTH]
