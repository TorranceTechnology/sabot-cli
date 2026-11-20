"""COV-1 to COV-4: `coverage` judges which in-scope spans the notes cite."""

import json
from pathlib import Path

import pytest

from conftest import CliResult, CliRunner
from support.plant import register_second_edition

DW1_SPAN = "dw1#6.6"
REFUSED_SUBTREE_NOTE = """---
id: cl.sixth-section-refused
type: clause
title: Sixth section declined
status: refused
confidence: high
rev: 1
schema: 1
modality: procedure
refusal: The whole section is outside what the rules engine models.
source:
  - "dw2#6/*"
---

# Sixth section declined

The section is set aside as a whole.
"""


def _coverage(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli("coverage", "--vault", str(vault), "--json", *options)


def _sources(result: CliResult) -> dict[str, dict[str, object]]:
    sources = result.json()["sources"]
    assert isinstance(sources, list)
    return {str(entry["key"]): entry for entry in sources}


def _rewrite_note(vault: Path, note_id: str, old: str, new: str) -> None:
    (path,) = vault.rglob(f"{note_id}.md")
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {path.name}"
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def _rewrite_ruleset(vault: Path, old: str, new: str) -> None:
    path = vault / "_ruleset.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def test_full_coverage_passes(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the clean vault, which cites every span of edition one
    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then nothing is uncited or partly cited
    assert result.returncode == 0
    dw1 = _sources(result)["dw1"]
    assert result.json()["complete"] is True
    assert dw1["uncited"] == []
    assert dw1["partiallyCited"] == []
    assert dw1["cited"] == dw1["inScope"]
    assert isinstance(dw1["inScope"], int)
    assert dw1["inScope"] > 0


def test_citing_notes_are_counted_by_status(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the clean vault, whose notes cite dw1 under four statuses
    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then each status is counted once per citing note, the uncounted statuses included
    counts = _sources(result)["dw1"]["citingNotes"]
    assert isinstance(counts, dict)
    assert {status: counts[status] for status in ("refused", "structural", "superseded")} == {
        "refused": 1,
        "structural": 1,
        "superseded": 1,
    }
    assert counts["mapped"] > 1


def test_uncited_span_is_listed(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the only citation of one span removed
    _rewrite_note(driftwood_vault, "og.high-water", f'  - "{DW1_SPAN}"\n', "")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then exactly that span is listed and the exit code is 1
    assert result.returncode == 1
    assert _sources(result)["dw1"]["uncited"] == [DW1_SPAN]
    assert result.json()["complete"] is False


def test_uncited_span_is_named_in_the_text_report(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the only citation of one span removed
    _rewrite_note(driftwood_vault, "og.high-water", f'  - "{DW1_SPAN}"\n', "")

    # When coverage runs without --json
    result = run_cli("coverage", "--vault", str(driftwood_vault))

    # Then the table and an `uncited` line name it
    assert result.returncode == 1
    assert f"uncited {DW1_SPAN}\n" in result.stdout
    assert result.stdout.splitlines()[0].split() == [
        "source",
        "in",
        "scope",
        "cited",
        "uncited",
        "partial",
    ]


def test_partially_cited_multi_sentence_span_lists_missing_sentences(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a two-sentence span whose second sentence is cited by no note
    _rewrite_note(driftwood_vault, "cl.tide-wrap", '  - "dw1#6.1/s2"\n', "")
    _rewrite_note(driftwood_vault, "oq.tide-overflow", '  - "dw1#6.1/s2"\n', "")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then the span is partly cited and its second sentence is the one listed
    assert result.returncode == 1
    dw1 = _sources(result)["dw1"]
    assert dw1["partiallyCited"] == [{"missing": ["dw1#6.1/s2"], "span": "dw1#6.1"}]
    assert dw1["uncited"] == []


def test_refused_subtree_covers_descendants(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given edition two registered, scoped to section 6, and not yet cited
    register_second_edition(driftwood_vault)
    _rewrite_ruleset(driftwood_vault, "scope:\n", "scope:\n  dw2: ['6']\n")
    before = _coverage(run_cli, driftwood_vault, "--source", "dw2")
    assert before.returncode == 1

    # When a refused note citing the subtree of section 6 is added
    note = driftwood_vault / "clauses" / "cl.sixth-section-refused.md"
    note.write_bytes(REFUSED_SUBTREE_NOTE.encode("utf-8"))
    result = _coverage(run_cli, driftwood_vault, "--source", "dw2")

    # Then every span under 6 is covered
    assert result.returncode == 0
    dw2 = _sources(result)["dw2"]
    assert dw2["uncited"] == []
    assert dw2["inScope"] == dw2["cited"]
    assert isinstance(dw2["inScope"], int)
    assert dw2["inScope"] > 1


def test_subtree_cited_by_a_mapped_note_covers_nothing(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the only citation of one span replaced by a subtree citation from a mapped note
    _rewrite_note(driftwood_vault, "og.high-water", f'"{DW1_SPAN}"', f'"{DW1_SPAN}/*"')

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then the span is still uncited
    assert result.returncode == 1
    assert _sources(result)["dw1"]["uncited"] == [DW1_SPAN]


def test_a_pending_note_does_not_cover_what_it_cites(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the only note citing one span marked pending
    _rewrite_note(driftwood_vault, "og.high-water", "status: mapped", "status: pending")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then the span is uncited, and the pending note is still counted as a citer
    assert result.returncode == 1
    dw1 = _sources(result)["dw1"]
    assert dw1["uncited"] == [DW1_SPAN]
    assert dw1["citingNotes"]["pending"] == 1  # type: ignore[index]


def test_a_superseded_note_does_not_cover_what_it_cites(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the only note citing one span marked superseded
    _rewrite_note(driftwood_vault, "og.high-water", "status: mapped", "status: superseded")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then the span is uncited
    assert result.returncode == 1
    assert _sources(result)["dw1"]["uncited"] == [DW1_SPAN]


def test_scope_limits_the_spans_judged(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a span outside section 4 left uncited, and a scope of section 4 only
    _rewrite_note(driftwood_vault, "og.high-water", f'  - "{DW1_SPAN}"\n', "")
    full = _sources(_coverage(run_cli, driftwood_vault))["dw1"]["inScope"]
    _rewrite_ruleset(driftwood_vault, "dw1: []", "dw1: ['4']")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then only section 4 is judged and the uncited span outside it is not reported
    assert result.returncode == 0
    in_scope = _sources(result)["dw1"]["inScope"]
    assert isinstance(in_scope, int)
    assert isinstance(full, int)
    assert 0 < in_scope < full


def test_scope_prefix_holds_the_section_and_its_rules(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the vault scoped to section 1
    _rewrite_ruleset(driftwood_vault, "dw1: []", "dw1: ['1']")
    spans = [
        json.loads(line)
        for line in (driftwood_vault / "spans" / "dw1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    section_one = [r for r in spans if r["kind"] == "span" and r["locator"].split(".")[0] == "1"]

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then exactly the spans numbered 1 and 1.x are in scope
    assert _sources(result)["dw1"]["inScope"] == len(section_one)


def test_source_option_limits_the_report(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given two registered sources, neither of which supersedes the other
    register_second_edition(driftwood_vault)
    _rewrite_ruleset(driftwood_vault, "    supersedes: dw1\n", "")

    # When coverage runs for every source and then for one
    everything = _coverage(run_cli, driftwood_vault)
    one = _coverage(run_cli, driftwood_vault, "--source", "dw1")

    # Then the second report holds only the requested source
    assert sorted(_sources(everything)) == ["dw1", "dw2"]
    assert sorted(_sources(one)) == ["dw1"]
    assert one.returncode == 0


def test_a_superseded_source_is_measured_only_when_named(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition two registered as the successor of edition one, which is fully cited
    register_second_edition(driftwood_vault)

    # When coverage runs for every source and then for the old edition by name
    everything = _coverage(run_cli, driftwood_vault)
    named = _coverage(run_cli, driftwood_vault, "--source", "dw1")

    # Then the default report leaves the superseded edition out, and naming it measures it
    assert sorted(_sources(everything)) == ["dw2"]
    assert sorted(_sources(named)) == ["dw1"]
    assert named.returncode == 0


def test_the_sources_left_out_for_being_superseded_are_listed(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition two registered as the successor of edition one
    register_second_edition(driftwood_vault)

    # When coverage runs for every source, as JSON and as text, and for the old edition by name
    everything = _coverage(run_cli, driftwood_vault)
    text = run_cli("coverage", "--vault", str(driftwood_vault))
    named = _coverage(run_cli, driftwood_vault, "--source", "dw1")

    # Then the report names what it left out, and naming the edition leaves nothing out
    assert everything.json()["skippedSuperseded"] == ["dw1"]
    assert "skipped dw1 (superseded)\n" in text.stdout
    assert named.json()["skippedSuperseded"] == []
    named_text = run_cli("coverage", "--vault", str(driftwood_vault), "--source", "dw1")
    assert "skipped" not in named_text.stdout


@pytest.mark.parametrize("loop", ["itself", "each other"])
def test_sources_that_supersede_each_other_in_a_loop_are_refused(
    loop: str, run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition two registered after edition one, and a loop planted in the ruleset
    register_second_edition(driftwood_vault)
    target = "dw1" if loop == "itself" else "dw2"
    _rewrite_ruleset(
        driftwood_vault, "    note: src.dw1\n", f"    note: src.dw1\n    supersedes: {target}\n"
    )

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then it refuses the ruleset instead of reporting that nothing needs covering
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "ruleset-invalid"  # type: ignore[index]
    assert "loop" in result.stdout


def test_a_chain_of_editions_is_measured_at_its_last_link(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given three editions, each registered as the successor of the one before
    register_second_edition(driftwood_vault)
    spans = driftwood_vault / "spans"
    (spans / "dw3.jsonl").write_bytes((spans / "dw2.jsonl").read_bytes().replace(b"dw2", b"dw3"))
    _rewrite_ruleset(
        driftwood_vault,
        "    supersedes: dw1\n",
        "    supersedes: dw1\n  - key: dw3\n    note: src.dw2\n    supersedes: dw2\n",
    )

    # When coverage runs for every source
    result = _coverage(run_cli, driftwood_vault)

    # Then only the last edition is measured
    assert sorted(_sources(result)) == ["dw3"]


def test_a_registered_source_without_a_scope_entry_is_wholly_in_scope(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition two registered with no scope entry and no citations
    register_second_edition(driftwood_vault)

    # When coverage runs for it
    result = _coverage(run_cli, driftwood_vault, "--source", "dw2")

    # Then every span of it is in scope and uncited
    assert result.returncode == 1
    dw2 = _sources(result)["dw2"]
    assert dw2["cited"] == 0
    assert len(dw2["uncited"]) == dw2["inScope"]  # type: ignore[arg-type]


def test_an_unregistered_source_is_refused(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a source key the ruleset does not list
    # When coverage is asked about it
    result = _coverage(run_cli, driftwood_vault, "--source", "nope")

    # Then it is refused by name
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "source-unknown"  # type: ignore[index]


def test_a_registered_source_without_a_span_file_is_refused(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given edition one's span file removed
    (driftwood_vault / "spans" / "dw1.jsonl").unlink()

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then it is refused rather than reported as fully uncited
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "span-file-missing"  # type: ignore[index]


def test_an_unreadable_span_file_is_refused(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a span file with a line that is not a record
    (driftwood_vault / "spans" / "dw1.jsonl").write_bytes(b"not json\n")

    # When coverage runs
    result = _coverage(run_cli, driftwood_vault)

    # Then it is refused by name
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "span-file-invalid"  # type: ignore[index]


def test_a_directory_that_is_not_a_vault_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a directory with no ruleset file
    # When coverage runs on it
    result = _coverage(run_cli, tmp_path)

    # Then it is refused as not a vault
    assert result.returncode == 3
    assert result.json()["error"]["code"] == "not-a-vault"  # type: ignore[index]
