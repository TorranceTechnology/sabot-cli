"""Plant exactly one named defect into a copy of the Driftwood fixture.

Three groups of defects share one entry point:

- lint classes: `root` is a copy of the vault (the directory holding `_ruleset.yaml`);
  the defect name is the lint class name it must trip;
- boundaries defects: `root` is a directory holding a copy of `sheet.boundaries.jsonl` and
  `sheet.txt`, for example a copy of the fixture directory;
- segmenter defects: `root` is a directory holding a copy of `rules-e1.txt`.

Callers copy the fixture first; planting edits in place and refuses nothing about the copy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_DELIMITER = "---"
_HOST = "og.high-water"


@dataclass(frozen=True)
class PlantedDefect:
    """What one planting changed and where a checker is expected to report it."""

    defect: str
    path: PurePosixPath
    note_id: str | None
    # Ids a finding is expected on. Empty when the finding is reported by path (an unparseable
    # note has no readable id) or lands on every note that cites a source.
    expected_notes: tuple[str, ...] = ()
    also_changed: tuple[PurePosixPath, ...] = ()


class _NoteFile:
    """A note file edited line by line, so planted edits never depend on a YAML library."""

    def __init__(self, vault_root: Path, note_id: str) -> None:
        matches = sorted(vault_root.rglob(f"{note_id}.md"))
        if len(matches) != 1:
            raise FileNotFoundError(f"expected one note named {note_id}, found {len(matches)}")
        self.path = matches[0]
        self.lines = self.path.read_bytes().decode("utf-8").split("\n")
        self._closing = self.lines.index(_DELIMITER, 1)

    def set_scalar(self, key: str, rendered_value: str) -> None:
        index = self._key_index(key)
        self.lines[index] = f"{key}: {rendered_value}"

    def rename_id(self, new_id: str) -> None:
        self.set_scalar("id", new_id)

    def remove_key(self, key: str) -> None:
        start = self._key_index(key)
        end = start + 1
        while self.lines[end].startswith("  "):
            end += 1
        del self.lines[start:end]
        self._closing -= end - start

    def add_list_item(self, key: str, item: str) -> None:
        """Append an item to a block list, creating the list when the key is absent."""
        try:
            start = self._key_index(key)
        except KeyError:
            self.lines[self._closing : self._closing] = [f"{key}:", f"  - {item}"]
            self._closing += 2
            return
        end = start + 1
        while self.lines[end].startswith("  "):
            end += 1
        self.lines.insert(end, f"  - {item}")
        self._closing += 1

    def add_scalar(self, key: str, rendered_value: str) -> None:
        self.lines.insert(self._closing, f"{key}: {rendered_value}")
        self._closing += 1

    def replace_in_front_matter(self, old: str, new: str) -> None:
        for index in range(1, self._closing):
            if old in self.lines[index]:
                self.lines[index] = self.lines[index].replace(old, new)
                return
        raise ValueError(f"{old!r} not found in the front matter of {self.path.name}")

    def append_to_body(self, text: str) -> None:
        while self.lines and self.lines[-1] == "":
            self.lines.pop()
        self.lines += ["", text, ""]

    def save(self, destination: Path | None = None) -> Path:
        target = destination or self.path
        target.write_bytes("\n".join(self.lines).encode("utf-8"))
        return target

    def _key_index(self, key: str) -> int:
        for index in range(1, self._closing):
            if self.lines[index].startswith(f"{key}:"):
                return index
        raise KeyError(key)


def _relative(root: Path, path: Path) -> PurePosixPath:
    return PurePosixPath(path.relative_to(root).as_posix())


def _edit_host(vault_root: Path, defect: str, edit: Callable[[_NoteFile], None]) -> PlantedDefect:
    note = _NoteFile(vault_root, _HOST)
    edit(note)
    return PlantedDefect(defect, _relative(vault_root, note.save()), _HOST, (_HOST,))


def _edit_note(
    vault_root: Path,
    defect: str,
    note_id: str,
    edit: Callable[[_NoteFile], None],
    expected: tuple[str, ...] | None = None,
) -> PlantedDefect:
    note = _NoteFile(vault_root, note_id)
    edit(note)
    names = (note_id,) if expected is None else expected
    return PlantedDefect(defect, _relative(vault_root, note.save()), note_id, names)


def _rename_host(vault_root: Path, defect: str, new_id: str) -> PlantedDefect:
    """Change the id and the file name together so only the id rule under test fails."""
    note = _NoteFile(vault_root, _HOST)
    old_path = note.path
    note.rename_id(new_id)
    destination = old_path.with_name(f"{new_id}.md")
    note.save(destination)
    old_path.unlink()
    return PlantedDefect(defect, _relative(vault_root, destination), new_id, (new_id,))


def _id_reserved_name(vault_root: Path) -> PlantedDefect:
    """Give the `win.` extension type the prefix `aux.` and rename its one note to match.

    Prefix, id and file name agree, so no other id rule trips; only the device name does.
    """
    ruleset = vault_root / "_ruleset.yaml"
    text = ruleset.read_bytes().decode("utf-8")
    if 'prefix: "win."' not in text:
        raise ValueError("the ruleset has no win. node type to re-prefix")
    ruleset.write_bytes(text.replace('prefix: "win."', 'prefix: "aux."', 1).encode("utf-8"))
    linking = _NoteFile(vault_root, "cl.formats")
    linking.replace_in_front_matter("[[win.first-to-target]]", "[[aux.first-to-target]]")
    linking_path = linking.save()
    note = _NoteFile(vault_root, "win.first-to-target")
    old_path = note.path
    note.rename_id("aux.first-to-target")
    destination = note.save(old_path.with_name("aux.first-to-target.md"))
    old_path.unlink()
    return PlantedDefect(
        "id-reserved-name",
        _relative(vault_root, destination),
        "aux.first-to-target",
        ("aux.first-to-target",),
        also_changed=(_relative(vault_root, ruleset), _relative(vault_root, linking_path)),
    )


def _id_duplicate(vault_root: Path) -> PlantedDefect:
    """Copy the host into another folder; two files with one stem and one id."""
    note = _NoteFile(vault_root, _HOST)
    copy = vault_root / "clauses" / note.path.name
    note.save(copy)
    return PlantedDefect("id-duplicate", _relative(vault_root, copy), _HOST, (_HOST,))


def _orphan(vault_root: Path) -> PlantedDefect:
    path = vault_root / "terms" / "term.stray.md"
    path.write_bytes(
        "\n".join(
            [
                _DELIMITER,
                "id: term.stray",
                "type: term",
                "title: Stray term",
                "status: mapped",
                "confidence: high",
                "rev: 1",
                "schema: 1",
                "source:",
                '  - "dw1#1.1"',
                _DELIMITER,
                "",
                "# Stray term",
                "",
                "A defined word that nothing else in the vault mentions.",
                "",
            ]
        ).encode("utf-8")
    )
    return PlantedDefect("orphan", _relative(vault_root, path), "term.stray", ("term.stray",))


def _ruleset_invalid(vault_root: Path) -> PlantedDefect:
    path = vault_root / "_ruleset.yaml"
    text = path.read_bytes().decode("utf-8")
    anchor = "    note: src.dw1\n"
    if anchor not in text:
        raise ValueError("the ruleset has no dw1 source entry to extend")
    path.write_bytes(
        text.replace(anchor, anchor + "  - key: zz9\n    note: src.zz9\n", 1).encode("utf-8")
    )
    return PlantedDefect("ruleset-invalid", _relative(vault_root, path), None)


def _transaction_incomplete(vault_root: Path) -> PlantedDefect:
    """Leave a journal directory behind, as a write killed part-way does."""
    journal = vault_root / ".sabot-txn-planted"
    journal.mkdir()
    (journal / "journal.json").write_bytes(b"{}\n")
    return PlantedDefect("transaction-incomplete", _relative(vault_root, journal), None)


def register_second_edition(vault_root: Path) -> tuple[PurePosixPath, PurePosixPath]:
    """Register dw2 as the successor of dw1, in the ruleset and on its source note.

    The shipped vault leaves dw2 unregistered so that a strict lint of edition one is clean;
    migration tests and the `source-superseded` defect both start from this call.
    """
    ruleset = vault_root / "_ruleset.yaml"
    text = ruleset.read_bytes().decode("utf-8")
    anchor = "    note: src.dw1\n"
    if anchor not in text:
        raise ValueError("the ruleset has no dw1 source entry to extend")
    ruleset.write_bytes(
        text.replace(
            anchor, anchor + "  - key: dw2\n    note: src.dw2\n    supersedes: dw1\n", 1
        ).encode("utf-8")
    )
    note = _NoteFile(vault_root, "src.dw2")
    note.add_scalar("supersedes", "dw1")
    return _relative(vault_root, ruleset), _relative(vault_root, note.save())


def _source_superseded(vault_root: Path) -> PlantedDefect:
    ruleset, source = register_second_edition(vault_root)
    # Every note that cites dw1 is reported, so no single expected note is named.
    return PlantedDefect("source-superseded", ruleset, None, also_changed=(source,))


def _mark_stale(note: _NoteFile) -> None:
    note.add_scalar("stale", "true")
    note.add_scalar("stale-reason", "editorial")


def _long_quotation() -> str:
    quoted = "the tide is the slow clock that every boat in every harbour must eventually obey"
    assert len(quoted) >= 60
    return f'A margin remark reads "{quoted}".'


def _conflict_without_question(vault_root: Path) -> PlantedDefect:
    """Point the open question away from the conflicting spans; the finding lands on the clauses."""
    note = _NoteFile(vault_root, "oq.tide-overflow")
    note.remove_key("source")
    note.add_list_item("source", '"dw1#7.1"')
    return PlantedDefect(
        "conflict-without-question",
        _relative(vault_root, note.save()),
        "oq.tide-overflow",
        ("cl.tide-stay", "cl.tide-wrap"),
    )


_VAULT_DEFECTS: dict[str, Callable[[Path], PlantedDefect]] = {
    "frontmatter-invalid": lambda root: _edit_note(
        root,
        "frontmatter-invalid",
        _HOST,
        lambda n: n.set_scalar("title", '"unterminated'),
        (),
    ),
    "required-missing": lambda root: _edit_host(
        root, "required-missing", lambda n: n.remove_key("confidence")
    ),
    "id-malformed": lambda root: _rename_host(root, "id-malformed", "og.high_water"),
    "id-file-mismatch": lambda root: _edit_note(
        root,
        "id-file-mismatch",
        _HOST,
        lambda n: n.rename_id("og.high-water-mark"),
        ("og.high-water-mark",),
    ),
    "id-duplicate": _id_duplicate,
    "id-prefix-mismatch": lambda root: _rename_host(root, "id-prefix-mismatch", "kw.high-water"),
    "id-reserved-name": _id_reserved_name,
    "type-unknown": lambda root: _edit_host(
        root, "type-unknown", lambda n: n.set_scalar("type", "tide-pattern")
    ),
    "enum-invalid": lambda root: _edit_host(
        root, "enum-invalid", lambda n: n.set_scalar("confidence", "certain")
    ),
    "attribute-unknown": lambda root: _edit_host(
        root, "attribute-unknown", lambda n: n.add_scalar("flavour", "salty")
    ),
    "status-requirement": lambda root: _edit_host(
        root, "status-requirement", lambda n: n.remove_key("source")
    ),
    "type-requirement": lambda root: _edit_note(
        root, "type-requirement", "zone.wreck", lambda n: n.remove_key("ordered")
    ),
    "span-unknown": lambda root: _edit_host(
        root, "span-unknown", lambda n: n.replace_in_front_matter('"dw1#6.6"', '"dw1#99.9"')
    ),
    "span-subtree-misused": lambda root: _edit_host(
        root,
        "span-subtree-misused",
        lambda n: n.replace_in_front_matter('"dw1#6.6"', '"dw1#6.6/*"'),
    ),
    "link-malformed": lambda root: _edit_host(
        root,
        "link-malformed",
        lambda n: n.replace_in_front_matter('"[[zone.harbour]]"', '"zone.harbour"'),
    ),
    "link-dangling": lambda root: _edit_host(
        root, "link-dangling", lambda n: n.add_list_item("references", '"[[ch.no-such-note]]"')
    ),
    "edge-domain": lambda root: _edit_host(
        root, "edge-domain", lambda n: n.add_list_item("defines", '"[[ch.capacity]]"')
    ),
    "edge-range": lambda root: _edit_note(
        root,
        "edge-range",
        "cl.hand-limit-summary",
        lambda n: n.add_list_item("defines", '"[[cl.hand-limit]]"'),
    ),
    "inverse-authored": lambda root: _edit_host(
        root, "inverse-authored", lambda n: n.add_list_item("referenced-by", '"[[ch.capacity]]"')
    ),
    "symmetric-duplicated": lambda root: _edit_note(
        root,
        "symmetric-duplicated",
        "cl.tide-wrap",
        lambda n: n.add_list_item("conflicts-with", '"[[cl.tide-stay]]"'),
        ("cl.tide-stay", "cl.tide-wrap"),
    ),
    "precedes-cycle": lambda root: _edit_note(
        root,
        "precedes-cycle",
        "ph.moor",
        lambda n: n.add_list_item("precedes", '"[[ph.ebb]]"'),
        ("ph.ebb", "ph.flood", "ph.moor", "ph.sail"),
    ),
    "conflict-without-question": _conflict_without_question,
    "restates-conflicts-pair": lambda root: _edit_note(
        root,
        "restates-conflicts-pair",
        "cl.tide-stay",
        lambda n: n.add_list_item("restates", '"[[cl.tide-wrap]]"'),
        ("cl.tide-stay", "cl.tide-wrap"),
    ),
    "superseded-without-successor": lambda root: _edit_note(
        root,
        "superseded-without-successor",
        "cl.hand-limit-draft",
        lambda n: n.remove_key("refines"),
    ),
    "schema-version-mismatch": lambda root: _edit_host(
        root, "schema-version-mismatch", lambda n: n.set_scalar("schema", "2")
    ),
    "ruleset-invalid": _ruleset_invalid,
    "transaction-incomplete": _transaction_incomplete,
    "quotation-long": lambda root: _edit_host(
        root, "quotation-long", lambda n: n.append_to_body(_long_quotation())
    ),
    "overrides-and-exception": lambda root: _edit_note(
        root,
        "overrides-and-exception",
        "kw.buoyant",
        lambda n: n.add_list_item("exception-to", '"[[cl.beach-effect]]"'),
    ),
    "body-link": lambda root: _edit_host(
        root, "body-link", lambda n: n.append_to_body("See [[ch.capacity]] for the number.")
    ),
    "pending": lambda root: _edit_host(
        root, "pending", lambda n: n.set_scalar("status", "pending")
    ),
    "stale": lambda root: _edit_host(root, "stale", _mark_stale),
    "question-open": lambda root: _edit_note(
        root, "question-open", "oq.tide-overflow", lambda n: n.remove_key("resolved-by")
    ),
    "orphan": _orphan,
    "source-superseded": _source_superseded,
}


def _boundaries_defect(
    root: Path, defect: str, mutate: Callable[[list[dict[str, object]], int], None]
) -> PlantedDefect:
    path = root / "sheet.boundaries.jsonl"
    text_length = len((root / "sheet.txt").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutate(records, text_length)
    path.write_bytes(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
        ).encode("utf-8")
    )
    return PlantedDefect(defect, _relative(root, path), None)


def _record(records: list[dict[str, object]], locator: str) -> dict[str, object]:
    return next(r for r in records if r.get("locator") == locator)


def _gap(records: list[dict[str, object]], _length: int) -> None:
    short_tide = _record(records, "goal/short-tide")
    short_tide["end"] = int(str(short_tide["end"])) - 6


def _overlap(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal/first-to-bank")["end"] = 50


def _offset_out_of_range(records: list[dict[str, object]], length: int) -> None:
    # The footer skip record is last and nothing follows it, so no other rule is tripped.
    next(r for r in records if r.get("skip"))["end"] = length + 10


def _duplicate_boundary_locator(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal/short-tide")["locator"] = "goal/first-to-bank"


def _missing_parent(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal/short-tide")["parent"] = "no-such-parent"


def _malformed_record(records: list[dict[str, object]], _length: int) -> None:
    records.append({"locator": 5})


def _inverted_skip(records: list[dict[str, object]], _length: int) -> None:
    records.append({"end": 5, "reason": "empty", "skip": True, "start": 5})


def _malformed_locator(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal/short-tide")["locator"] = "Goal/Short Tide"


def _child_past_parent(records: list[dict[str, object]], _length: int) -> None:
    # Two blank characters separate this parent from the next span, so reaching them adds no gap.
    _record(records, "goal/full-tide")["end"] = 125


def _parent_of_itself(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal")["parent"] = "goal"


def _page_goes_back(records: list[dict[str, object]], _length: int) -> None:
    _record(records, "goal/first-to-bank")["page"] = 2


_BOUNDARIES_DEFECTS: dict[str, Callable[[list[dict[str, object]], int], None]] = {
    "boundaries-gap": _gap,
    "boundaries-overlap": _overlap,
    "boundaries-offset-out-of-range": _offset_out_of_range,
    "boundaries-duplicate-locator": _duplicate_boundary_locator,
    "boundaries-missing-parent": _missing_parent,
    "boundaries-record-malformed": _malformed_record,
    "boundaries-offset-order": _inverted_skip,
    "boundaries-locator-malformed": _malformed_locator,
    "boundaries-child-outside-parent": _child_past_parent,
    "boundaries-parent-cycle": _parent_of_itself,
    "boundaries-page-decreasing": _page_goes_back,
}


def _renumber_rule(root: Path, defect: str, old_line_start: str, new_number: str) -> PlantedDefect:
    path = root / "rules-e1.txt"
    text = path.read_bytes().decode("utf-8")
    pattern = re.compile(rf"^{re.escape(old_line_start)}(?=\s)", re.MULTILINE)
    if not pattern.search(text):
        raise ValueError(f"rule {old_line_start} not found in {path.name}")
    path.write_bytes(pattern.sub(new_number, text, count=1).encode("utf-8"))
    return PlantedDefect(defect, _relative(root, path), None)


_SEGMENTER_DEFECTS: dict[str, Callable[[Path], PlantedDefect]] = {
    # Rule 4.4 takes the number of the rule just before it. Any duplicate also sits out of strict
    # order, so a checker that reads "monotone" as strictly increasing may name both defects.
    "segmenter-duplicate-locator": lambda root: _renumber_rule(
        root, "segmenter-duplicate-locator", "4.4", "4.3"
    ),
    # Rule 4.3 becomes 4.6, a number no other rule has, so only the order is wrong.
    "segmenter-non-monotone": lambda root: _renumber_rule(
        root, "segmenter-non-monotone", "4.3", "4.6"
    ),
}

LINT_DEFECTS: tuple[str, ...] = tuple(_VAULT_DEFECTS)
BOUNDARIES_DEFECTS: tuple[str, ...] = tuple(_BOUNDARIES_DEFECTS)
SEGMENTER_DEFECTS: tuple[str, ...] = tuple(_SEGMENTER_DEFECTS)


def defect_names() -> tuple[str, ...]:
    """Every defect `plant` accepts: lint classes, then boundaries, then segmenter defects."""
    return LINT_DEFECTS + BOUNDARIES_DEFECTS + SEGMENTER_DEFECTS


def plant(vault_root: Path, defect: str) -> PlantedDefect:
    """Apply one named defect under `vault_root` and report what changed."""
    if defect in _VAULT_DEFECTS:
        return _VAULT_DEFECTS[defect](vault_root)
    if defect in _BOUNDARIES_DEFECTS:
        return _boundaries_defect(vault_root, defect, _BOUNDARIES_DEFECTS[defect])
    if defect in _SEGMENTER_DEFECTS:
        return _SEGMENTER_DEFECTS[defect](vault_root)
    raise ValueError(f"unknown defect {defect!r}; known: {', '.join(defect_names())}")
