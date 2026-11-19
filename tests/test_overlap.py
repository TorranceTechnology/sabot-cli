"""OVL-1 and OVL-2: `overlap` measures copied source wording without repeating it."""

from pathlib import Path

from conftest import FIXTURES, CliResult, CliRunner
from source_vaults import error_code, snapshot, write_text

RULES_E1 = FIXTURES / "rules-e1.txt"
NOTE = "model/patterns/og.high-water.md"
# Ten consecutive words of rule 5.3 of the fixture's first edition.
TEN_WORDS = "the combined weight of the cargo on a boat is"


def _plant(vault: Path, text: str) -> None:
    note = vault / NOTE
    write_text(note, note.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + text + "\n")


def _overlap(run_cli: CliRunner, vault: Path, *options: str) -> CliResult:
    return run_cli(
        "overlap", "--source-text", str(RULES_E1), "--vault", str(vault), "--json", *options
    )


def _entry(result: CliResult, path: str = NOTE) -> dict[str, object]:
    notes = result.json()["notes"]
    assert isinstance(notes, list)
    (entry,) = (n for n in notes if n["path"] == path)
    return entry


def test_clean_vault_shares_no_long_run(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given the fixture vault, whose bodies are original paraphrase
    before = snapshot(driftwood_vault)

    # When overlap runs over every note
    result = _overlap(run_cli, driftwood_vault)

    # Then nothing warns or fails, every note is listed in path order, and nothing is written
    assert result.returncode == 0
    assert result.json()["failed"] == 0
    assert result.json()["warned"] == 0
    paths = [n["path"] for n in result.json()["notes"]]  # type: ignore[attr-defined]
    assert paths == sorted(paths)
    assert len(paths) == 54
    assert snapshot(driftwood_vault) == before


def test_copied_run_at_threshold_fails(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a body sharing a 10-word run with the source
    _plant(driftwood_vault, f"The rule says {TEN_WORDS}, near enough.")

    # When overlap runs
    result = _overlap(run_cli, driftwood_vault)

    # Then that note fails with the run length, and the words are printed nowhere
    assert result.returncode == 1
    entry = _entry(result)
    assert entry["words"] == 10
    assert entry["status"] == "fail"
    assert result.json()["failed"] == 1
    text_result = run_cli(
        "overlap", "--source-text", str(RULES_E1), "--vault", str(driftwood_vault)
    )
    assert text_result.returncode == 1
    for output in (result.stdout, text_result.stdout, text_result.stderr):
        assert "combined weight" not in output.lower()
        assert "cargo on a boat" not in output.lower()


def test_run_is_found_through_case_punctuation_and_line_breaks(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given the same run with other case, punctuation and line breaks in the body
    _plant(driftwood_vault, "THE Combined-Weight of the\ncargo, on a *boat*\n\nis.")

    # When overlap runs
    entry = _entry(_overlap(run_cli, driftwood_vault))

    # Then it is still a 10-word run
    assert entry["words"] == 10


def test_a_page_break_in_the_source_does_not_cut_a_run(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given a source whose page footer and form feed fall inside the copied wording
    source = write_text(
        tmp_path / "source.txt",
        "3.1  Boats rise with the tide and wait\n\n- 4 -\n\f\n"
        "4.1  for a calm moment before they sail on.\n",
    )
    _plant(driftwood_vault, "Boats rise with the tide and wait for a calm moment before they sail")

    # When overlap runs
    result = run_cli(
        "overlap", "--source-text", str(source), "--vault", str(driftwood_vault), "--json"
    )

    # Then the run is measured across the break, rule numbers and page number ignored
    assert _entry(result)["words"] == 14


def test_thresholds_set_the_exit_code_and_offsets_locate_the_run(
    run_cli: CliRunner, driftwood_vault: Path
) -> None:
    # Given a body with a 9-word run, set between the defaults' warn (8) and fail (10) lines
    nine = "the combined weight of the cargo on a boat"
    _plant(driftwood_vault, f"Note that {nine} matters.")

    # When overlap runs with the defaults
    default = _overlap(run_cli, driftwood_vault)

    # Then the note warns and the exit code is 0
    assert default.returncode == 0
    assert _entry(default)["status"] == "warn"
    assert default.json()["warned"] == 1

    # When the failing threshold is lowered to 9, or the warning raised above the run
    lowered = _overlap(run_cli, driftwood_vault, "--fail-at", "9")
    raised = _overlap(run_cli, driftwood_vault, "--warn-at", "10")

    # Then 9 fails, and a run under both thresholds passes
    assert lowered.returncode == 1
    assert _entry(lowered)["status"] == "fail"
    assert raised.returncode == 0
    assert _entry(raised)["status"] == "ok"

    # And the offsets are character offsets into the body that bound exactly the run
    body = (driftwood_vault / NOTE).read_text(encoding="utf-8").split("\n---\n", 1)[1]
    entry = _entry(default)
    start, end = entry["bodyStart"], entry["bodyEnd"]
    assert isinstance(start, int)
    assert isinstance(end, int)
    assert body[start:end] == nine


def test_a_note_with_no_shared_word_is_listed_with_no_offsets(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given a source sharing no word with the body of the note
    source = write_text(tmp_path / "source.txt", "zzz qqq xxx\n")

    # When overlap runs
    result = run_cli(
        "overlap", "--source-text", str(source), "--vault", str(driftwood_vault), "--json"
    )

    # Then the entry has length 0 and null offsets
    entry = _entry(result)
    assert (entry["words"], entry["bodyStart"], entry["bodyEnd"], entry["status"]) == (
        0,
        None,
        None,
        "ok",
    )


def test_named_notes_are_the_only_ones_checked(run_cli: CliRunner, driftwood_vault: Path) -> None:
    # Given a copying note and a clean one
    _plant(driftwood_vault, f"{TEN_WORDS}.")

    # When overlap is given only the clean note, by a path under the vault
    clean = _overlap(run_cli, driftwood_vault, "clauses/cl.setup.md")
    # And only the copying note, by its path as it exists from the working directory
    copying = _overlap(run_cli, driftwood_vault, str(driftwood_vault / NOTE))

    # Then only the named note is listed, and the exit code follows it
    assert clean.returncode == 0
    assert [n["path"] for n in clean.json()["notes"]] == ["clauses/cl.setup.md"]  # type: ignore[attr-defined]
    assert copying.returncode == 1
    assert [n["path"] for n in copying.json()["notes"]] == [NOTE]  # type: ignore[attr-defined]


def test_overlap_refuses_unusable_input(
    run_cli: CliRunner, driftwood_vault: Path, tmp_path: Path
) -> None:
    # Given each way the input can be unusable
    inside = driftwood_vault / "outside-check.txt"
    inside.write_bytes(RULES_E1.read_bytes())
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe")
    vault = str(driftwood_vault)
    cases: dict[str, tuple[str, ...]] = {
        "source-text-inside-vault": ("--source-text", str(inside), "--vault", vault),
        "file-unreadable": ("--source-text", str(tmp_path / "none.txt"), "--vault", vault),
        "text-undecodable": ("--source-text", str(bad), "--vault", vault),
        "threshold-invalid": ("--source-text", str(RULES_E1), "--warn-at", "11", "--vault", vault),
        "path-outside-vault": ("--source-text", str(RULES_E1), str(RULES_E1), "--vault", vault),
        "path-not-a-note": ("--source-text", str(RULES_E1), "spans/dw1.jsonl", "--vault", vault),
        "not-a-vault": ("--source-text", str(RULES_E1), "--vault", str(tmp_path)),
    }

    for code, arguments in cases.items():
        # When the command runs
        result = run_cli("overlap", *arguments, "--json")

        # Then it refuses with that code and exit 3
        assert result.returncode == 3, code
        assert error_code(result) == code
