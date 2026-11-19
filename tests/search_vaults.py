"""Small hand-written vaults for the search and traverse tests, whose notes are only the
properties and text those tests read."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from conftest import CliResult

RULESET = "ruleset: mini\nschemaVersion: 1\nsources: []\nscope: {}\nextensions: {}\n"


@dataclass(frozen=True)
class NoteSpec:
    """One note to write: front matter, an optional body and outgoing links by edge name."""

    id: str
    title: str
    body: str = ""
    type: str = "clause"
    status: str = "mapped"
    links: Mapping[str, Sequence[str]] = field(default_factory=dict)
    extra: Mapping[str, str] = field(default_factory=dict)


def render(note: NoteSpec) -> str:
    """The note's Markdown text; a JSON string is a valid YAML double-quoted scalar."""
    lines = [
        "---",
        f"id: {note.id}",
        f"type: {note.type}",
        f"title: {json.dumps(note.title)}",
        f"status: {note.status}",
        *(f"{key}: {value}" for key, value in note.extra.items()),
    ]
    for edge, targets in note.links.items():
        lines.append(f"{edge}:")
        lines.extend(f'  - "[[{target}]]"' for target in targets)
    lines += ["---", "", f"# {note.title}", "", note.body, ""]
    return "\n".join(lines)


def write_vault(root: Path, notes: Sequence[NoteSpec], *, reverse: bool = False) -> Path:
    """Write a vault holding `notes`, creating the files in the order given or its reverse."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "_ruleset.yaml").write_text(RULESET, encoding="utf-8")
    for note in reversed(notes) if reverse else notes:
        path = root / "notes" / f"{note.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render(note).encode("utf-8"))
    return root


def result_rows(result: CliResult) -> list[dict[str, object]]:
    """The `results` list of a `search` or `traverse` JSON document."""
    rows = result.json()["results"]
    assert isinstance(rows, list)
    return rows


def result_ids(result: CliResult) -> list[object]:
    """The ids of the rows of a `search` or `traverse` JSON document, in output order."""
    return [row["id"] for row in result_rows(result)]
