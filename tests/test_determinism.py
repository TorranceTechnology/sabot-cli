"""DET-1 and DET-2: output is the same on every run and carries no machine-specific paths."""

import json
from pathlib import Path

import pytest

from conftest import CliResult, CliRunner, ListingOrder
from invocations import COMMAND_NAMES, invocation
from sabot_cli.output import canonical_json
from sabot_cli.vault import load_vault
from source_vaults import write_text
from support.plant import plant


def _arguments(name: str, workdir: Path, *, apply_writes: bool) -> tuple[str, ...]:
    """The invocation of `name`; `migrate-edition` runs for real when `apply_writes` is set."""
    arguments = invocation(name, workdir)
    if apply_writes:
        arguments = tuple(argument for argument in arguments if argument != "--dry-run")
    return arguments


def _run_json(
    run_cli: CliRunner, name: str, vault: Path, workdir: Path, *, apply_writes: bool = False
) -> CliResult:
    arguments = _arguments(name, workdir, apply_writes=apply_writes)
    vault_options = () if name == "init" else ("--vault", str(vault))
    return run_cli(name, *arguments, *vault_options, "--json")


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_every_command_is_byte_identical_across_runs(
    name: str,
    run_cli: CliRunner,
    force_listing_order: ListingOrder,
    driftwood_vault: Path,
    driftwood_vault_twin: Path,
    tmp_path: Path,
) -> None:
    # Given two copies of the vault whose directories are listed in opposite orders
    first_work = tmp_path / "work-first"
    second_work = tmp_path / "work-second"
    first_work.mkdir()
    second_work.mkdir()
    before = _snapshot(driftwood_vault)

    # When the command runs on each, writing for real where it can
    force_listing_order(False)
    first = _run_json(run_cli, name, driftwood_vault, first_work, apply_writes=True)
    force_listing_order(True)
    second = _run_json(run_cli, name, driftwood_vault_twin, second_work, apply_writes=True)

    # Then stdout, the exit code and every file left behind are byte-identical
    assert first.stdout != "", f"{name} printed nothing"
    assert first.returncode == second.returncode
    assert first.stdout == second.stdout
    if name == "init":
        assert _snapshot(first_work) == _snapshot(second_work)
        assert _snapshot(first_work), "init wrote nothing"
    else:
        assert _snapshot(driftwood_vault) == _snapshot(driftwood_vault_twin)
    if name == "migrate-edition":
        assert _snapshot(driftwood_vault) != before, "migrate-edition changed nothing"


def test_lint_findings_keep_one_order_whatever_the_listing_order(
    run_cli: CliRunner,
    force_listing_order: ListingOrder,
    driftwood_vault: Path,
    driftwood_vault_twin: Path,
) -> None:
    # Given two copies of a vault with several planted defects on different notes
    for vault in (driftwood_vault, driftwood_vault_twin):
        for defect in ("id-duplicate", "orphan", "conflict-without-question", "type-requirement"):
            plant(vault, defect)
        for folder in ("clauses", "terms", "rulings"):
            write_text(vault / folder / "unreadable.md", "---\nid: [\n---\n")

    # When each is linted with the directories listed in opposite orders
    force_listing_order(False)
    first = run_cli("lint", "--vault", str(driftwood_vault), "--json")
    force_listing_order(True)
    second = run_cli("lint", "--vault", str(driftwood_vault_twin), "--json")

    # Then there are several findings, and they come out in the same order
    findings = first.json()["findings"]
    assert isinstance(findings, list)
    assert len(findings) >= 3
    assert first.stdout == second.stdout


def test_lint_findings_are_sorted_by_path_class_and_message(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a vault with many findings on different notes, enough that an arbitrary order is
    # very unlikely to be the sorted one
    for defect in (
        "orphan",
        "conflict-without-question",
        "type-requirement",
        "link-dangling",
        "body-link",
        "quotation-long",
        "id-duplicate",
    ):
        plant(driftwood_vault, defect)
    for folder in ("clauses", "terms", "rulings", "model"):
        write_text(driftwood_vault / folder / "unreadable.md", "---\nid: [\n---\n")

    # When it is linted
    findings = run_cli("lint", "--vault", str(driftwood_vault), "--json").json()["findings"]

    # Then the findings come out sorted by path, class and message, whatever order the checks
    # and the file system produced them in
    keys = [(f["path"], f["class"], f["message"]) for f in findings]  # type: ignore[index, union-attr]
    assert len(keys) >= 8
    assert keys == sorted(keys)


def test_the_vault_reader_lists_notes_in_path_order_whatever_the_listing_order(
    force_listing_order: ListingOrder, driftwood_vault: Path
) -> None:
    # Given a vault read with its directories listed in name order and in reverse
    force_listing_order(False)
    forward = [
        note.path.as_posix() for note in load_vault(driftwood_vault, frozenset()).notes.values()
    ]
    force_listing_order(True)
    backward = [
        note.path.as_posix() for note in load_vault(driftwood_vault, frozenset()).notes.values()
    ]

    # When the notes are listed
    # Then both listings are the same, in path order
    assert forward == backward
    assert forward == sorted(forward)
    assert len(forward) > 10


def _every_object_lists_its_keys_in_order(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    assert keys == sorted(keys), f"keys out of order: {keys}"
    return dict(pairs)


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_every_json_document_writes_its_keys_in_sorted_order(
    name: str, run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given a command run with --json
    result = _run_json(run_cli, name, driftwood_vault, tmp_path)
    assert result.stdout != ""

    # When its document is read with the order its keys were written in
    # Then every object, nested ones included, lists its keys sorted
    json.loads(result.stdout, object_pairs_hook=_every_object_lists_its_keys_in_order)


def test_canonical_json_sorts_nested_keys() -> None:
    # Given a document whose keys are given out of order, at two depths
    document = {"b": 1, "a": {"d": [{"z": 1, "y": 2}], "c": 2}}

    # When it is written
    text = canonical_json(document)

    # Then the keys are sorted at every depth and the text ends in one newline
    assert text.index('"a"') < text.index('"b"')
    assert text.index('"c"') < text.index('"d"')
    assert text.index('"y"') < text.index('"z"')
    assert text.endswith("}\n")


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_outputs_carry_no_absolute_paths(
    name: str, run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given a vault under a temporary directory
    # When the command runs in both output formats
    json_result = _run_json(run_cli, name, driftwood_vault, tmp_path)
    arguments = invocation(name, tmp_path / "text")
    vault_options = () if name == "init" else ("--vault", str(driftwood_vault))
    text_result = run_cli(name, *arguments, *vault_options)

    # Then neither stream names the temporary directory, spelled as the platform writes it,
    # with forward slashes, or as JSON escapes it
    spellings = {str(tmp_path), tmp_path.as_posix(), json.dumps(str(tmp_path))[1:-1]}
    for output in (json_result.stdout, text_result.stdout, text_result.stderr):
        for spelling in spellings:
            assert spelling not in output
