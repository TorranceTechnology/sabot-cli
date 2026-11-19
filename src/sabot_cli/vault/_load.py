"""Build a `Vault` from a directory of Markdown notes."""

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from sabot_cli.vault import _frontmatter, _links, _walk
from sabot_cli.vault._model import Link, Note, Problem, Vault

FRONTMATTER_INVALID = "frontmatter-invalid"
ID_DUPLICATE = "id-duplicate"


def load_vault(root: Path, link_properties: frozenset[str]) -> Vault:
    """Read every note under `root`.

    Properties named in `link_properties` are parsed into `Note.links`. A note whose front
    matter cannot be read becomes a `Problem` and is absent from `notes`. When two notes carry
    one id, the first in path order is kept and the later one is reported as a `Problem`.
    A note whose `id` is missing or not text is keyed by its file name so a checker can still
    see it and report the missing id.
    """
    notes: dict[str, Note] = {}
    problems: list[Problem] = []
    for file in _walk.note_files(root):
        relative = PurePosixPath(file.relative_to(root).as_posix())
        try:
            text = file.read_bytes().decode("utf-8-sig")
            properties, body = _frontmatter.parse(text)
        except (OSError, UnicodeDecodeError) as error:
            reason = (
                "is not valid UTF-8" if isinstance(error, UnicodeDecodeError) else "is unreadable"
            )
            problems.append(Problem(relative, FRONTMATTER_INVALID, f"the file {reason}"))
            continue
        except _frontmatter.FrontMatterError as error:
            problems.append(Problem(relative, FRONTMATTER_INVALID, str(error)))
            continue
        declared = properties.get("id")
        note_id = declared if isinstance(declared, str) and declared else file.stem
        if note_id in notes:
            first = notes[note_id].path.as_posix()
            problems.append(
                Problem(relative, ID_DUPLICATE, f"the id {note_id!r} is already used by {first}")
            )
            continue
        notes[note_id] = Note(
            id=note_id,
            path=relative,
            properties=properties,
            body=body,
            links=_links_of(properties, link_properties),
        )
    return Vault(root=root, notes=notes, problems=tuple(problems))


def _links_of(
    properties: Mapping[str, object], link_properties: frozenset[str]
) -> tuple[Link, ...]:
    links: list[Link] = []
    for name, value in properties.items():
        if name not in link_properties or value is None:
            continue
        for item in value if isinstance(value, list) else [value]:
            raw = item if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str)
            links.append(
                Link(name, _links.parse_wikilink(raw) if isinstance(item, str) else None, raw)
            )
    return tuple(links)
