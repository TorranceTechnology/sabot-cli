"""Full-text search over the notes of a vault."""

from collections.abc import Sequence

from sabot_cli.search import _fulltext
from sabot_cli.search._model import Hit
from sabot_cli.search._snippet import snippet
from sabot_cli.search._tokenise import query_phrases, searchable_body, words
from sabot_cli.vault import Note, Vault

SCORE_DECIMALS = 4


def full_text(vault: Vault, terms: Sequence[str], require_all: bool, limit: int) -> tuple[Hit, ...]:
    """Rank the notes of `vault` against `terms`, best first, and keep the first `limit`.

    The title counts twice the body. Terms are alternatives unless `require_all`; a term that
    holds several words is a phrase. IDF and length statistics come from the notes of `vault`
    alone, so a caller that filters first gets scores for the filtered set. Notes with equal
    rounded scores are ordered by id, and ranks run from 1 without gaps.
    """
    phrases = query_phrases(terms)
    documents = {
        note.id: _fulltext.Document(note.id, words(_title(note)), words(_body(note)))
        for note in sorted(vault.notes.values(), key=lambda note: note.id)
    }
    scored = sorted(
        (
            (round(match.score, SCORE_DECIMALS), match.key)
            for match in _fulltext.rank(list(documents.values()), phrases, require_all=require_all)
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return tuple(
        Hit(
            note=vault.notes[key],
            rank=position,
            score=score,
            snippet=snippet(_title(vault.notes[key]), _body(vault.notes[key]), phrases),
        )
        for position, (score, key) in enumerate(scored[: max(limit, 0)], start=1)
    )


def _title(note: Note) -> str:
    title = note.properties.get("title")
    return title if isinstance(title, str) else ""


def _body(note: Note) -> str:
    return searchable_body(note.body, _title(note))
