"""The write lock tells a live writer from a dead one, and a reused process id from the writer."""

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import FIXTURES, CliRunner, copy_vault
from sabot_cli.testing import checkpoint_hook, process_start
from sabot_cli.vault import transaction as transaction_module
from sabot_cli.vault.transaction import LOCK_FILE, lock_state
from source_vaults import error_code, ingest_arguments
from support.locks import dead_process_id, write_lock
from support.vault_state import vault_state

RULES_E2 = FIXTURES / "rules-e2.txt"
requires_start_times = pytest.mark.skipif(
    not sys.platform.startswith(("linux", "darwin")) and sys.platform != "win32",
    reason="process start times are read on Linux, macOS and Windows only",
)


@contextmanager
def _running_child() -> Iterator[subprocess.Popen[bytes]]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield child
    finally:
        child.kill()
        child.wait()


@requires_start_times
def test_a_process_start_time_is_read_and_is_the_same_each_time() -> None:
    # Given this running process
    # When its start time is read twice
    first, second = process_start(os.getpid()), process_start(os.getpid())

    # Then both readings are the same non-empty string
    assert first
    assert first == second


@requires_start_times
def test_two_processes_have_different_start_times() -> None:
    # Given this process and a child that started after it
    with _running_child() as child:
        # When both start times are read
        # Then they differ
        assert process_start(child.pid) != process_start(os.getpid())


def test_the_start_time_of_a_process_that_has_gone_is_unknown() -> None:
    # Given a process that has exited
    # When its start time is read
    # Then there is none, and no error
    assert process_start(dead_process_id()) is None


@pytest.mark.parametrize("pid", [0, -1, 2**40, 10**30])
def test_the_start_time_of_an_impossible_process_id_is_unknown(pid: int) -> None:
    assert process_start(pid) is None


def test_a_reused_process_id_with_another_start_time_is_a_stale_lock(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a lock naming a process id that is running now (this process) but recording the
    # start time of an earlier process that had the same id
    if process_start(os.getpid()) is None:
        pytest.skip("this platform does not give a process start time")
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=os.getpid(), start="an-earlier-process")
    before = vault_state(vault)

    # When a reader runs, and a writer
    reader = run_cli("coverage", "--vault", str(vault), "--json")
    writer = run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault), "--json")

    # Then the reader is not refused, the writer says the lock was left behind, and nothing changed
    assert reader.returncode == 0
    assert error_code(writer) == "vault-locked"
    assert "left its lock behind" in writer.stdout
    assert vault_state(vault) == before
    # And recover clears it without --force
    assert run_cli("recover", "--vault", str(vault), "--json").returncode == 0


def test_a_lock_with_the_running_processes_own_start_time_is_live(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a lock recording this process's id and its real start time
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=os.getpid(), start=process_start(os.getpid()))

    # When the lock is judged and recover runs
    # Then the writer is live and recover refuses
    assert lock_state(vault) == "live"
    assert error_code(run_cli("recover", "--vault", str(vault), "--json")) == "vault-locked"


def test_a_start_time_that_cannot_be_read_never_makes_a_lock_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a lock recording a start time, for a live process whose start time cannot be read
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=os.getpid(), start="whatever")
    monkeypatch.setattr(transaction_module, "process_start", lambda _pid: None)

    # When the lock is judged
    # Then it is live: a lock is not cleared on a guess
    assert lock_state(vault) == "live"


def test_a_lock_without_a_start_time_is_judged_by_its_process_alone(tmp_path: Path) -> None:
    # Given locks of the earlier form, with no start time
    vault = copy_vault(tmp_path / "vault")

    # When they name a live process and a dead one
    write_lock(vault, pid=os.getpid())
    live = lock_state(vault)
    write_lock(vault, pid=dead_process_id())

    # Then the first is live and the second stale
    assert (live, lock_state(vault)) == ("live", "stale")


@pytest.mark.parametrize("pid", [10**30, 2**40, 0, -3, True, "12", 1.5, None, [1]])
def test_a_lock_naming_an_impossible_process_id_cannot_be_judged_and_never_crashes(
    pid: object, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a lock whose process id is not a usable process id
    vault = copy_vault(tmp_path / "vault")
    (vault / LOCK_FILE).write_text(
        json.dumps({"host": socket.gethostname(), "pid": pid}), encoding="utf-8"
    )

    # When readers, lint and recover run
    # Then none raises: readers go on, recover asks for --force, and --force clears the lock
    assert run_cli("lint", "--vault", str(vault), "--json").returncode == 0
    assert run_cli("coverage", "--vault", str(vault), "--json").returncode == 0
    refused = run_cli("recover", "--vault", str(vault), "--json")
    assert error_code(refused) == "vault-locked"
    assert "--force" in refused.stdout
    assert run_cli("recover", "--force", "--vault", str(vault), "--json").returncode == 0


def test_the_message_for_a_write_in_progress_names_the_way_out(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a lock held by a running process
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=os.getpid(), start=process_start(os.getpid()))

    # When a command runs
    result = run_cli("coverage", "--vault", str(vault), "--json")

    # Then the message says a write is in progress and names `recover --force`
    assert "in progress" in result.stdout
    assert "sabot recover --force" in result.stdout


def test_a_running_write_records_its_process_id_host_and_start_time(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a write that reads its own lock while it runs
    vault = copy_vault(tmp_path / "vault")
    seen: list[dict[str, object]] = []

    def read_lock(phase: str, _index: int) -> None:
        if phase == "journaled":
            seen.append(json.loads((vault / LOCK_FILE).read_text(encoding="utf-8")))

    # When ingest runs
    with checkpoint_hook(read_lock):
        run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault))

    # Then the lock names this process and, where the platform gives it, its start time
    assert seen[0]["pid"] == os.getpid()
    assert seen[0]["host"] == socket.gethostname()
    assert seen[0]["start"] == process_start(os.getpid())
