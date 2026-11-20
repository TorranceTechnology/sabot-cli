# Changelog

Entries are dated and say what a user of the tools will see change. Counts are of the test suite on the day.

## 2026-11-20

### Changed

- **The construct note prefix is `cst.`** (it was `con.`). `CON` is a name Windows reserves for a device, so a file called `con.<name>.md` cannot be checked out there. Rename existing construct notes and the `[[con.…]]` links to them; `lint` reports the old ids as `id-prefix-mismatch`.
- **Text files are read without a leading byte order mark.** Notes, the ruleset and extracted source text all drop it, and a note or ruleset saved with one keeps it when a tool rewrites the file. Span files made from a text that began with a mark before this change record offsets one character too high for every span. Run `segment` again on such a source to correct them; hashes and locators are unaffected.
- **`--skip-pages` defaults to 0.** Text without form feeds is one page and nothing is skipped from it. It used to drop the whole text.
- **`coverage` measures only the newest edition** of a chain by default and now lists what it left out: `skippedSuperseded` in JSON and a `skipped KEY (superseded)` line in text. `--source KEY` still measures a superseded source.
- **`compile --out` inside the vault may name only a file below `_index/`** (`out-inside-vault`). Any other path there is a note, the ruleset, a span file or a file the tools read. Outside the vault nothing changes.
- **`segment --emit-boundaries` may not name a path inside the vault** (`emit-inside-vault`), and the file is written after the vault write has committed.
- **A key ending in `.boundaries`, or one whose span file would be named for a Windows device (`aux`, `con`, `nul`, `com1`, `lpt1`, …), is refused** as `source-key-invalid`.
- **`lint`'s `id-reserved-name` covers every file and directory below the vault**, not only note ids and file names. Superscript-digit device names (`com¹`), `conin$`, `conout$` and names ending in a dot or a space are included.
- **`body-link` ignores wikilinks inside code spans and fenced code blocks.** `quotation-long` now also reads curly single quotes and block quotes.
- **`--help` states the default of `--kind`, `--format`, `--numbering`, `--direction`, `--depth`, `--fail-at` and `--out`.**
- **Ruleset keys that other YAML readers would type** (`yes`, `on`, `0x1f`, `2024-01-01`, `1e3`) are written quoted when `ingest` registers a source.

### Added

- **`recover`** rolls back a write that was killed and clears what it left, and applies only a journal this user's sabot wrote for this vault. Every journal carries a MAC (HMAC-SHA256) made with a 32-byte random key that the first write creates, with exclusive create and mode 0600, in a per-user state directory outside the vault (`$XDG_STATE_HOME/sabot` or `~/.local/state/sabot`, `~/Library/Application Support/sabot`, `%LOCALAPPDATA%\sabot`; `SABOT_STATE_DIR` overrides it). The MAC covers the vault's resolved path, every path and digest the journal names and the SHA-256 of each backup. `recover` verifies it before anything else and refuses with `journal-unverified`, changing nothing, when the key, the MAC, the vault path or a backup does not match, whatever `--force` says. A verified journal still goes through the checks on paths and content: it may name only plain, non-hidden paths inside the vault, and a file is changed only when it holds what the journal says (a created file the journaled digest, a replaced file the written bytes or already the backup, a deleted file nothing). One operation that fails a check stops the whole recovery with nothing changed. A write or `recover` refuses with `state-directory-in-vault` when the state directory lies inside the vault and with `key-invalid` when the key file is not 32 bytes, is a link or is not a regular file.
- **`recover --discard`** removes the journal directories (`.sabot-txn-*`) and staged files (`.sabot-new-*`) without applying any journal, and changes no other file. It reads no journal and needs no key, never follows a link and never enters a hidden directory such as `.git`. It is the way out for a journal `recover` refuses, such as one left by a crash on another machine; it does not undo what that write had already applied.
- **`--force` applies to the lock only.** It no longer overrides anything about a journal, and the check of a journal's host is gone: a journal written elsewhere, or in a vault that moved to another path, is `journal-unverified`.
- **A write lock that records its writer.** While a write is running every other command, `lint` included, refuses with `vault-locked` ("a write is in progress"); a lock whose process has gone is cleared by `recover`.
- **Writes into a vault are all-or-nothing** (`init`, `ingest`, `segment`, `migrate-edition`, `compile` below `_index/`): staged, journaled with backups, applied by rename with `_ruleset.yaml` last and rolled back on failure, interrupt or termination signal. A signal that arrives during the rollback waits for it to finish, and the message says whether the vault was left as it was, the write had completed or `recover` must run.
- **`segment` removes the `spans/<key>.boundaries.jsonl` an earlier `boundaries` run left** when the key is segmented in another mode, and `diff-editions` reads ranges only for a source whose note says `segmenter: boundaries`.
- **`segment` lists every line that begins with a number and did not open a rule**, instead of only the wrapped decimals it joined to the rule before: `continuations` in JSON (line, number, span or null, and a `kind` of `wrapped`, `unopened` or `furniture`) and the check `numbered-continuations`, a notice that does not stop the run. `unopened` covers a bare number on a line of its own, `4.2)`, `5 Scoring` and a lettered number in dotted mode; `furniture` covers a page number or contents line that begins with a number and was dropped. `--strict-numbering` makes a non-empty list a failure. A check now has the status `passed`, `failed` or `notice`, and its entries are in natural order (`line 19` before `line 125`).
- **The write lock records its writer's start time and the boot of the machine.** A lock whose process id now belongs to a process that started at another time, or that was written in another boot, is stale; the second is what clears the lock of a writer that died before a reboot, when its id now belongs to a system process whose start time cannot be read. The boot is read from `boot_id` on Linux, `kern.boottime` on macOS and `GetTickCount64` on Windows, and two boot times within 10 seconds count as one. Where a start time or boot cannot be read, the lock is judged by what can.
- **`id-reserved-name`, `transaction-incomplete`, `source-text-empty`, `source-key-invalid` for reserved keys, `emit-inside-vault`, `out-inside-vault`, `journal-invalid`, `journal-unverified`, `key-invalid`, `state-directory-in-vault`, `note-name-unwritable`, `boundaries-not-written` and `recovery-failed`** refusals or lint classes, each with a test that plants it.

### Fixed

- A numbered line that read like a decimal broken across lines was dropped or merged into the line before it even when it was a real rule. It is now a continuation only when it directly follows a line that stops short of a sentence end, is not the number the list reaches next and begins in lower case.
- A heading repeated under one parent gets `-2`, `-3` instead of failing `duplicate-locator`.
- Sources that supersede each other in a loop are refused as `ruleset-invalid`; `coverage` used to report that nothing needed covering.
- `overlap` refuses a source text with no words.
- `recover` no longer writes or deletes files a journal names outside the vault or in a hidden directory such as `.git`, and no longer reports success when a backup it needs is missing. A journal that passes every check of path and content (a `replace` of a note whose digest it records, a `create` that deletes a note, a `delete` that creates a file, a created-directory list that removes an empty directory) is no longer applied unless it is signed for this vault with this user's key, and `--force` no longer lets one through.
- A write that would create a file below a file (a vault whose `_index` is a file, for one) is refused before any journal is written; it used to leave a journal that nothing could roll back, and the message said the write could not be fully undone.
- A termination signal that arrives just before the rollback can defer signals is reported as not fully undone, not as leaving the vault as it was.
- `segment` says, with its own code (`boundaries-not-written`), that the vault was updated when only the `--emit-boundaries` file could not be written.
- A lock that names a process id too large to exist, or any value that is not a process id, no longer ends a command in a traceback, and an id from 2147483648 up is not taken as a running writer on Linux and macOS.
- A journal nested too deeply to parse, or one naming a path with a NUL, a control character or a lone surrogate, is refused as `journal-invalid` instead of ending `recover` in a traceback. A path whose name hides a leading dot behind a zero-width character is read as hidden.
- `recover` refuses before changing anything when a staged name is held by a directory; it used to apply the journals before it and then fail part-way.
- The writer's own rollback restores the permission bits of a file its write deleted, execute bits included; it used to bring back an executable file as not executable.
- `migrate-edition` refuses with `note-name-unwritable`, naming the file, when a note it must rewrite has a name a write cannot hold (a colon or a backslash, for one), instead of ending in a traceback.
- `lint` no longer enters hidden directories (`.git`, `.obsidian`) when it checks names Windows cannot hold, and `quotation-long` no longer reads an Obsidian callout as a quotation.
- A journal directory that holds no `journal.json` (the remains of a finished write) no longer refuses every command; the next write or `recover` removes it.
- `init` below a file refuses as `write-failed` instead of `vault-locked` on platforms whose `mkdir` reports that as an existing name.
- `ci/check_content_guard.py` reports text that holds NUL bytes (and still scans it with them removed; UTF-16 saved without a byte order mark is read that way only when its text is ASCII, and otherwise it is not valid UTF-8 and is skipped as binary) and refuses `--hash` of a term with no letter or digit.
- `migrate-edition` reports a note that changed while it ran as `migration-rewrite-invalid`.

### Removed

- The refusal codes `note-unreadable` and `note-front-matter-invalid` of `migrate-edition`: the notes they judged are read by the vault reader moments earlier, so no input reaches them.

The suite holds 958 tests.
