"""Writes into a vault are all-or-nothing: a failure at any step leaves the vault as it was."""

import errno
import hashlib
import json
import os
import shutil
import signal
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from conftest import FIXTURES, CliRunner, copy_vault
from sabot_cli.testing import HardStop, checkpoint_hook
from sabot_cli.vault import load_vault
from sabot_cli.vault import transaction as transaction_module
from sabot_cli.vault.transaction import (
    LOCK_FILE,
    Transaction,
    TransactionError,
    atomic_write,
    is_vault_path,
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


# A scenario with more steps than this runs a sample of them: every step costs a copy of the
# vault and a full run, which on Windows adds up to minutes for the 99-step migration.
_EXHAUSTIVE_LIMIT = 40
_SAMPLE_EVERY = 10


def _steps_to_try(steps: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Every step of a small scenario; of a large one, each phase's ends and every tenth."""
    if len(steps) <= _EXHAUSTIVE_LIMIT:
        return steps
    chosen = set(range(0, len(steps), _SAMPLE_EVERY))
    for phase in {phase for phase, _ in steps}:
        positions = [number for number, (name, _) in enumerate(steps) if name == phase]
        chosen |= {positions[0], positions[-1]}
    return [steps[number] for number in sorted(chosen)]


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

    for number, step in enumerate(_steps_to_try(steps)):
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

    for number, step in enumerate(_steps_to_try(steps)):
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
    # Given a removal that fails four times, as it does while a scanner holds a file open
    vault = copy_vault(tmp_path / "vault")
    real_rmtree = shutil.rmtree
    attempts: list[Path] = []

    def holding(path: Path, *args: object, **kwargs: object) -> None:
        attempts.append(Path(path))
        if len(attempts) <= 4:
            raise PermissionError(errno.EACCES, "held open")
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(transaction_module.shutil, "rmtree", holding)
    monkeypatch.setattr(transaction_module.time, "sleep", lambda _seconds: None)

    # When a transaction commits
    with Transaction(vault) as transaction:
        transaction.put("clauses/new.md", b"x")
        transaction.commit()

    # Then the removal was retried until it worked and no journal directory remains
    assert len(attempts) == 5
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
    _plant_journal(vault, _record([_operation("clauses")]))
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses with recovery-failed and keeps the journal for another attempt
    assert result.returncode == 3
    assert error_code(result) == "recovery-failed"
    assert vault_state(vault) == before


_TOKEN = "eeeeeeeeeeee"


def _record(
    operations: list[dict[str, object]],
    *,
    directories: tuple[str, ...] = (),
    host: str | None = None,
) -> dict[str, object]:
    return {
        "directories": list(directories),
        "format": 2,
        "host": socket.gethostname() if host is None else host,
        "operations": operations,
        "token": _TOKEN,
    }


def _operation(
    path: str = "clauses/cl.setup.md",
    *,
    kind: str = "replace",
    sha256: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    name = path.rsplit("/", 1)[-1]
    parent = path[: -len(name)]
    puts_back = kind != "create"
    operation: dict[str, object] = {
        "backup": "backup/0" if puts_back else None,
        "kind": kind,
        "mode": 0o644 if puts_back else None,
        "path": path,
        "sha256": sha256 or "0" * 64,
        "staged": f"{parent}.sabot-new-{_TOKEN}-{name}",
    }
    operation.update(overrides)
    return operation


def _plant_journal(vault: Path, record: dict[str, object], *, backup: bytes = b"ATTACKER") -> Path:
    directory = vault / f".sabot-txn-{record['token']}"
    (directory / "backup").mkdir(parents=True)
    (directory / "backup" / "0").write_bytes(backup)
    (directory / "journal.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_JOURNAL_FLAWS: dict[str, Callable[[Path], dict[str, object]]] = {
    "path above the vault": lambda outside: {
        "path": "../victim.txt",
        "staged": f"../.sabot-new-{_TOKEN}-victim.txt",
    },
    "absolute path": lambda outside: {
        "path": str(outside / "victim.txt"),
        "staged": f"{outside}/.sabot-new-{_TOKEN}-victim.txt",
    },
    "hidden directory": lambda outside: {
        "path": ".git/hooks/pre-commit",
        "staged": f".git/hooks/.sabot-new-{_TOKEN}-pre-commit",
    },
    "hidden file": lambda outside: {
        "path": "clauses/.cl.setup.md",
        "staged": f"clauses/.sabot-new-{_TOKEN}-.cl.setup.md",
    },
    "staged file elsewhere": lambda outside: {"staged": f"../.sabot-new-{_TOKEN}-cl.setup.md"},
    "staged file of another name": lambda outside: {
        "staged": f"clauses/.sabot-new-{_TOKEN}-other.md"
    },
    "backup above the journal": lambda outside: {"backup": "../../../victim.txt"},
    "absolute backup": lambda outside: {"backup": str(outside / "victim.txt")},
    "unknown operation": lambda outside: {"kind": "rewrite"},
    "replace without a backup": lambda outside: {"backup": None},
    "replace without a mode": lambda outside: {"mode": None},
    "mode out of range": lambda outside: {"mode": 0o77777},
    "create with a backup": lambda outside: {"kind": "create", "backup": "backup/0", "mode": 0o644},
    "malformed digest": lambda outside: {"sha256": "nothex"},
    "path of the journal's own": lambda outside: {
        "path": f".sabot-txn-{_TOKEN}/journal.json",
        "staged": f".sabot-txn-{_TOKEN}/.sabot-new-{_TOKEN}-journal.json",
    },
}


@pytest.mark.parametrize("flaw", sorted(_JOURNAL_FLAWS))
def test_recover_refuses_a_journal_that_names_what_it_may_not(
    flaw: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given files outside a vault, and a planted journal of one operation with one flaw (a
    # vault shared through version control can carry such a directory)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_bytes(b"original")
    (tmp_path / f".sabot-new-{_TOKEN}-cl.setup.md").write_bytes(b"a staged victim")
    (tmp_path / f".sabot-new-{_TOKEN}-victim.txt").write_bytes(b"another victim")
    vault = copy_vault(tmp_path / "vault")
    setup = (vault / "clauses" / "cl.setup.md").read_bytes()
    operation = _operation(**{"sha256": _sha(setup), **_JOURNAL_FLAWS[flaw](outside)})
    _plant_journal(vault, _record([operation]))
    before = vault_state(tmp_path)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses the journal and nothing, in the vault or beside it, is touched
    assert result.returncode == 3
    assert error_code(result) == "journal-invalid"
    assert vault_state(tmp_path) == before


def test_recover_refuses_a_journal_that_names_a_directory_it_may_not(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal whose created-directory list reaches above the vault and into .git
    (tmp_path / "newdir").mkdir()
    vault = copy_vault(tmp_path / "vault")
    for name in ("../newdir", ".git"):
        directory = _plant_journal(vault, _record([], directories=(name,)))
        before = vault_state(tmp_path)

        # When recover runs
        result = run_cli("recover", "--vault", str(vault), "--json")

        # Then it refuses and nothing changes
        assert error_code(result) == "journal-invalid", name
        assert vault_state(tmp_path) == before, name
        shutil.rmtree(directory)


def test_recover_accepts_a_well_formed_journal_and_restores_what_it_names(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal of the shape this tool writes, for a note that holds what the write put
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    written = note.read_bytes()
    _plant_journal(vault, _record([_operation(sha256=_sha(written))]), backup=b"the original\n")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then the note holds the backup's bytes and the journal is gone
    assert result.returncode == 0
    assert note.read_bytes() == b"the original\n"
    assert not list(vault.glob(".sabot-txn-*"))


def test_recover_installs_no_hook_below_git_and_changes_nothing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a .git directory and a planted journal that replaces a git hook with an
    # executable script
    vault = copy_vault(tmp_path / "vault")
    (vault / ".git" / "hooks").mkdir(parents=True)
    (vault / ".git" / "hooks" / "pre-commit.sample").write_bytes(b"sample\n")
    hook = _operation(
        ".git/hooks/pre-commit", kind="create", staged=f".git/hooks/.sabot-new-{_TOKEN}-pre-commit"
    )
    hook.update(kind="replace", backup="backup/0", mode=0o755)
    _plant_journal(vault, _record([hook]), backup=b"#!/bin/sh\necho pwned > pwned.txt\n")
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and no hook exists
    assert result.returncode == 3
    assert error_code(result) == "journal-invalid"
    assert not (vault / ".git" / "hooks" / "pre-commit").exists()
    assert vault_state(vault) == before


def test_recover_deletes_neither_git_head_nor_a_note_a_journal_names(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with .git/HEAD and a planted journal that creates it and a note, the note's
    # digest being the digest of what it holds now
    vault = copy_vault(tmp_path / "vault")
    (vault / ".git").mkdir()
    (vault / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    note = vault / "clauses" / "cl.setup.md"
    digest = _sha(note.read_bytes())
    head = _operation(".git/HEAD", kind="create", sha256=digest)
    victim = _operation("clauses/cl.setup.md", kind="create", sha256=digest)
    _plant_journal(vault, _record([head, victim]))
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses the journal as a whole: both files are still there
    assert result.returncode == 3
    assert error_code(result) == "journal-invalid"
    assert vault_state(vault) == before


@dataclass(frozen=True)
class _Case:
    kind: str
    current: bytes | None  # what the target holds when recover runs
    expect: bytes | Literal["refused"] | None  # what it holds afterwards


_NEW, _OLD = b"what the write put there\n", b"what the write replaced\n"
_CASES = {
    "create, file is absent": _Case("create", None, None),
    "create, file holds the journaled bytes": _Case("create", _NEW, None),
    "create, file holds other bytes": _Case("create", b"edited since\n", "refused"),
    "replace, file holds the journaled bytes": _Case("replace", _NEW, _OLD),
    "replace, file already holds the backup": _Case("replace", _OLD, _OLD),
    "replace, file holds other bytes": _Case("replace", b"edited since\n", "refused"),
    "replace, file is gone": _Case("replace", None, "refused"),
    "delete, file is absent": _Case("delete", None, _OLD),
    "delete, file already holds the backup": _Case("delete", _OLD, _OLD),
    "delete, file holds other bytes": _Case("delete", b"edited since\n", "refused"),
}


@pytest.mark.parametrize("name", sorted(_CASES))
def test_recover_changes_a_file_only_when_it_holds_what_the_journal_says(
    name: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a planted journal of one operation, and the file it names holding some bytes
    case = _CASES[name]
    vault = copy_vault(tmp_path / "vault")
    target = vault / "clauses" / "cl.probe.md"
    if case.current is not None:
        target.write_bytes(case.current)
    operation = _operation("clauses/cl.probe.md", kind=case.kind, sha256=_sha(_NEW))
    _plant_journal(vault, _record([operation]), backup=_OLD)
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    if case.expect == "refused":
        # Then it refuses, and the file, the journal and everything else are as they were
        assert result.returncode == 3
        assert error_code(result) == "recovery-failed"
        assert vault_state(vault) == before
    else:
        # Then the file holds what the rule says, and the journal is gone
        assert result.returncode == 0
        assert (target.read_bytes() if target.exists() else None) == case.expect
        assert not list(vault.glob(".sabot-txn-*"))


def test_one_operation_that_cannot_be_verified_stops_the_whole_recovery(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal whose first operation is verifiable (a created file holding what was
    # journaled) and whose second is not (a replaced file edited since)
    vault = copy_vault(tmp_path / "vault")
    created = vault / "clauses" / "cl.created.md"
    created.write_bytes(_NEW)
    edited = vault / "clauses" / "cl.setup.md"
    edited_bytes = edited.read_bytes()
    operations = [
        _operation("clauses/cl.created.md", kind="create", sha256=_sha(_NEW)),
        _operation("clauses/cl.setup.md", sha256=_sha(_NEW), backup="backup/0"),
    ]
    _plant_journal(vault, _record(operations), backup=_OLD)
    before = vault_state(vault)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and the verifiable operation was not carried out either
    assert error_code(result) == "recovery-failed"
    assert created.read_bytes() == _NEW
    assert edited.read_bytes() == edited_bytes
    assert vault_state(vault) == before


@requires_posix_permissions
@pytest.mark.parametrize(
    ("current_mode", "recorded_mode", "expected"),
    [
        (0o644, 0o755, 0o644),
        (0o755, 0o755, 0o755),
        (0o755, 0o644, 0o644),
        (0o644, 0o4755, 0o644),
        (0o640, 0o640, 0o640),
    ],
    ids=[
        "a journal cannot make a file executable",
        "an executable file stays executable",
        "an execute bit the journal does not record is not given",
        "set-id bits are never restored",
        "other bits are the recorded ones",
    ],
)
def test_a_restored_file_never_gains_execute_or_set_id_bits_from_a_journal(
    current_mode: int, recorded_mode: int, expected: int, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a planted replace of a file that holds the journaled bytes, with a recorded mode
    vault = copy_vault(tmp_path / "vault")
    target = vault / "clauses" / "cl.probe.md"
    target.write_bytes(_NEW)
    target.chmod(current_mode)
    operation = _operation("clauses/cl.probe.md", sha256=_sha(_NEW), mode=recorded_mode)
    _plant_journal(vault, _record([operation]), backup=_OLD)

    # When recover runs
    assert run_cli("recover", "--vault", str(vault)).returncode == 0

    # Then the file holds the backup, with the bits the rule allows
    assert target.read_bytes() == _OLD
    assert target.stat().st_mode & 0o7777 == expected


@requires_posix_permissions
def test_a_file_deleted_by_a_write_comes_back_without_execute_bits(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a planted delete of a file that is absent, recorded as executable
    vault = copy_vault(tmp_path / "vault")
    operation = _operation("clauses/cl.probe.md", kind="delete", mode=0o755)
    _plant_journal(vault, _record([operation]), backup=_OLD)

    # When recover runs
    assert run_cli("recover", "--vault", str(vault)).returncode == 0

    # Then the file is back, not executable
    restored = vault / "clauses" / "cl.probe.md"
    assert restored.read_bytes() == _OLD
    assert restored.stat().st_mode & 0o111 == 0


@requires_posix_permissions
def test_a_killed_replace_of_an_executable_file_recovers_its_bytes_and_mode(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an executable note whose replacement was killed after it was applied
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    note.chmod(0o750)
    before = vault_state(vault)
    _kill_a_replace(vault, ("applied", 0))

    # When recover runs
    assert run_cli("recover", "--vault", str(vault)).returncode == 0

    # Then its bytes and its mode are what they were
    assert vault_state(vault) == before
    assert note.stat().st_mode & 0o777 == 0o750


def test_recover_refuses_a_journal_from_another_host_until_forced(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal of the right shape that another host wrote
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    operation = _operation(sha256=_sha(note.read_bytes()))
    _plant_journal(vault, _record([operation], host="another-host.invalid"), backup=_OLD)
    before = vault_state(vault)

    # When recover runs without --force
    refused = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and changes nothing
    assert refused.returncode == 3
    assert error_code(refused) == "journal-other-host"
    assert vault_state(vault) == before

    # And with --force it rolls the journal back, the other checks still holding
    forced = run_cli("recover", "--force", "--vault", str(vault), "--json")
    assert forced.returncode == 0
    assert note.read_bytes() == _OLD


def test_recover_refuses_a_journal_directory_that_is_a_link(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal outside the vault, and a link to it in the vault under a journal's name
    elsewhere = tmp_path / "elsewhere"
    note = tmp_path / "vault-copy" / "clauses" / "cl.setup.md"
    note.parent.mkdir(parents=True)
    note.write_bytes(b"x")
    staging = tmp_path / "staging"
    staging.mkdir()
    _plant_journal(staging, _record([_operation(sha256=_sha(b"x"))]))
    shutil.move(staging / f".sabot-txn-{_TOKEN}", elsewhere)
    vault = copy_vault(tmp_path / "vault")
    try:
        (vault / f".sabot-txn-{_TOKEN}").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    before = vault_state(tmp_path)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and nothing, linked or not, changes
    assert error_code(result) == "journal-invalid"
    assert vault_state(tmp_path) == before


def test_recover_does_not_follow_a_backup_directory_that_is_a_link(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal whose backup directory is a link to a directory outside the vault, which
    # holds a file named for the backup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "0").write_bytes(b"LEAKED")
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    directory = _plant_journal(vault, _record([_operation(sha256=_sha(note.read_bytes()))]))
    shutil.rmtree(directory / "backup")
    try:
        (directory / "backup").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    before = vault_state(tmp_path)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses and the outside file was not copied into the vault
    assert error_code(result) == "journal-invalid"
    assert note.read_bytes() != b"LEAKED"
    assert vault_state(tmp_path) == before


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
    operation = _operation("linked/cl.victim.md", kind="create", sha256=_sha(b"original"))
    _plant_journal(vault, _record([operation]))
    before_outside = vault_state(outside)

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses to roll back through the link and the outside file is intact
    assert result.returncode == 3
    assert error_code(result) == "recovery-failed"
    assert vault_state(outside) == before_outside


def test_recover_does_not_replace_a_link_inside_the_vault(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a note that is a link to a file outside, and a journal that replaces the note with
    # the digest of what the link reads
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"original")
    vault = copy_vault(tmp_path / "vault")
    try:
        (vault / "clauses" / "cl.link.md").symlink_to(outside)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    operation = _operation("clauses/cl.link.md", sha256=_sha(b"original"))
    _plant_journal(vault, _record([operation]), backup=b"ATTACKER")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it refuses, and the outside file is intact
    assert error_code(result) == "recovery-failed"
    assert outside.read_bytes() == b"original"


def test_a_planned_file_below_a_file_is_refused_before_a_journal_exists(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault in which `_index` is a file, so `_index/graph.json` cannot be made
    vault = copy_vault(tmp_path / "vault")
    (vault / "_index").write_bytes(b"in the way\n")
    before = vault_state(vault)

    # When compile runs
    result = run_cli("compile", "--vault", str(vault), "--json")

    # Then it refuses as a write failure that did not change anything, and leaves no journal
    assert result.returncode == 3
    assert error_code(result) == "write-failed"
    assert "recover" not in result.stdout
    assert vault_state(vault) == before
    assert run_cli("search", "tide", "--vault", str(vault), "--json").returncode == 0


def test_recover_clears_a_journal_whose_created_file_sits_below_a_file(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal that created `_index/graph.json`, when `_index` is now a file
    vault = copy_vault(tmp_path / "vault")
    (vault / "_index").write_bytes(b"in the way\n")
    operation = _operation("_index/graph.json", kind="create", sha256=_sha(b"{}"))
    _plant_journal(vault, _record([operation], directories=("_index",)))

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then the file below a file counts as absent, the journal is cleared and `_index` is kept
    assert result.returncode == 0
    assert (vault / "_index").read_bytes() == b"in the way\n"
    assert not list(vault.glob(".sabot-txn-*"))


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="the platform has no SIGTERM")
def test_a_signal_that_escapes_the_rollback_does_not_claim_the_vault_was_left_as_it_was(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a write that fails after its first applied operation, and a termination signal that
    # arrives just before the rollback can defer signals
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)

    real_signal = signal.signal
    armed = False

    def fail(phase: str, index: int) -> None:
        nonlocal armed
        if (phase, index) == ("applied", 0):
            armed = True
            raise OSError(errno.EIO, "injected")

    def late(number: int, handler: object) -> object:
        nonlocal armed
        if armed:
            armed = False
            signal.raise_signal(signal.SIGTERM)
        return real_signal(number, handler)  # type: ignore[arg-type]

    monkeypatch.setattr(signal, "signal", late)

    # When ingest runs
    with checkpoint_hook(fail):
        result = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")
    monkeypatch.undo()

    # Then it says the run could not be undone and names recover, since the vault did change
    assert error_code(result) == "interrupted"
    message = str(result.json()["error"]["message"])  # type: ignore[index]
    assert "left as it was" not in message
    assert "sabot recover" in message
    # And recover restores the vault
    assert run_cli("recover", "--vault", str(vault), "--json").returncode == 0
    assert vault_state(vault) == before


@pytest.mark.parametrize(
    ("path", "windows", "accepted"),
    [
        ("clauses/cl.setup.md", False, True),
        ("_index/graph.json", True, True),
        (".git/HEAD", False, False),
        ("clauses/.hidden.md", False, False),
        (".GIT/hooks/x", False, False),
        ("a/./b", False, False),
        ("clauses/name.", True, False),
        ("clauses/name.", False, True),
        ("clauses/name ", True, False),
        ("PROGRA~1/x.md", True, False),
        ("PROGRA~1/x.md", False, True),
    ],
)
def test_the_paths_a_journal_may_name(path: str, windows: bool, accepted: bool) -> None:
    # Given a path and whether Windows' extra rules apply
    # When it is judged
    # Then hidden names are refused everywhere, and Windows aliases of a name on Windows
    assert is_vault_path(path, windows=windows) is accepted


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
