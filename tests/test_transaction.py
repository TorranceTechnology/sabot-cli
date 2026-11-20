"""Writes into a vault are all-or-nothing: a failure at any step leaves the vault as it was."""

import errno
import json
import os
import shutil
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import FIXTURES, CliRunner, copy_vault
from sabot_cli.vault import load_vault
from sabot_cli.vault import transaction as transaction_module
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
from support.locks import abandon_lock, dead_process_id, write_lock
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


def _run_plan(vault: Path, plan: Callable[[Transaction], None]) -> None:
    with Transaction(vault) as transaction:
        plan(transaction)
        transaction.commit()


def _kill_at(vault: Path, step: tuple[str, int], plan: Callable[[Transaction], None]) -> None:
    """Run `plan` on `vault`, killed outright at `step`, leaving what a dead process leaves."""
    with checkpoint_hook(_failing_at(step, HardStop())), pytest.raises(HardStop):
        _run_plan(vault, plan)
    abandon_lock(vault)


def _kill_a_replace(vault: Path, step: tuple[str, int]) -> None:
    """Replace one note in `vault`, killed outright at `step`, as a dead process leaves it."""
    _kill_at(vault, step, lambda transaction: transaction.put("clauses/cl.setup.md", b"replaced\n"))


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
        abandon_lock(vault)

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
    abandon_lock(target)
    assert run_cli("recover", "--vault", str(target)).returncode == 0
    assert vault_state(target) == {}
    assert run_cli("init", str(target), "--ruleset", "driftwood").returncode == 0


def test_a_lock_left_by_a_process_that_is_gone_is_cleared_by_recover(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose write lock names a process that has exited
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=dead_process_id())
    before = vault_state(vault)

    # When a writing command runs
    result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then it refuses with vault-locked and writes nothing
    assert result.returncode == 3
    assert error_code(result) == "vault-locked"
    assert vault_state(vault) == before
    # And a reader is not refused, since nobody is writing
    assert run_cli("coverage", "--vault", str(vault), "--json").returncode == 0
    # And recover clears the lock without --force
    cleared = run_cli("recover", "--vault", str(vault), "--json")
    assert cleared.returncode == 0
    assert cleared.json()["clearedLock"] is True
    assert not (vault / LOCK_FILE).exists()


def test_a_lock_held_by_a_running_process_refuses_every_command_and_recover(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose write lock names a process that is running (this one)
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=os.getpid())
    before = vault_state(vault)

    # When any command runs, even a reader, and when recover runs without --force
    for command in (
        ("coverage",),
        ("search", "tide"),
        ("lint",),
        ingest_arguments(RULES_E2, "dwx"),
        ("recover",),
    ):
        result = run_cli(*command, "--vault", str(vault), "--json")

        # Then each refuses with vault-locked, says a write is in progress and changes nothing
        assert result.returncode == 3, command
        assert error_code(result) == "vault-locked", command
        assert "in progress" in result.stdout, command
        assert vault_state(vault) == before, command

    # And --force clears it
    forced = run_cli("recover", "--force", "--vault", str(vault), "--json")
    assert forced.returncode == 0
    assert not (vault / LOCK_FILE).exists()


def test_a_reader_during_a_write_is_told_a_write_is_in_progress_not_that_one_was_left(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a write that has journaled its first change and is still running
    vault = copy_vault(tmp_path / "vault")
    seen: list[tuple[int, str]] = []

    def read_meanwhile(phase: str, index: int) -> None:
        if (phase, index) == ("applied", 0):
            during = run_cli("coverage", "--vault", str(vault), "--json")
            recovery = run_cli("recover", "--vault", str(vault), "--json")
            seen.extend([(during.returncode, error_code(during)), (3, error_code(recovery))])

    # When a reader and recover run from inside it
    with checkpoint_hook(read_meanwhile):
        written = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then both are told a write is in progress, and the write completes
    assert seen == [(3, "vault-locked"), (3, "vault-locked")]
    assert written.returncode == 0
    assert run_cli("lint", "--vault", str(vault), "--json").returncode == 0


def test_a_lock_from_another_host_or_unreadable_is_cleared_only_with_force(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    for number, plant in enumerate(
        (
            lambda vault: write_lock(vault, pid=dead_process_id(), host="another-host.invalid"),
            lambda vault: (vault / LOCK_FILE).write_bytes(b""),
        )
    ):
        # Given a vault whose lock names another host, or holds nothing readable
        vault = copy_vault(tmp_path / f"vault-{number}")
        plant(vault)

        # When recover runs without --force
        refused = run_cli("recover", "--vault", str(vault), "--json")

        # Then it refuses and keeps the lock; with --force it clears it
        assert refused.returncode == 3
        assert error_code(refused) == "vault-locked"
        assert (vault / LOCK_FILE).exists()
        forced = run_cli("recover", "--force", "--vault", str(vault), "--json")
        assert forced.returncode == 0
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

    # Then it refuses with interrupted and the vault is as it was
    assert result.returncode == 3
    assert error_code(result) == "interrupted"
    assert vault_state(vault) == before


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="the platform has no SIGTERM")
@pytest.mark.parametrize("fails", [False, True], ids=["succeeds", "fails"])
def test_the_signal_handlers_in_place_before_a_write_are_back_after_it(
    fails: bool, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given handlers of the caller's own for the signals a transaction takes over
    def own_handler(_signum: int, _frame: object) -> None:
        raise AssertionError("the caller's handler ran")

    numbers = [signal.SIGTERM, signal.SIGINT]
    previous = {number: signal.signal(number, own_handler) for number in numbers}
    vault = copy_vault(tmp_path / "vault")
    try:
        # When a write runs, whether it commits or fails
        def hook(phase: str, index: int) -> None:
            if fails and (phase, index) == ("applied", 0):
                raise OSError(errno.EIO, "injected")

        with checkpoint_hook(hook):
            run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

        # Then each handler is the caller's own again
        assert [signal.getsignal(number) for number in numbers] == [own_handler] * len(numbers)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="the platform has no SIGTERM")
def test_a_termination_signal_during_rollback_waits_until_the_rollback_is_done(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a write that fails after its first applied operation, and a SIGTERM that arrives
    # while the rollback removes that operation's file
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    rolling_back = False
    real_unlink = Path.unlink

    def fail(phase: str, index: int) -> None:
        nonlocal rolling_back
        if (phase, index) == ("applied", 0):
            rolling_back = True
            raise OSError(errno.EIO, "injected")

    def unlink(self: Path, missing_ok: bool = False) -> None:
        nonlocal rolling_back
        if rolling_back:
            rolling_back = False
            signal.raise_signal(signal.SIGTERM)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    # When ingest runs
    with checkpoint_hook(fail):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    monkeypatch.undo()

    # Then the rollback finished, so the vault is as it was, and the run still ends as interrupted
    assert result.returncode == 3
    assert error_code(result) == "interrupted"
    assert "left as it was" in result.stdout
    assert vault_state(vault) == before


def test_an_interrupt_whose_rollback_fails_says_so_and_recover_finishes_it(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a Ctrl-C after the first applied operation, and a rollback that cannot remove files
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    failing = False
    real_unlink = Path.unlink

    def interrupt(phase: str, index: int) -> None:
        nonlocal failing
        if (phase, index) == ("applied", 0):
            failing = True
            raise KeyboardInterrupt

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if failing and self.name != LOCK_FILE:
            raise PermissionError(errno.EACCES, "held open")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    # When ingest runs
    with checkpoint_hook(interrupt):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    monkeypatch.undo()

    # Then the message does not claim the vault was left as it was, and names recover
    assert error_code(result) == "interrupted"
    message = str(result.json()["error"]["message"])  # type: ignore[index]
    assert "left as it was" not in message
    assert "sabot recover" in message
    # And recover restores the vault
    assert run_cli("recover", "--vault", str(vault), "--json").returncode == 0
    assert vault_state(vault) == before


def test_an_interrupt_after_the_commit_point_says_the_write_is_complete(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a Ctrl-C that arrives once journal.json is removed, while the rest of the journal
    # directory is being removed
    vault = copy_vault(tmp_path / "vault")
    real_remove = transaction_module.shutil.rmtree
    interrupted = False

    def remove(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal interrupted
        if not interrupted and Path(path).name.startswith(".sabot-txn-"):
            interrupted = True
            raise KeyboardInterrupt
        real_remove(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(transaction_module.shutil, "rmtree", remove)

    # When ingest runs
    result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    monkeypatch.undo()

    # Then it reports the interruption but not that the vault was left as it was, since the
    # write is in place, and no journal or lock is left behind
    assert error_code(result) == "interrupted"
    assert "write was complete" in result.stdout
    assert (vault / "sources" / "src.dwx.md").is_file()
    assert not list(vault.glob(".sabot-*"))
    assert run_cli("lint", "--vault", str(vault), "--json").returncode == 0


def test_a_staged_file_that_changed_before_it_was_applied_is_not_applied(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a write whose staged file is altered after it was staged
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    def tamper(phase: str, index: int) -> None:
        if (phase, index) == ("staged", 0):
            for staged in vault.rglob(".sabot-new-*"):
                staged.write_bytes(b"tampered")

    # When ingest runs
    with checkpoint_hook(tamper):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then it refuses and the vault is as it was
    assert result.returncode == 3
    assert error_code(result) == "write-failed"
    assert vault_state(vault) == before


def test_init_below_a_file_refuses_as_write_failed_even_when_mkdir_reports_an_existing_name(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a path below a file, and a platform whose mkdir reports that as "already exists"
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file")
    real_mkdir = Path.mkdir

    def mkdir(self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
        if blocker in (self, *self.parents):
            raise FileExistsError(errno.EEXIST, "File exists")
        real_mkdir(self, mode, parents, exist_ok)

    monkeypatch.setattr(Path, "mkdir", mkdir)

    # When init runs
    result = run_cli("init", str(blocker / "vault"), "--ruleset", "driftwood", "--json")

    # Then it refuses as a write failure, not as a lock held by another process
    assert result.returncode == 3
    assert error_code(result) == "write-failed"


@requires_posix_permissions
def test_recover_restores_a_file_whose_staged_copy_is_read_only(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a replace killed after staging, whose staged copy then became read-only
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    _kill_a_replace(vault, ("staged", 0))
    for staged in vault.rglob(".sabot-new-*"):
        staged.chmod(0o444)
        if os.access(staged, os.W_OK):
            staged.chmod(0o644)
            pytest.skip("this account can write read-only files")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then the file is restored and nothing is left
    assert result.returncode == 0
    assert vault_state(vault) == before


def test_recover_refuses_a_replace_whose_backup_is_missing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a replace killed after its first applied operation, and its backup then lost
    vault = copy_vault(tmp_path / "vault")
    _kill_a_replace(vault, ("applied", 0))
    (backup,) = vault.glob(".sabot-txn-*/backup/0")
    backup.unlink()

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses with recovery-failed instead of reporting success, and keeps the journal
    assert result.returncode == 3
    assert error_code(result) == "recovery-failed"
    assert (vault / "clauses" / "cl.setup.md").read_bytes() == b"replaced\n"
    assert list(vault.glob(".sabot-txn-*/journal.json"))


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


def test_recover_removes_staged_files_and_lint_reports_them(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a staged file left by a write that did not finish
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    (vault / "sources" / ".sabot-new-aaaaaaaaaaaa-src.x.md").write_bytes(b"partial")

    # When lint runs, it reports the file; when recover runs, it is gone and recover names it
    linted = run_cli("lint", "--vault", str(vault), "--json")
    paths = {f["path"] for f in linted.json()["findings"]}  # type: ignore[attr-defined, union-attr]
    assert paths == {"sources/.sabot-new-aaaaaaaaaaaa-src.x.md"}
    recovered = run_cli("recover", "--vault", str(vault), "--json").json()
    assert recovered["rolledBack"] == []
    assert recovered["removedStaged"] == ["sources/.sabot-new-aaaaaaaaaaaa-src.x.md"]
    assert vault_state(vault) == before


def test_a_journal_directory_without_a_record_is_committed_and_blocks_nothing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal directory whose record is gone, as a finished write leaves it when
    # something held a file open while the directory was removed
    vault = copy_vault(tmp_path / "vault")
    leftover = vault / ".sabot-txn-bbbbbbbbbbbb"
    (leftover / "backup").mkdir(parents=True)
    (leftover / "backup" / "0").write_bytes(b"old")

    # When a reader and lint run
    # Then neither refuses nor reports it, and neither touches it
    assert run_cli("search", "tide", "--vault", str(vault), "--json").returncode == 0
    linted = run_cli("lint", "--vault", str(vault), "--json")
    assert linted.returncode == 0
    assert not any(".sabot-txn" in str(f["path"]) for f in linted.json()["findings"])  # type: ignore[attr-defined, union-attr]
    assert leftover.is_dir()

    # And the next writer removes it without a word
    written = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    assert written.returncode == 0
    assert not leftover.exists()

    # And recover removes one too, without listing it as rolled back
    leftover.mkdir()
    recovered = run_cli("recover", "--vault", str(vault), "--json")
    assert recovered.json()["rolledBack"] == []
    assert not leftover.exists()


def test_removing_a_journal_tries_again_when_a_file_is_briefly_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a removal that fails twice, as it does while a scanner holds a file open
    vault = copy_vault(tmp_path / "vault")
    real_rmtree = shutil.rmtree
    attempts: list[Path] = []

    def holding(path: Path, *args: object, **kwargs: object) -> None:
        attempts.append(Path(path))
        if len(attempts) <= 2:
            raise PermissionError(errno.EACCES, "held open")
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(transaction_module.shutil, "rmtree", holding)
    monkeypatch.setattr(transaction_module.time, "sleep", lambda _seconds: None)

    # When a transaction commits
    with Transaction(vault) as transaction:
        transaction.put("clauses/new.md", b"x")
        transaction.commit()

    # Then the removal was retried until it worked and no journal directory remains
    assert len(attempts) == 3
    assert not list(vault.glob(".sabot-txn-*"))


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
                "staged": ".sabot-new-dddddddddddd-clauses",
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


def _plant_journal(vault: Path, record: dict[str, object]) -> Path:
    token = str(record["token"])
    directory = vault / f".sabot-txn-{token}"
    (directory / "backup").mkdir(parents=True)
    (directory / "backup" / "0").write_bytes(b"ATTACKER")
    (directory / "journal.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


def _operation(
    token: str, *, path: str = "clauses/cl.setup.md", **overrides: object
) -> dict[str, object]:
    name = path.rsplit("/", 1)[-1]
    parent = path[: -len(name)]
    operation: dict[str, object] = {
        "backup": "backup/0",
        "kind": "replace",
        "path": path,
        "sha256": "0" * 64,
        "staged": f"{parent}.sabot-new-{token}-{name}",
    }
    operation.update(overrides)
    return operation


_TOKEN = "eeeeeeeeeeee"
_JOURNAL_FLAWS: dict[str, Callable[[Path], dict[str, object]]] = {
    "path above the vault": lambda outside: {"path": "../victim.txt", "staged": "../x"},
    "absolute path": lambda outside: {"path": str(outside / "victim.txt"), "staged": "x"},
    "staged file elsewhere": lambda outside: {
        "path": "clauses/cl.setup.md",
        "staged": f"../.sabot-new-{_TOKEN}-cl.setup.md",
    },
    "staged file of another name": lambda outside: {
        "path": "clauses/cl.setup.md",
        "staged": f"clauses/.sabot-new-{_TOKEN}-other.md",
    },
    "backup above the journal": lambda outside: {"backup": "../../../victim.txt"},
    "absolute backup": lambda outside: {"backup": str(outside / "victim.txt")},
    "unknown operation": lambda outside: {"kind": "delete"},
    "replace without a backup": lambda outside: {"backup": None},
    "create with a backup": lambda outside: {"kind": "create"},
    "malformed digest": lambda outside: {"sha256": "nothex"},
    "path of the journal's own": lambda outside: {
        "path": f".sabot-txn-{_TOKEN}/journal.json",
        "staged": f".sabot-txn-{_TOKEN}/.sabot-new-{_TOKEN}-journal.json",
    },
}


@pytest.mark.parametrize("flaw", sorted(_JOURNAL_FLAWS))
def test_recover_refuses_a_journal_that_names_files_outside_the_vault(
    flaw: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given files outside a vault, and a planted journal that points a replace and a create at
    # them (a vault shared through version control can carry such a directory)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim, other = outside / "victim.txt", outside / "victim2.txt"
    victim.write_bytes(b"original")
    other.write_bytes(b"also original")
    vault = copy_vault(tmp_path / "vault")
    first = _operation(_TOKEN, **_JOURNAL_FLAWS[flaw](outside))
    second = _operation(_TOKEN, path="../victim2.txt", kind="create", backup=None)
    _plant_journal(
        vault,
        {
            "directories": ["../newdir"],
            "format": 1,
            "operations": [first, second],
            "token": _TOKEN,
        },
    )
    before_vault = vault_state(vault)
    before_outside = vault_state(outside)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses the journal and nothing outside the vault, or inside it, is touched
    assert result.returncode == 3
    assert error_code(result) == "journal-invalid"
    assert vault_state(outside) == before_outside
    assert not (tmp_path / "newdir").exists()
    assert vault_state(vault) == before_vault


def test_recover_refuses_a_journal_whose_token_is_not_its_directory(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal directory holding a record of another token
    vault = copy_vault(tmp_path / "vault")
    record = {
        "directories": [],
        "format": 1,
        "operations": [_operation("ffffffffffff")],
        "token": "ffffffffffff",
    }
    directory = _plant_journal(vault, record)
    directory.rename(vault / f".sabot-txn-{_TOKEN}")
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and changes nothing
    assert error_code(result) == "journal-invalid"
    assert vault_state(vault) == before


def test_recover_does_not_write_through_a_link_that_leaves_the_vault(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault directory that is a link to a directory outside, and a well-formed journal
    # whose create operation names a file below it
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cl.victim.md").write_bytes(b"original")
    vault = copy_vault(tmp_path / "vault")
    try:
        (vault / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    _plant_journal(
        vault,
        {
            "directories": [],
            "format": 1,
            "operations": [
                _operation(_TOKEN, path="linked/cl.victim.md", kind="create", backup=None)
            ],
            "token": _TOKEN,
        },
    )
    before_outside = vault_state(outside)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses to roll back through the link and the outside file is intact
    assert result.returncode == 3
    assert error_code(result) == "recovery-failed"
    assert vault_state(outside) == before_outside


def test_a_deleted_file_is_put_back_by_a_rollback_and_a_missing_one_is_ignored(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a stray file, and a transaction that deletes it, another file that
    # is not there, and replaces a note
    vault = copy_vault(tmp_path / "vault")
    stray = vault / "spans" / "stray.jsonl"
    stray.write_bytes(b"stray\n")
    before = vault_state(vault)

    def plan(transaction: Transaction) -> None:
        transaction.delete("spans/stray.jsonl")
        transaction.delete("spans/absent.jsonl")
        transaction.put("clauses/cl.setup.md", b"replaced\n")

    # When a write error is injected after each step in turn
    steps: list[tuple[str, int]] = []
    with checkpoint_hook(lambda phase, index: steps.append((phase, index))):
        _run_plan(vault, plan)
    assert not stray.exists()
    assert {phase for phase, _ in steps} == {"journaled", "staged", "applied"}
    stray.write_bytes(b"stray\n")
    (vault / "clauses" / "cl.setup.md").write_bytes(before["clauses/cl.setup.md"] or b"")
    for step in steps:
        failing = checkpoint_hook(_failing_at(step, OSError(errno.EIO, "injected")))
        with failing, pytest.raises(TransactionError) as raised:
            _run_plan(vault, plan)

        # Then each failure leaves the vault exactly as it was, the deleted file included
        assert raised.value.code == "write-failed", step
        assert vault_state(vault) == before, step

    # And a kill after the delete is rolled back by recover
    _kill_at(vault, ("applied", 0), plan)
    assert run_cli("recover", "--vault", str(vault), "--json").returncode == 0
    assert vault_state(vault) == before
