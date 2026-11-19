"""SRC-1 to SRC-6: `search` with terms ranks notes by BM25 relevance.

The last test runs every query of the fixture's benchmark file through the command line.
"""

import re
from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES, CliResult, CliRunner
from search_vaults import NoteSpec, result_ids, result_rows, write_vault


def _search(run_cli: CliRunner, vault: Path, *arguments: str) -> CliResult:
    return run_cli("search", *arguments, "--vault", str(vault), "--json")


def _vault(tmp_path: Path, *notes: NoteSpec, reverse: bool = False) -> Path:
    return write_vault(tmp_path / "vault", notes, reverse=reverse)


def test_title_match_outranks_body_match(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a term in one title and, in a note with the earlier id, only in a body
    vault = _vault(
        tmp_path,
        NoteSpec("a.body", "Alpha", "The harbour bell rings."),
        NoteSpec("z.title", "Harbour", "Something else entirely."),
    )

    # When searching for it
    result = _search(run_cli, vault, "harbour")

    # Then the title note ranks first even though its id sorts last
    assert result_ids(result) == ["z.title", "a.body"]


def test_more_matching_terms_rank_higher(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given notes holding one term each and one note holding both, with the latest id
    vault = _vault(
        tmp_path,
        NoteSpec("a.salt", "Note", "salt barrel"),
        NoteSpec("b.rope", "Note", "rope barrel"),
        NoteSpec("c.both", "Note", "salt rope barrel"),
    )

    # When searching for both terms
    result = _search(run_cli, vault, "salt", "rope")

    # Then the note holding both ranks first and the others follow in id order
    assert result_ids(result) == ["c.both", "a.salt", "b.rope"]


def test_phrase_and_all_terms_modes(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given notes holding two words adjacent, apart, reversed and only one of them
    vault = _vault(
        tmp_path,
        NoteSpec("p1", "Note", "wreck pile"),
        NoteSpec("p2", "Note", "pile of wreck"),
        NoteSpec("p3", "Note", "wreck beside the pile"),
        NoteSpec("p4", "Note", "wreck only"),
    )

    # When searching with a phrase, with --all, and with the same words as alternatives
    phrase = _search(run_cli, vault, "wreck pile")
    every = _search(run_cli, vault, "wreck", "pile", "--all")
    either = _search(run_cli, vault, "wreck", "pile")

    # Then the phrase needs adjacency, --all needs both words anywhere, and neither is the default
    assert result_ids(phrase) == ["p1"]
    assert sorted(str(note_id) for note_id in result_ids(every)) == ["p1", "p2", "p3"]
    assert sorted(str(note_id) for note_id in result_ids(either)) == ["p1", "p2", "p3", "p4"]


def test_type_filter_and_limit(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a vault with many clauses
    unlimited = _search(run_cli, driftwood_vault, "--type", "clause")

    # When limiting a filter-only search and a ranked one
    capped = _search(run_cli, driftwood_vault, "--type", "clause", "--limit", "2")
    ranked = _search(run_cli, driftwood_vault, "squall", "--type", "clause", "--limit", "1")

    # Then the first ids by id order come back, and one ranked row
    assert len(result_ids(unlimited)) > 2
    assert result_ids(capped) == result_ids(unlimited)[:2]
    assert len(result_ids(ranked)) == 1


@pytest.mark.parametrize("limit", ["0", "-1", "two"])
def test_a_limit_that_is_not_a_positive_number_is_a_usage_error(
    limit: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a limit that cannot count rows
    # When searching
    result = _search(run_cli, driftwood_vault, "squall", "--limit", limit)

    # Then the command exits 2
    assert result.returncode == 2


def test_equal_scores_break_ties_by_id(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given two notes with identical text, the later id written first
    vault = _vault(
        tmp_path,
        NoteSpec("m.two", "Twin", "salt rope"),
        NoteSpec("m.one", "Twin", "salt rope"),
        reverse=True,
    )

    # When searching for their words
    result = _search(run_cli, vault, "salt")

    # Then equal scores are ordered by id
    rows = result_rows(result)
    assert [row["id"] for row in rows] == ["m.one", "m.two"]
    assert rows[0]["score"] == rows[1]["score"]


def test_terms_rank_only_within_the_filtered_set(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a term in a clause and in a keyword, where the clause would outrank the keyword
    vault = _vault(
        tmp_path,
        NoteSpec("cl.tide", "Tide", "tide tide tide", type="clause"),
        NoteSpec("kw.tide", "Rising water", "the tide", type="keyword"),
    )

    # When searching with the keyword type filter
    filtered = _search(run_cli, vault, "tide", "--type", "keyword")
    unfiltered = _search(run_cli, vault, "tide")

    # Then only the keyword is returned, ranked first, and the unfiltered list had both
    assert result_ids(filtered) == ["kw.tide"]
    assert result_rows(filtered)[0]["rank"] == 1
    assert result_ids(unfiltered) == ["cl.tide", "kw.tide"]


def test_filters_apply_before_the_limit_and_ranks_stay_contiguous(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given strong matches of one type and weaker matches of another
    strong = [NoteSpec(f"a.{index}", "Salt salt", "salt salt", type="clause") for index in range(5)]
    weak = [NoteSpec(f"b.{index}", "Note", "salt", type="keyword") for index in range(5)]
    vault = _vault(tmp_path, *strong, *weak)

    # When searching the weaker type with a limit
    result = _search(run_cli, vault, "salt", "--type", "keyword", "--limit", "3")

    # Then three keywords come back with ranks 1 to 3
    rows = result_rows(result)
    assert [row["id"] for row in rows] == ["b.0", "b.1", "b.2"]
    assert [row["rank"] for row in rows] == [1, 2, 3]


def test_default_limit_is_twenty_with_terms(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given 25 notes holding a word
    vault = _vault(tmp_path, *(NoteSpec(f"n.{index:02d}", "Note", "salt") for index in range(25)))

    # When searching for it with and without a limit
    default = _search(run_cli, vault, "salt")
    explicit = _search(run_cli, vault, "salt", "--limit", "25")

    # Then twenty rows come back by default and all 25 when asked for
    assert [row["rank"] for row in result_rows(default)] == list(range(1, 21))
    assert len(result_rows(explicit)) == 25


def test_no_match_is_an_empty_list_with_exit_zero(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a term found nowhere
    # When searching for it
    result = _search(run_cli, driftwood_vault, "zeppelin")

    # Then the list is empty and the exit code is 0
    assert result.returncode == 0
    assert result_rows(result) == []


def test_scores_are_rounded_to_four_decimals(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given ranked results
    result = _search(run_cli, driftwood_vault, "boat", "tide")

    # When reading their scores
    scores = [row["score"] for row in result_rows(result)]

    # Then each is rounded to four decimals and none rises down the list
    assert scores
    assert all(isinstance(score, float) and score == round(score, 4) for score in scores)
    assert scores == sorted(scores, reverse=True)


def test_score_follows_bm25_with_the_title_weighted_twice(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given one note whose title holds the term and whose body only repeats the title as a heading
    vault = _vault(tmp_path, NoteSpec("only", "Lantern"))

    # When searching for the term
    result = _search(run_cli, vault, "lantern")

    # Then the score is twice the title score alone: 2 * ln(4/3), the heading adding nothing
    assert result_rows(result)[0]["score"] == 0.5754


def test_wikilinks_in_bodies_contribute_their_alias_or_target(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given bodies with an aliased link, a bare link and a link with a heading part
    vault = _vault(
        tmp_path,
        NoteSpec("a", "A", "See [[cl.hidden-target|the marker rule]] now."),
        NoteSpec("b", "B", "See [[zone.stock]] now."),
        NoteSpec("c", "C", "See [[cl.other#Section|plain words]] now."),
    )

    # When searching for alias words, target words and bracket text
    alias = _search(run_cli, vault, "marker")
    shadowed = _search(run_cli, vault, "hidden")
    target = _search(run_cli, vault, "stock")
    heading = _search(run_cli, vault, "section", "other")

    # Then the alias is searchable, a target behind an alias and a heading part are not,
    # and a bare link is searched by its target
    assert result_ids(alias) == ["a"]
    assert result_ids(shadowed) == []
    assert result_ids(target) == ["b"]
    assert result_ids(heading) == []


def test_text_inside_code_fences_is_searchable(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a body with a word only inside a fence
    vault = _vault(tmp_path, NoteSpec("a", "A", "```\nlantern\n```"))

    # When searching for it
    result = _search(run_cli, vault, "lantern")

    # Then the note is found
    assert result_ids(result) == ["a"]


@pytest.mark.parametrize(
    "hostile",
    [
        "***",
        '"unterminated',
        "a:b[c]{d}\\e^f$g|h&i",
        "title:squall OR (NOT) ~tide*",
        "[[ph.sail|Sail]]",
        "' OR 1=1 --",
        "é中文",
    ],
)
def test_punctuation_in_a_query_is_searched_as_text_and_never_raises(
    hostile: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a query made of quotes, colons, brackets, operators and non-ASCII text
    # When searching for it
    result = _search(run_cli, driftwood_vault, hostile)

    # Then the command answers with exit 0 and one JSON document
    assert result.returncode == 0
    assert isinstance(result_rows(result), list)


def test_punctuation_around_a_word_does_not_change_the_results(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a word and the same word wrapped in query-like punctuation
    plain = _search(run_cli, driftwood_vault, "squall")
    wrapped = _search(run_cli, driftwood_vault, '**"Squall"**')

    # When comparing the answers
    # Then they are identical
    assert wrapped.stdout == plain.stdout
    assert result_ids(plain)


def test_a_term_after_double_dash_may_start_with_a_dash(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a note holding a word
    vault = _vault(tmp_path, NoteSpec("a", "A", "salt"))

    # When the term follows `--` and begins with a dash
    result = run_cli("search", "--vault", str(vault), "--json", "--", "-salt")

    # Then it is searched as the word it contains
    assert result_ids(result) == ["a"]


def test_a_term_that_is_all_punctuation_matches_nothing(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a term holding no words, alongside a filter
    result = _search(run_cli, driftwood_vault, "***", "--type", "zone")

    # When searching
    # Then nothing matches, because ranking was asked for and there is nothing to rank by
    assert result.returncode == 0
    assert result_rows(result) == []


def test_snippets_excerpt_the_body_around_the_first_match(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a long body with the word in the middle, and a note matching only in its title
    filler = " ".join(f"word{index}" for index in range(60))
    vault = _vault(
        tmp_path,
        NoteSpec("a", "Plain", f"{filler}\n\nthe lantern glows\n\n{filler}"),
        NoteSpec("b", "Lantern", "nothing here"),
    )

    # When asking for snippets, and not asking
    with_snippets = _search(run_cli, vault, "lantern", "--snippets")
    without = _search(run_cli, vault, "lantern")

    # Then the body excerpt is one line around the match with cut marks, the title-only match
    # shows its title, and rows carry no snippet unless asked
    rows = {row["id"]: row for row in result_rows(with_snippets)}
    excerpt = str(rows["a"]["snippet"])
    assert "the lantern glows" in excerpt
    assert excerpt.startswith("...")
    assert excerpt.endswith("...")
    assert "\n" not in excerpt
    assert rows["b"]["snippet"] == "Lantern"
    assert all("snippet" not in row for row in result_rows(without))


def test_plain_text_output_is_a_table_with_rank_and_score(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a ranked search without --json
    result = run_cli("search", "stock", "--vault", str(driftwood_vault))

    # When reading the human output
    lines = result.stdout.splitlines()

    # Then it is an aligned table whose first row is the best match
    assert result.returncode == 0
    assert lines[0].split()[:4] == ["rank", "score", "id", "type"]
    rank, score, note_id = lines[1].split()[:3]
    assert (rank, note_id) == ("1", "zone.stock")
    assert re.fullmatch(r"\d+\.\d{4}", score)


def test_output_does_not_depend_on_the_order_files_were_created(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given one vault written in two orders, holding tied scores and a note reached by two
    # equal-length paths along different edges
    notes = [
        NoteSpec("tie.c", "Twin", "salt rope"),
        NoteSpec("tie.a", "Twin", "salt rope"),
        NoteSpec("tie.b", "Twin", "salt rope"),
        NoteSpec("start", "Start", links={"references": ["mid.y", "mid.x"], "part-of": ["mid.z"]}),
        NoteSpec("mid.x", "X", links={"defines": ["goal"]}),
        NoteSpec("mid.y", "Y", links={"defines": ["goal"]}),
        NoteSpec("mid.z", "Z", links={"precedes": ["goal"]}),
        NoteSpec("goal", "Goal"),
    ]
    forward = write_vault(tmp_path / "forward", notes)
    backward = write_vault(tmp_path / "backward-with-another-name", notes, reverse=True)

    # When searching and traversing each
    searches = [_search(run_cli, vault, "salt", "rope") for vault in (forward, backward)]
    walks = [
        run_cli(
            "traverse",
            "start",
            "--depth",
            "3",
            "--direction",
            "both",
            "--vault",
            str(vault),
            "--json",
        )
        for vault in (forward, backward)
    ]

    # Then the output is byte-identical, ties are in id order and the goal's path is stable
    assert searches[0].stdout == searches[1].stdout
    assert walks[0].stdout == walks[1].stdout
    assert result_ids(searches[0]) == ["tie.a", "tie.b", "tie.c"]
    goal = next(row for row in result_rows(walks[0]) if row["id"] == "goal")
    assert goal["via"] == [
        {"edge": "part-of", "from": "start", "to": "mid.z"},
        {"edge": "precedes", "from": "mid.z", "to": "goal"},
    ]


def _benchmark_queries() -> list[dict[str, object]]:
    document = yaml.safe_load((FIXTURES / "benchmark-queries.yaml").read_text(encoding="utf-8"))
    return list(document["queries"])


@pytest.mark.parametrize("query", _benchmark_queries(), ids=lambda query: str(query["id"]))
def test_every_benchmark_query_gives_its_expected_answer(
    query: dict[str, object], run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a benchmark query and the answer computed for it independently
    arguments = [str(argument) for argument in query["args"]]  # type: ignore[attr-defined]
    expect: dict[str, object] = query["expect"]  # type: ignore[assignment]

    # When it runs through the command line
    result = run_cli(str(query["command"]), *arguments, "--vault", str(driftwood_vault), "--json")

    # Then the exit code is 0 and the rows match, in order or as a set as the query says
    assert result.returncode == 0
    if query["command"] == "search":
        ids = result_ids(result)
        expected_ids: list[object] = expect["ids"]  # type: ignore[assignment]
        if expect["order"] == "exact":
            assert ids == expected_ids
        else:
            assert sorted(map(str, ids)) == sorted(map(str, expected_ids))
            if "first" in expect:
                assert ids[0] == expect["first"]
    else:
        rows = [
            {"id": row["id"], "distance": row["distance"], "edge": row["via"][-1]["edge"]}  # type: ignore[index]
            for row in result_rows(result)
        ]
        assert rows == expect["rows"]
