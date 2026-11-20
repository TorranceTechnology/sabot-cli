"""TRV-1 to TRV-5: `traverse` lists the notes reachable from one note."""

from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from search_vaults import NoteSpec, result_ids, result_rows, write_vault


def _traverse(run_cli: CliRunner, vault: Path, *arguments: str) -> CliResult:
    return run_cli("traverse", *arguments, "--vault", str(vault), "--json")


def _hop(source: str, edge: str, target: str) -> dict[str, str]:
    return {"edge": edge, "from": source, "to": target}


def _via(result: CliResult, note_id: str) -> object:
    (row,) = (row for row in result_rows(result) if row["id"] == note_id)
    return row["via"]


def test_outgoing_neighbours_at_depth_one(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given an action with five outgoing edges
    # When traversing from it with the defaults
    result = _traverse(run_cli, driftwood_vault, "act.launch-boat")

    # Then the direct targets come back at distance 1, ordered by edge name then id
    rows = result_rows(result)
    assert result.returncode == 0
    assert [(row["id"], row["distance"]) for row in rows] == [
        ("cst.launch-boat-command", 1),
        ("ph.sail", 1),
        ("zone.hand", 1),
        ("to.launch-boat", 1),
        ("tp.boat-launched", 1),
    ]
    assert [row["via"][0]["edge"] for row in rows] == [  # type: ignore[index]
        "implemented-by",
        "occurs-in",
        "requires",
        "tested-by",
        "triggers",
    ]
    sail = rows[1]
    assert (sail["type"], sail["title"]) == ("phase", "Sail phase")


def test_incoming_edges_use_inverse_names(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a phase that other notes point at
    # When following incoming edges
    result = _traverse(run_cli, driftwood_vault, "ph.sail", "--direction", "in")

    # Then each row is named by the inverse, and its path reads in the direction of travel
    names = [row["via"][0]["edge"] for row in result_rows(result)]  # type: ignore[index]
    assert names == ["defined-by", "follows", "has-part", "hosts", "hosts", "hosts"]
    assert _via(result, "act.launch-boat") == [_hop("ph.sail", "hosts", "act.launch-boat")]


def test_an_override_is_followed_backwards_as_overridden_by(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a keyword that overrides a clause
    # When following incoming edges from the clause
    result = _traverse(run_cli, driftwood_vault, "cl.beach-effect", "--direction", "in")

    # Then the keyword is reached through the inverse name `overridden-by`
    assert _via(result, "kw.buoyant") == [_hop("cl.beach-effect", "overridden-by", "kw.buoyant")]


def test_an_edge_filter_names_edges_as_followed_in_each_direction(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a phase with three notes that host-link to it
    # When filtering by the inverse name while following incoming edges, and by the authored
    # name while following outgoing ones
    incoming = _traverse(
        run_cli, driftwood_vault, "ph.sail", "--direction", "in", "--edge", "hosts"
    )
    outgoing = _traverse(run_cli, driftwood_vault, "ph.sail", "--edge", "hosts")

    # Then the inverse name selects them going in and selects nothing going out
    assert result_ids(incoming) == ["act.launch-boat", "act.sail-out", "tp.boat-launched"]
    assert result_ids(outgoing) == []


def test_depth_two_with_edge_filter(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a chain of phases joined by `precedes`
    # When traversing two hops along that edge only
    result = _traverse(run_cli, driftwood_vault, "ph.ebb", "--edge", "precedes", "--depth", "2")

    # Then both phases come back with their paths
    assert result_ids(result) == ["ph.sail", "ph.flood"]
    assert _via(result, "ph.flood") == [
        _hop("ph.ebb", "precedes", "ph.sail"),
        _hop("ph.sail", "precedes", "ph.flood"),
    ]


def test_only_the_named_edges_are_followed(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given an action with five outgoing edges
    # When two are named
    result = _traverse(
        run_cli, driftwood_vault, "act.launch-boat", "--edge", "requires", "--edge", "occurs-in"
    )

    # Then only those two lead anywhere
    assert result_ids(result) == ["ph.sail", "zone.hand"]


def test_cycle_terminates_and_lists_each_node_once(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a `returns-to` edge that closes a loop through the start note
    # When traversing to the maximum depth
    result = _traverse(
        run_cli,
        driftwood_vault,
        "ph.ebb",
        "--edge",
        "precedes",
        "--edge",
        "returns-to",
        "--depth",
        "10",
    )

    # Then every phase appears once at its shortest distance and the start note is not listed
    rows = result_rows(result)
    assert [(row["id"], row["distance"]) for row in rows] == [
        ("ph.sail", 1),
        ("ph.flood", 2),
        ("ph.moor", 3),
    ]


def test_unknown_start_id_is_refused(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given an id no note carries
    # When traversing from it
    result = _traverse(run_cli, driftwood_vault, "nope")

    # Then the command exits 3 with the JSON error document
    assert result.returncode == 3
    assert result.json()["error"] == {
        "code": "note-unknown",
        "message": "no note has the id 'nope'",
    }


def test_unknown_edge_name_is_refused(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given an edge name the schema does not define
    # When traversing along it
    result = _traverse(run_cli, driftwood_vault, "ph.ebb", "--edge", "flies-to")

    # Then the command exits 3 and names it
    assert result.returncode == 3
    assert result.json()["error"] == {
        "code": "edge-unknown",
        "message": "no edge is named 'flies-to'",
    }


@pytest.mark.parametrize("depth", ["0", "11", "-1", "deep"])
def test_a_depth_outside_one_to_ten_is_a_usage_error(
    depth: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a depth outside the supported range
    # When traversing
    result = _traverse(run_cli, driftwood_vault, "ph.ebb", "--depth", depth)

    # Then the command exits 2
    assert result.returncode == 2


def test_a_link_to_a_missing_note_is_listed_with_a_null_type(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a note linking an id that no note carries
    vault = write_vault(
        tmp_path / "vault",
        [NoteSpec("a", "A", links={"references": ["ghost"]})],
    )

    # When traversing from it
    result = _traverse(run_cli, vault, "a")

    # Then the target is listed with no type and no title
    (row,) = result_rows(result)
    assert (row["id"], row["type"], row["title"]) == ("ghost", None, None)


def test_each_note_appears_once_at_its_shortest_distance(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a note reachable in one hop and also in two
    vault = write_vault(
        tmp_path / "vault",
        [
            NoteSpec("a", "A", links={"references": ["b", "c"]}),
            NoteSpec("b", "B", links={"references": ["c"]}),
            NoteSpec("c", "C"),
        ],
    )

    # When traversing two hops
    result = _traverse(run_cli, vault, "a", "--depth", "2")

    # Then it is listed once, at distance 1, and its path is the direct hop
    assert [(row["id"], row["distance"]) for row in result_rows(result)] == [("b", 1), ("c", 1)]
    assert _via(result, "c") == [_hop("a", "references", "c")]


def test_equal_length_paths_resolve_to_the_smallest_edge_then_id(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given two notes each reached by two equal-length paths: one differing in edge names and
    # one in the ids of the notes between
    vault = write_vault(
        tmp_path / "vault",
        [
            NoteSpec("start", "Start", links={"references": ["m.b", "m.a"], "part-of": ["m.c"]}),
            NoteSpec("m.a", "A", links={"defines": ["by-id", "by-edge"]}),
            NoteSpec("m.b", "B", links={"defines": ["by-id"]}),
            NoteSpec("m.c", "C", links={"precedes": ["by-edge"]}),
            NoteSpec("by-id", "By id"),
            NoteSpec("by-edge", "By edge"),
        ],
    )

    # When traversing two hops
    result = _traverse(run_cli, vault, "start", "--depth", "2")

    # Then the path whose hops are smallest in (edge, id) order is reported
    assert _via(result, "by-id") == [
        _hop("start", "references", "m.a"),
        _hop("m.a", "defines", "by-id"),
    ]
    assert _via(result, "by-edge") == [
        _hop("start", "part-of", "m.c"),
        _hop("m.c", "precedes", "by-edge"),
    ]


def test_direction_both_follows_authored_and_inverse_edges(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a middle note with one link out and one link in
    vault = write_vault(
        tmp_path / "vault",
        [
            NoteSpec("before", "Before", links={"precedes": ["middle"]}),
            NoteSpec("middle", "Middle", links={"precedes": ["after"]}),
            NoteSpec("after", "After"),
        ],
    )

    # When traversing in both directions
    result = _traverse(run_cli, vault, "middle", "--direction", "both")

    # Then each neighbour is named as followed from the middle note
    assert [
        (row["id"], row["via"][0]["edge"])  # type: ignore[index]
        for row in result_rows(result)
    ] == [("before", "follows"), ("after", "precedes")]


def test_a_symmetric_edge_is_found_from_both_ends(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a `restates` link authored on one note only
    vault = write_vault(
        tmp_path / "vault",
        [NoteSpec("a", "A", links={"restates": ["b"]}), NoteSpec("b", "B")],
    )

    # When following incoming edges from the note that does not author it
    result = _traverse(run_cli, vault, "b", "--direction", "in")

    # Then the edge keeps its own name as its inverse
    assert _via(result, "a") == [_hop("b", "restates", "a")]


def test_plain_text_output_is_a_table_with_paths(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a traversal without --json
    result = run_cli(
        "traverse", "ph.ebb", "--edge", "precedes", "--depth", "2", "--vault", str(driftwood_vault)
    )

    # When reading the human output
    lines = result.stdout.splitlines()

    # Then the header and a row with its path are shown
    assert result.returncode == 0
    assert lines[0].split() == ["distance", "id", "type", "title", "via"]
    assert lines[2].endswith("ph.ebb -precedes-> ph.sail ph.sail -precedes-> ph.flood")


def test_an_edge_filter_accepts_the_authored_name_when_following_incoming_edges(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a phase that three notes link to with occurs-in, whose inverse is hosts
    # When following incoming edges filtered by the authored name
    result = _traverse(
        run_cli, driftwood_vault, "ph.sail", "--direction", "in", "--edge", "occurs-in"
    )

    # Then the same notes are found as when filtering by the inverse name
    assert result_ids(result) == ["act.launch-boat", "act.sail-out", "tp.boat-launched"]
