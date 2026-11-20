"""Windows reserves device names as file names, so no note id, file or tracked path may use one."""

import subprocess
from pathlib import Path

import pytest

from conftest import CliRunner
from sabot_cli.rules import load_schema
from sabot_cli.vault.paths import reserved_device_name

REPOSITORY = Path(__file__).parent.parent


@pytest.mark.parametrize(
    "name",
    ["con", "CON", "Con.x.md", "prn.md", "aux.thing", "nul", "com1", "COM9.x", "lpt1.x", "LPT9"],
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
