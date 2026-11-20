"""`recover --discard` removes journals and staged files and changes no other file.

It must be safe on a vault nobody trusts, so these tests plant journals and staged files whose
names, links and contents try to make it delete or follow something else, and hold that every
other file keeps its bytes.
"""

import json
import os
from pathlib import Path

import pytest

from conftest import CliRunner, copy_vault
from sabot_cli.testing import state_directory
from sabot_cli.vault.transaction import LOCK_FILE
from source_vaults import error_code
from support.journals import TOKEN, operation, plant_journal, record, sha
from support.locks import dead_process_id, write_lock
from support.vault_state import vault_state

_JOURNAL = f".sabot-txn-{TOKEN}"


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("this account cannot create symbolic links")


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_bytes(b"original")
    (outside / "journal.json").write_bytes(b"{}")
    (outside / ".sabot-new-aaaaaaaaaaaa-victim.txt").write_bytes(b"a staged-looking victim")
    (outside / "backup").mkdir()
    (outside / "backup" / "0").write_bytes(b"outside backup")
    return outside


def test_discard_removes_a_journal_without_applying_it_and_changes_nothing_else(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with an unsigned journal that would replace a note, and a staged file
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    plant_journal(
        vault, record([operation(sha256=sha(note.read_bytes()))]), backup=b"ATTACKER", signed=False
    )
    (vault / "sources" / ".sabot-new-aaaaaaaaaaaa-src.x.md").write_bytes(b"staged")
    expected = {
        key: value
        for key, value in vault_state(vault).items()
        if ".sabot-txn" not in key and ".sabot-new" not in key
    }

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then the journal and the staged file are gone, the note is untouched, and the report names
    # what was removed in sorted order
    assert result.returncode == 0
    assert result.json() == {
        "clearedLock": False,
        "command": "recover",
        "discard": True,
        "format": 1,
        "removedJournals": [_JOURNAL],
        "removedStaged": ["sources/.sabot-new-aaaaaaaaaaaa-src.x.md"],
    }
    assert vault_state(vault) == expected


def test_discard_text_output_is_deterministic_and_a_second_run_finds_nothing(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a vault with two journals and two staged files
    vault = copy_vault(tmp_path / "vault")
    for token in ("cccccccccccc", "bbbbbbbbbbbb"):
        (vault / f".sabot-txn-{token}").mkdir()
    for name in ("notes/.sabot-new-zzz-b.md", "clauses/.sabot-new-aaa-a.md"):
        (vault / name).parent.mkdir(exist_ok=True)
        (vault / name).write_bytes(b"staged")

    # When discard runs twice
    first = run_cli("recover", "--discard", "--vault", str(vault))
    second = run_cli("recover", "--discard", "--vault", str(vault))

    # Then the first lists everything sorted and the second has nothing to do
    assert first.stdout == (
        "removed journal: .sabot-txn-bbbbbbbbbbbb\n"
        "removed journal: .sabot-txn-cccccccccccc\n"
        "removed staged file: clauses/.sabot-new-aaa-a.md\n"
        "removed staged file: notes/.sabot-new-zzz-b.md\n"
    )
    assert second.stdout == "nothing to discard\n"


def test_discard_unlinks_a_journal_that_is_a_link_and_never_follows_it(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal name in the vault that is a link to a directory outside, which holds files
    # a journal directory would hold
    outside = _outside(tmp_path)
    vault = copy_vault(tmp_path / "vault")
    _symlink(vault / _JOURNAL, outside, directory=True)
    outside_before = vault_state(outside)
    expected = {k: v for k, v in vault_state(vault).items() if k != f"{_JOURNAL}/"}

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then only the link is gone; everything it pointed at is as it was
    assert result.returncode == 0
    assert result.json()["removedJournals"] == [_JOURNAL]
    assert not (vault / _JOURNAL).exists()
    assert not (vault / _JOURNAL).is_symlink()
    assert vault_state(outside) == outside_before
    assert vault_state(vault) == expected


def test_discard_removes_links_inside_a_journal_directory_without_following_them(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal directory holding links to outside directories and files, one named for
    # its backup directory and one for its journal file
    outside = _outside(tmp_path)
    vault = copy_vault(tmp_path / "vault")
    journal = vault / _JOURNAL
    journal.mkdir()
    _symlink(journal / "backup", outside / "backup", directory=True)
    _symlink(journal / "journal.json", outside / "victim.txt")
    _symlink(journal / "elsewhere", outside, directory=True)
    outside_before = vault_state(outside)

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then the journal directory is gone and the outside files are intact
    assert result.returncode == 0
    assert not journal.exists()
    assert vault_state(outside) == outside_before


def test_discard_unlinks_a_staged_link_and_leaves_what_it_pointed_at(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given staged names that are links to an outside file, to a vault note and to a directory
    outside = _outside(tmp_path)
    vault = copy_vault(tmp_path / "vault")
    note = vault / "clauses" / "cl.setup.md"
    _symlink(vault / "clauses" / ".sabot-new-aaaaaaaaaaaa-out.md", outside / "victim.txt")
    _symlink(vault / "clauses" / ".sabot-new-aaaaaaaaaaaa-note.md", note)
    _symlink(vault / "clauses" / ".sabot-new-aaaaaaaaaaaa-dir", outside, directory=True)
    outside_before = vault_state(outside)
    note_before = note.read_bytes()

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then the file links are removed, the directory link is not a staged file and stays, and no
    # target changed
    assert result.returncode == 0
    assert result.json()["removedStaged"] == [
        "clauses/.sabot-new-aaaaaaaaaaaa-note.md",
        "clauses/.sabot-new-aaaaaaaaaaaa-out.md",
    ]
    assert vault_state(outside) == outside_before
    assert note.read_bytes() == note_before
    assert (vault / "clauses" / ".sabot-new-aaaaaaaaaaaa-dir").is_symlink()


def test_discard_does_not_enter_a_linked_directory_or_a_hidden_one(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given staged-looking files below a link to an outside directory, below `.git` and below
    # another hidden directory, and a staged-named directory holding a file
    outside = _outside(tmp_path)
    vault = copy_vault(tmp_path / "vault")
    _symlink(vault / "linked", outside, directory=True)
    for hidden in (".git", ".obsidian"):
        (vault / hidden).mkdir()
        (vault / hidden / ".sabot-new-aaaaaaaaaaaa-x").write_bytes(b"not ours")
    staged_directory = vault / "clauses" / ".sabot-new-aaaaaaaaaaaa-dir"
    staged_directory.mkdir()
    (staged_directory / ".sabot-new-bbbbbbbbbbbb-inner").write_bytes(b"inside a directory")
    before_outside = vault_state(outside)
    before_vault = vault_state(vault)

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then nothing is removed
    assert result.returncode == 0
    assert result.json()["removedStaged"] == []
    assert vault_state(outside) == before_outside
    assert vault_state(vault) == before_vault


def test_discard_leaves_a_regular_file_named_like_a_journal(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a file, not a directory, with a journal's name
    vault = copy_vault(tmp_path / "vault")
    (vault / ".sabot-txn-notes").write_bytes(b"my notes")
    before = vault_state(vault)

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then it is left alone
    assert result.returncode == 0
    assert result.json()["removedJournals"] == []
    assert vault_state(vault) == before


@pytest.mark.parametrize(
    "name",
    [".sabot-txn-", ".sabot-txn- x y", ".sabot-txn-..", ".sabot-txn-é‍", ".sabot-txn-*"],
)
def test_discard_removes_only_the_journal_whose_odd_name_it_was_given(
    name: str, run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a journal directory with an odd name whose journal names a note and a path above
    # the vault
    if os.name == "nt" and ("*" in name or name.endswith(".")):
        pytest.skip("Windows cannot hold a file name with an asterisk or a trailing dot")
    vault = copy_vault(tmp_path / "vault")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"original")
    directory = vault / name
    directory.mkdir()
    (directory / "journal.json").write_text(
        json.dumps(
            {
                "operations": [
                    {"path": "../victim.txt", "kind": "create"},
                    {"path": "clauses/cl.setup.md", "kind": "create"},
                ]
            }
        ),
        encoding="utf-8",
    )
    expected = {k: v for k, v in vault_state(vault).items() if not k.startswith(f"{name}")}

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then that directory is gone and every other file keeps its bytes
    assert result.returncode == 0
    assert result.json()["removedJournals"] == [name]
    assert victim.read_bytes() == b"original"
    assert vault_state(vault) == expected


def test_discard_clears_a_stale_lock_and_refuses_a_live_one_unless_forced(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a stale lock, then a live one
    vault = copy_vault(tmp_path / "vault")
    write_lock(vault, pid=dead_process_id())

    # When discard runs on the stale lock
    cleared = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then the lock is cleared without --force
    assert cleared.returncode == 0
    assert cleared.json()["clearedLock"] is True
    assert not (vault / LOCK_FILE).exists()

    # And a live lock refuses, changing nothing, until --force
    write_lock(vault, pid=os.getpid())
    (vault / _JOURNAL).mkdir()
    before = vault_state(vault)
    refused = run_cli("recover", "--discard", "--vault", str(vault), "--json")
    assert (refused.returncode, error_code(refused)) == (3, "vault-locked")
    assert vault_state(vault) == before
    forced = run_cli("recover", "--discard", "--force", "--vault", str(vault), "--json")
    assert forced.returncode == 0
    assert not (vault / _JOURNAL).exists()
    assert not (vault / LOCK_FILE).exists()


def test_discard_refuses_a_directory_that_does_not_exist(
    run_cli: CliRunner, tmp_path: Path
) -> None:
    # Given a path that is no directory
    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(tmp_path / "missing"), "--json")

    # Then it refuses
    assert (result.returncode, error_code(result)) == (3, "not-a-vault")


def test_discard_needs_no_key(run_cli: CliRunner, tmp_path: Path) -> None:
    # Given a vault with a journal and a user with no key
    vault = copy_vault(tmp_path / "vault")
    (vault / _JOURNAL).mkdir()

    # When discard runs
    result = run_cli("recover", "--discard", "--vault", str(vault), "--json")

    # Then it succeeds and creates no key
    assert result.returncode == 0
    assert not state_directory().exists()
