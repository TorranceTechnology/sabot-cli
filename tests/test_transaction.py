"""Writes into a vault are all-or-nothing: a failure at any step leaves the vault as it was."""

import errno
import json
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import FIXTURES, CliRunner, copy_vault
from sabot_cli.vault import load_vault
from sabot_cli.vault.transaction import (
    LOCK_FILE,
    HardStop,
    Transaction,
    TransactionError,
    atomic_write,
    checkpoint_hook,
)
from source_vaults import error_code, ingest_arguments, segment_arguments, vault_with_sources
from support.filesystem import requires_posix_permissions
from support.vault_state import vault_state

RULES_E1 = FIXTURES / "rules-e1.txt"
RULES_E2 = FIXTURES / "rules-e2.txt"


@dataclass(frozen=True)
class Scenario:
    """One writing command: how to build its vault and the arguments that make it write."""

    name: str
    build: Callable[[CliRunner, Path], Path]
    arguments: Callable[[Path], tuple[str, ...]]


def _fixture(_run_cli: CliRunner, where: Path) -> Path:
    return copy_vault(where / "vault")


def _ingest_vault(_run_cli: CliRunner, where: Path) -> Path:
    return copy_vault(where / "vault")


def _segment_vault(run_cli: CliRunner, where: Path) -> Path:
    return vault_with_sources(run_cli, where / "vault", {"dw1": RULES_E1})


SCENARIOS = (
    Scenario(
        "ingest",
        _ingest_vault,
        lambda vault: (*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault)),
    ),
    Scenario(
        "segment",
        _segment_vault,
        lambda vault: (*segment_arguments(RULES_E1, "dw1"), "--vault", str(vault)),
    ),
    Scenario(
        "migrate-edition",
        _fixture,
        lambda vault: ("migrate-edition", "dw1", "dw2", "--vault", str(vault)),
    ),
    Scenario("compile", _fixture, lambda vault: ("compile", "--vault", str(vault))),
)


def _record_steps(run_cli: CliRunner, scenario: Scenario, where: Path) -> list[tuple[str, int]]:
    steps: list[tuple[str, int]] = []
    vault = scenario.build(run_cli, where)
    with checkpoint_hook(lambda phase, index: steps.append((phase, index))):
        result = run_cli(*scenario.arguments(vault), "--json")
    assert result.returncode == 0, result.stdout
    return steps


def _failing_at(target: tuple[str, int], error: BaseException) -> Callable[[str, int], None]:
    def hook(phase: str, index: int) -> None:
        if (phase, index) == target:
            raise error

    return hook


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_a_failure_after_any_step_leaves_the_vault_unchanged(
    scenario: Scenario, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a writing command and every step its transaction takes
    steps = _record_steps(run_cli, scenario, tmp_path / "probe")
    assert {phase for phase, _ in steps} == {"journaled", "staged", "applied"}

    for number, step in enumerate(steps):
        vault = scenario.build(run_cli, tmp_path / f"run-{number}")
        before = vault_state(vault)

        # When a write error is injected after that step
        failure = OSError(errno.EIO, "injected")
        with checkpoint_hook(_failing_at(step, failure)):
            result = run_cli(*scenario.arguments(vault), "--json")

        # Then the command refuses with write-failed and every byte of the vault is as it was
        assert result.returncode == 3, step
        assert error_code(result) == "write-failed", step
        assert vault_state(vault) == before, step


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_a_kill_at_any_step_is_refused_until_recover_rolls_it_back(
    scenario: Scenario, run_cli: CliRunner, tmp_path: Path
) -> None:
    steps = _record_steps(run_cli, scenario, tmp_path / "probe")

    for number, step in enumerate(steps):
        # Given a vault whose write was killed outright at that step
        vault = scenario.build(run_cli, tmp_path / f"run-{number}")
        before = vault_state(vault)
        with checkpoint_hook(_failing_at(step, HardStop())), pytest.raises(HardStop):
            run_cli(*scenario.arguments(vault), "--json")

        # When any command runs, then it refuses and names the unfinished write
        for command in (
            ("coverage", "--vault", str(vault)),
            ("search", "tide", "--vault", str(vault)),
            scenario.arguments(vault),
        ):
            refused = run_cli(*command, "--json")
            assert refused.returncode == 3, (step, command)
            assert error_code(refused) == "transaction-incomplete", (step, command)

        # And lint reports it instead of refusing
        linted = run_cli("lint", "--vault", str(vault), "--json")
        assert "transaction-incomplete" in {f["class"] for f in linted.json()["findings"]}  # type: ignore[attr-defined, union-attr]

        # And recover returns the vault to its state before the run, so the command works again
        recovered = run_cli("recover", "--vault", str(vault), "--json")
        assert recovered.returncode == 0, step
        assert vault_state(vault) == before, step
        assert run_cli(*scenario.arguments(vault), "--json").returncode == 0, step


def test_the_registration_file_is_applied_last(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given an ingest killed after its first applied operation
    vault = copy_vault(tmp_path / "vault")
    ruleset = (vault / "_ruleset.yaml").read_bytes()
    arguments = (*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault))
    with checkpoint_hook(_failing_at(("applied", 0), HardStop())), pytest.raises(HardStop):
        run_cli(*arguments)

    # Then the new note stands but the ruleset has not yet named the source
    assert (vault / "sources" / "src.dwx.md").is_file()
    assert (vault / "_ruleset.yaml").read_bytes() == ruleset


def test_init_failure_and_kill_leave_no_vault_or_an_empty_one(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a directory that does not exist yet
    target = tmp_path / "parent" / "new-vault"

    # When init fails after its journal is written
    with checkpoint_hook(_failing_at(("journaled", 0), OSError(errno.EIO, "injected"))):
        failed = run_cli("init", str(target), "--ruleset", "driftwood", "--json")

    # Then it refuses and neither the vault nor the parent it created remains
    assert failed.returncode == 3
    assert error_code(failed) == "write-failed"
    assert not (tmp_path / "parent").exists()

    # And a kill leaves a vault only recover can clear, after which init works in the directory
    with checkpoint_hook(_failing_at(("staged", 0), HardStop())), pytest.raises(HardStop):
        run_cli("init", str(target), "--ruleset", "driftwood")
    assert run_cli("recover", "--vault", str(target)).returncode == 0
    assert vault_state(target) == {}
    assert run_cli("init", str(target), "--ruleset", "driftwood").returncode == 0


def test_a_held_lock_refuses_a_second_writer_and_recover_clears_it(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose write lock is held
    vault = copy_vault(tmp_path / "vault")
    (vault / LOCK_FILE).write_bytes(b"")
    before = vault_state(vault)

    # When a writing command runs
    result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then it refuses with vault-locked and writes nothing
    assert result.returncode == 3
    assert error_code(result) == "vault-locked"
    assert vault_state(vault) == before
    # And a reader is not refused
    assert run_cli("coverage", "--vault", str(vault), "--json").returncode == 0
    # And recover clears the lock
    cleared = run_cli("recover", "--vault", str(vault), "--json")
    assert cleared.json()["clearedLock"] is True
    assert not (vault / LOCK_FILE).exists()


def test_the_lock_is_released_after_a_run(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault
    vault = copy_vault(tmp_path / "vault")

    # When a writing command succeeds and when one refuses
    run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault))
    run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault))

    # Then no lock file is left behind
    assert not (vault / LOCK_FILE).exists()


def test_an_interrupt_rolls_back_and_exits_with_interrupted(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault and a Ctrl-C arriving after the first applied operation
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    # When ingest runs
    with checkpoint_hook(_failing_at(("applied", 0), KeyboardInterrupt())):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then it refuses with interrupted and the vault is as it was
    assert result.returncode == 3
    assert error_code(result) == "interrupted"
    assert vault_state(vault) == before


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="the platform has no SIGTERM")
def test_a_termination_signal_rolls_back(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault and a SIGTERM delivered after the first applied operation
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    def terminate(phase: str, index: int) -> None:
        if (phase, index) == ("applied", 0):
            signal.raise_signal(signal.SIGTERM)

    # When ingest runs
    with checkpoint_hook(terminate):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then it refuses with interrupted, the vault is as it was and the old handler is back
    assert result.returncode == 3
    assert error_code(result) == "interrupted"
    assert vault_state(vault) == before
    assert signal.getsignal(signal.SIGTERM) is not None


@requires_posix_permissions
def test_a_read_only_registration_file_refuses_and_writes_nothing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose ruleset file cannot be written
    vault = copy_vault(tmp_path / "vault")
    ruleset = vault / "_ruleset.yaml"
    ruleset.chmod(0o444)
    if os.access(ruleset, os.W_OK):
        ruleset.chmod(0o644)
        pytest.skip("this account can write read-only files")
    before = vault_state(vault)
    store, text_out = tmp_path / "store", tmp_path / "out" / "text.txt"

    # When ingest runs with a store and a text file
    try:
        result = run_cli(
            *ingest_arguments(RULES_E2, "dwx"), "--store", str(store), "--text-out", str(text_out),
            "--vault", str(vault), "--json",
        )  # fmt: skip
    finally:
        ruleset.chmod(0o644)

    # Then it refuses and neither the vault nor the outside files hold anything of the run
    assert result.returncode == 3
    assert error_code(result) == "write-failed"
    assert vault_state(vault) == before
    assert not store.exists()
    assert not text_out.parent.exists()


def test_outside_files_are_removed_when_the_vault_write_fails(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an ingest that stores the original and the text outside the vault
    vault = copy_vault(tmp_path / "vault")
    store, text_out = tmp_path / "store", tmp_path / "out" / "text.txt"

    # When a write error is injected after the vault's first applied operation
    with checkpoint_hook(_failing_at(("applied", 0), OSError(errno.EIO, "injected"))):
        result = run_cli(
            *ingest_arguments(RULES_E2, "dwx"), "--store", str(store), "--text-out", str(text_out),
            "--vault", str(vault), "--json",
        )  # fmt: skip

    # Then the outside files are gone, so a retry is not refused for what it left behind
    assert result.returncode == 3
    assert not store.exists()
    assert not text_out.exists()


def test_staged_files_are_never_read_as_notes(tmp_path: Path) -> None:
    # Given a vault with a hidden staged copy of a note beside the real one
    vault = copy_vault(tmp_path / "vault")
    real = vault / "clauses" / "cl.setup.md"
    (vault / "clauses" / ".sabot-new-0123456789ab-cl.setup.md").write_bytes(real.read_bytes())

    # When the vault is read
    loaded = load_vault(vault, frozenset())

    # Then the staged file is neither a note nor a duplicate of one
    assert loaded.problems == ()
    assert [note.path.as_posix() for note in loaded.notes.values()].count(
        "clauses/cl.setup.md"
    ) == 1
    assert not any(".sabot-new" in note.path.as_posix() for note in loaded.notes.values())


def test_recover_removes_staged_files_and_a_journal_without_a_record(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a staged file and a journal directory that never got its record
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    (vault / "sources" / ".sabot-new-aaaaaaaaaaaa-src.x.md").write_bytes(b"partial")
    (vault / ".sabot-txn-bbbbbbbbbbbb").mkdir()
    (vault / ".sabot-txn-bbbbbbbbbbbb" / "backup").mkdir()

    # When lint runs, it reports both; when recover runs, both are gone and it names them
    linted = run_cli("lint", "--vault", str(vault), "--json")
    paths = {f["path"] for f in linted.json()["findings"]}  # type: ignore[attr-defined, union-attr]
    assert paths == {".sabot-txn-bbbbbbbbbbbb", "sources/.sabot-new-aaaaaaaaaaaa-src.x.md"}
    recovered = run_cli("recover", "--vault", str(vault), "--json").json()
    assert recovered["rolledBack"] == [".sabot-txn-bbbbbbbbbbbb"]
    assert recovered["removedStaged"] == ["sources/.sabot-new-aaaaaaaaaaaa-src.x.md"]
    assert vault_state(vault) == before


def test_recover_on_a_settled_vault_has_nothing_to_do(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault with no unfinished write
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault))

    # Then it says so and changes nothing
    assert result.returncode == 0
    assert result.stdout == "nothing to recover\n"
    assert vault_state(vault) == before


def test_recover_refuses_a_directory_that_does_not_exist(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a missing directory
    # When recover runs
    result = run_cli("recover", "--vault", str(tmp_path / "missing"), "--json")

    # Then it refuses as not a vault
    assert result.returncode == 3
    assert error_code(result) == "not-a-vault"


def test_recover_refuses_a_journal_it_cannot_read(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a journal directory whose record is not a journal
    vault = copy_vault(tmp_path / "vault")
    journal = vault / ".sabot-txn-cccccccccccc"
    journal.mkdir()
    (journal / "journal.json").write_bytes(b"not json")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses by name and leaves the journal for a person to look at
    assert result.returncode == 3
    assert error_code(result) == "journal-invalid"
    assert (journal / "journal.json").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_replaced_file_keeps_its_permissions(tmp_path: Path) -> None:
    # Given a vault file with a distinctive mode
    vault = copy_vault(tmp_path / "vault")
    target = vault / "clauses" / "cl.setup.md"
    target.chmod(0o640)

    # When a transaction replaces it
    with Transaction(vault) as transaction:
        transaction.put("clauses/cl.setup.md", b"replaced\n")
        transaction.commit()

    # Then the content changed and the mode did not
    assert target.read_bytes() == b"replaced\n"
    assert target.stat().st_mode & 0o777 == 0o640


def test_a_transaction_refuses_paths_outside_the_vault(tmp_path: Path) -> None:
    # Given a vault
    vault = copy_vault(tmp_path / "vault")

    # When a path escapes it or names a transaction's own file
    # Then planning it is refused
    with Transaction(vault) as transaction:
        for bad in ("../outside.md", "/abs.md", "a/../b.md", ".sabot-lock", "x\\y.md", ""):
            with pytest.raises(ValueError, match="is not a path inside the vault"):
                transaction.put(bad, b"x")


def test_leaving_a_transaction_without_committing_writes_nothing(tmp_path: Path) -> None:
    # Given a vault and a planned file that is never committed
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    # When the block ends
    with Transaction(vault) as transaction:
        transaction.put("clauses/new.md", b"x")

    # Then nothing changed and the lock is gone
    assert vault_state(vault) == before


def test_entering_a_transaction_on_a_directory_that_is_no_vault_refuses(tmp_path: Path) -> None:
    # Given a directory without a ruleset
    # When a transaction is entered
    # Then it refuses as not a vault
    with pytest.raises(TransactionError) as raised, Transaction(tmp_path):
        pass
    assert raised.value.code == "not-a-vault"


def test_atomic_write_replaces_a_file_without_leaving_a_staged_copy(tmp_path: Path) -> None:
    # Given an existing file
    target = tmp_path / "out" / "graph.json"
    atomic_write(target, b"one")

    # When it is written again
    atomic_write(target, b"two")

    # Then it holds the new bytes and nothing else is in the directory
    assert target.read_bytes() == b"two"
    assert [path.name for path in target.parent.iterdir()] == ["graph.json"]


def test_a_retry_after_a_failed_ingest_is_not_refused_for_what_it_left(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an ingest that failed after writing its note
    vault = copy_vault(tmp_path / "vault")
    arguments = (*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    with checkpoint_hook(_failing_at(("applied", 0), OSError(errno.EIO, "injected"))):
        assert run_cli(*arguments).returncode == 3

    # When the same ingest runs again
    retry = run_cli(*arguments)

    # Then it succeeds: no orphaned source note blocks it, and the vault lints clean
    assert retry.returncode == 0
    assert run_cli("lint", "--vault", str(vault), "--json").returncode == 0


def test_recover_refuses_a_rollback_it_cannot_complete(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a journal that asks for a file to be restored over a directory
    vault = copy_vault(tmp_path / "vault")
    journal = vault / ".sabot-txn-dddddddddddd"
    (journal / "backup").mkdir(parents=True)
    (journal / "backup" / "0").write_bytes(b"old")
    record = {
        "directories": [],
        "format": 1,
        "operations": [
            {
                "backup": "backup/0",
                "kind": "replace",
                "path": "clauses",
                "sha256": "0" * 64,
                "staged": "clauses/.sabot-new-dddddddddddd-x",
            }
        ],
        "token": "dddddddddddd",
    }
    (journal / "journal.json").write_text(json.dumps(record), encoding="utf-8")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses with recovery-failed and keeps the journal for another attempt
    assert result.returncode == 3
    assert error_code(result) == "recovery-failed"
    assert (journal / "journal.json").is_file()
