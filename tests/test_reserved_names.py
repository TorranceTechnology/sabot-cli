"""Windows reserves device names as file names, so no note id, file or tracked path may use one."""

import subprocess
from pathlib import Path

import pytest

from conftest import CliRunner
from sabot_cli.rules import load_schema
from sabot_cli.vault.paths import reserved_device_name, windows_unusable_name
from support.filesystem import requires_device_file_names

REPOSITORY = Path(__file__).parent.parent


@pytest.mark.parametrize(
    "name",
    [
        "con",
        "CON",
        "Con.x.md",
        "prn.md",
        "aux.thing",
        "nul",
        "com1",
        "COM9.x",
        "lpt1.x",
        "LPT9",
        "com\u00b9",
        "COM\u00b2.x",
        "lpt\u00b3.md",
        "conin$",
        "CONOUT$.x",
        "con .md",
        "aux  ",
    ],
)
def test_a_device_name_before_the_first_dot_is_reserved(name: str) -> None:
    # Given a file name or id that starts with a device name, in any letter case
    # When it is judged
    # Then it is reserved, whatever follows the first dot
    assert reserved_device_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "cst.x",
        "console.md",
        "con-x.md",
        "com0",
        "com10.md",
        "lpt0",
        "x.con",
        "x.con.md",
        "auxiliary",
        "com\u2074",
        "conin",
        "con$",
    ],
)
def test_other_names_are_not_reserved(name: str) -> None:
    # Given names that merely resemble a device name
    # When they are judged
    # Then none is reserved: the first dot-separated part must be the device name itself
    assert not reserved_device_name(name)


def test_no_node_type_prefix_of_the_schema_is_a_device_name() -> None:
    # Given the prefix of every fixed node type, which starts every note id of that type
    prefixes = [node_type.prefix for node_type in load_schema().node_types.values()]

    # When each is judged as a file name
    # Then none is reserved, so a note named by its id can be checked out on Windows
    assert [prefix for prefix in prefixes if reserved_device_name(prefix)] == []


@requires_device_file_names
def test_a_note_file_named_for_a_device_is_reported(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a note whose file is `Nul.stray.md`, with an id that matches the file but not a type
    (driftwood_vault / "terms" / "Nul.stray.md").write_text(
        "---\nid: Nul.stray\ntype: term\ntitle: Stray\nstatus: structural\nconfidence: high\n"
        "rev: 1\nschema: 1\n---\n\n# Stray\n\nNothing links here.\n",
        encoding="utf-8",
    )

    # When the vault is linted
    result = run_cli("lint", "--vault", str(driftwood_vault), "--json")

    # Then the id and the file name are each reported as reserved
    reserved = [
        finding["message"]
        for finding in result.json()["findings"]  # type: ignore[union-attr]
        if finding["class"] == "id-reserved-name"  # type: ignore[index]
    ]
    assert result.returncode == 1
    assert len(reserved) == 2
    assert any("the id 'Nul.stray'" in message for message in reserved)  # type: ignore[operator]
    assert any("the file name Nul.stray.md" in message for message in reserved)  # type: ignore[operator]


def test_no_tracked_path_has_a_component_that_is_a_device_name() -> None:
    # Given every path git tracks in this repository
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPOSITORY, capture_output=True, text=True, check=True
    ).stdout
    paths = [path for path in listed.split("\0") if path]

    # When each directory and file name in them is judged
    reserved = [
        path
        for path in paths
        if any(reserved_device_name(component) for component in path.split("/"))
    ]

    # Then none is reserved, so the repository can be checked out on Windows
    assert paths, "git lists no tracked files"
    assert reserved == []


@pytest.mark.parametrize("name", ["note.", "note ", "dir.", "x.y. "])
def test_a_name_ending_in_a_dot_or_a_space_is_unusable_on_windows(name: str) -> None:
    # Given a name Windows would strip of its trailing dot or space
    # When it is judged
    # Then it is unusable, and the reason says so
    assert "dot or a space" in str(windows_unusable_name(name))


@pytest.mark.parametrize("name", ["note.md", ".gitkeep", "a.b", "x"])
def test_an_ordinary_name_is_usable_on_windows(name: str) -> None:
    # Given an ordinary name, including one that starts with a dot
    # When it is judged
    # Then nothing is wrong with it
    assert windows_unusable_name(name) is None


@requires_device_file_names
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("clauses/aux/cl.hidden.md", "directory"),
        ("spans/aux.jsonl", "file"),
        ("sources/note.", "file"),
        ("terms/dir /tm.inside.md", "directory"),
        ("spans/lpt\u00b9.jsonl", "file"),
        ("spans/conin$.jsonl", "file"),
    ],
)
def test_lint_reports_a_directory_or_file_anywhere_in_the_vault_whose_name_is_unusable(
    path: str, kind: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a path in the vault with a component Windows cannot hold: a directory, a span file,
    # a file that is not a note, a name with a trailing space or a superscript device name
    target = driftwood_vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x\n")

    # When the vault is linted
    result = run_cli("lint", "--vault", str(driftwood_vault), "--json")

    # Then the entry is reported, by its path, as unusable on Windows
    reported = [
        finding
        for finding in result.json()["findings"]  # type: ignore[union-attr]
        if finding["class"] == "id-reserved-name"  # type: ignore[index]
    ]
    name = path.rsplit("/", 1)[-1] if kind == "file" else path.split("/")[-2]
    assert [f["path"] for f in reported if f"the {kind} name {name} " in f["message"]]  # type: ignore[index, operator]
    assert result.returncode == 1


@requires_device_file_names
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (".git/hooks/aux", "file"),
        (".obsidian/plugins/aux", "file"),
        (".obsidian/plugins/aux/", "directory"),
    ],
)
def test_lint_does_not_enter_hidden_directories_for_unusable_names(
    path: str, kind: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a hidden directory (git's, or an editor's that is often ignored) holding a file or
    # an empty directory named for a device
    target = driftwood_vault / path
    if kind == "directory":
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")

    # When the vault is linted
    result = run_cli("lint", "--vault", str(driftwood_vault), "--json")

    # Then it is not reported, and lint passes
    assert result.returncode == 0
    assert result.json()["findings"] == []
