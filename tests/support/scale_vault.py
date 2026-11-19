"""Generate a schema-valid vault of a requested size from invented words.

Nothing generated is committed; benchmarks and the optional performance test call `generate`
into a temporary directory. The result depends only on `notes` and `seed`.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

_SYLLABLES = (
    "ba", "ke", "lo", "mi", "nu", "pa", "re", "si", "to", "vu", "zi", "dra", "fle", "gro", "pli",
    "stu", "quo", "wen", "yar", "jex",
)  # fmt: skip
_MODALITIES = ("must", "must-not", "may", "definition", "procedure", "outcome")
_CONFIDENCES = ("high", "medium", "low")
_OWNERSHIPS = ("player", "shared")
_VISIBILITIES = ("public", "hidden", "owner")
_SOURCE_KEY = "scale"
_RETRIEVED = "2026-11-19"
_MINIMUM_NOTES = 10


@dataclass
class _Note:
    id: str
    type: str
    title: str
    status: str
    body: str
    folder: str
    scalars: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)


def generate(root: Path, notes: int, seed: int) -> None:
    """Write a lint-clean vault of exactly `notes` notes under `root`.

    The vault holds one source note and a span file with one single-sentence span per other note.
    Phases form a `precedes` chain; clauses sit in phases, repeat each other through `references`
    and define every term, keyword and zone, so no note is an orphan.
    """
    if notes < _MINIMUM_NOTES:
        raise ValueError(f"a scale vault needs at least {_MINIMUM_NOTES} notes, got {notes}")
    rng = random.Random(seed)
    sentences = [_sentence(rng) for _ in range(notes - 1)]
    generated = _build_notes(rng, notes - 1)
    for index, note in enumerate(generated, start=1):
        note.lists["source"] = [f"{_SOURCE_KEY}#{index}"]

    text, offsets = _source_text(sentences)
    for note in generated:
        _write(root / note.folder / f"{note.id}.md", _render(note))
    _write(root / "sources" / "src.scale.md", _render(_source_note(text)))
    _write(root / "spans" / f"{_SOURCE_KEY}.jsonl", _span_file(sentences, offsets))
    _write(root / "_ruleset.yaml", _RULESET)


_RULESET = f"""ruleset: scale
schemaVersion: 1
sources:
  - key: {_SOURCE_KEY}
    note: src.{_SOURCE_KEY}
scope:
  {_SOURCE_KEY}: []
extensions: {{}}
"""


def _word(rng: random.Random) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 3)))


def _sentence(rng: random.Random) -> str:
    words = [_word(rng) for _ in range(rng.randint(6, 12))]
    return " ".join(words).capitalize() + "."


def _title(rng: random.Random) -> str:
    return " ".join(_word(rng) for _ in range(rng.randint(2, 3))).capitalize()


def _build_notes(rng: random.Random, count: int) -> list[_Note]:
    phase_count = max(3, count // 60)
    remaining = count - phase_count
    clause_count = max(1, remaining * 6 // 10)
    term_count = remaining * 2 // 10
    keyword_count = remaining // 10
    zone_count = remaining - clause_count - term_count - keyword_count

    phases = [_note(rng, "ph", "phase", "model/phases", index) for index in range(phase_count)]
    for earlier, later in itertools.pairwise(phases):
        earlier.edges["precedes"] = [later.id]

    clauses = [
        _note(rng, "cl", "clause", "clauses", index, scalars={"modality": rng.choice(_MODALITIES)})
        for index in range(clause_count)
    ]
    definables = [_note(rng, "term", "term", "terms", index) for index in range(term_count)]
    definables += [
        _note(rng, "kw", "keyword", "model/keywords", index) for index in range(keyword_count)
    ]
    definables += [
        _note(
            rng,
            "zone",
            "zone",
            "model/zones",
            index,
            scalars={
                "ownership": rng.choice(_OWNERSHIPS),
                "visibility": rng.choice(_VISIBILITIES),
                "ordered": rng.choice(("true", "false")),
            },
        )
        for index in range(zone_count)
    ]

    for position, clause in enumerate(clauses):
        clause.edges["part-of"] = [phases[position % phase_count].id]
        other = clauses[(position * 7 + 3) % clause_count]
        if other is not clause:
            clause.edges["references"] = [other.id]
    for position, definable in enumerate(definables):
        owner = clauses[position % clause_count]
        owner.edges.setdefault("defines", []).append(definable.id)
    return phases + clauses + definables


def _note(
    rng: random.Random,
    prefix: str,
    type_name: str,
    folder: str,
    index: int,
    scalars: dict[str, str] | None = None,
) -> _Note:
    title = _title(rng)
    note = _Note(
        id=f"{prefix}.n{index:05d}",
        type=type_name,
        title=title,
        status="mapped",
        body=f"{_sentence(rng)} {_sentence(rng)}",
        folder=folder,
        scalars={"confidence": rng.choice(_CONFIDENCES), **(scalars or {})},
    )
    note.lists["tags"] = sorted({_word(rng), _word(rng)})
    return note


def _source_text(sentences: list[str]) -> tuple[str, list[int]]:
    """The extracted text the source note describes, and each rule's character offset in it."""
    text = ""
    offsets: list[int] = []
    for number, sentence in enumerate(sentences, start=1):
        offsets.append(len(text))
        text += f"{number}.  {sentence}\n\n"
    return text, offsets


def _source_note(text: str) -> _Note:
    raw = text.encode("utf-8")
    note = _Note(
        id=f"src.{_SOURCE_KEY}",
        type="source",
        title="Generated scale rules",
        status="structural",
        body="A generated source whose rules are invented words, used to size benchmarks.",
        folder="sources",
        scalars={
            "kind": "edition",
            "key": _SOURCE_KEY,
            "label": "Generated scale rules",
            "edition": "first",
            "retrieved": json.dumps(_RETRIEVED),
            "bytes": str(len(raw)),
            "sha256": json.dumps(hashlib.sha256(raw).hexdigest()),
            "extractor": "text",
            "segmenter": "numbered",
            "spans": json.dumps(f"spans/{_SOURCE_KEY}.jsonl"),
        },
    )
    note.scalars["confidence"] = "high"
    return note


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _editorial(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalise(text))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _span_file(sentences: list[str], offsets: list[int]) -> str:
    lines: list[str] = []
    for position, (sentence, offset) in enumerate(zip(sentences, offsets, strict=True)):
        number = position + 1
        record = {
            "editorialSha256": _digest(_editorial(sentence)),
            "kind": "span",
            "locator": str(number),
            "offset": offset,
            "page": 1 + position // 40,
            "parent": None,
            "position": position,
            "sentences": 1,
            "sha256": _digest(_normalise(sentence)),
            "source": _SOURCE_KEY,
            "span": f"{_SOURCE_KEY}#{number}",
            "words": len(sentence.split()),
        }
        lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def _render(note: _Note) -> str:
    # Invented words carry no characters that YAML would read as syntax, so nothing is quoted.
    lines = [
        "---",
        f"id: {note.id}",
        f"type: {note.type}",
        f"title: {note.title}",
        f"status: {note.status}",
        f"confidence: {note.scalars.get('confidence', 'high')}",
        "rev: 1",
        "schema: 1",
    ]
    lines += [f"{key}: {value}" for key, value in note.scalars.items() if key != "confidence"]
    for key in sorted(note.lists):
        lines.append(f"{key}:")
        lines += [f"  - {json.dumps(item)}" for item in note.lists[key]]
    for key in sorted(note.edges):
        lines.append(f"{key}:")
        lines += [f'  - "[[{target}]]"' for target in note.edges[key]]
    lines += ["---", "", f"# {note.title}", "", note.body, ""]
    return "\n".join(lines)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
