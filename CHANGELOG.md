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

- **`recover`** rolls back a write that was killed and clears what it left. It checks every journal against the vault before changing anything: it refuses a journal that names a hidden, outside or linked path, one written on another host, a write that is still running on this host and a lock it cannot judge (`--force` overrides the last three), and it changes a file only when the file holds what the journal says (a created file the journaled digest, a replaced file the written bytes or already the backup, a deleted file nothing). One operation that fails a check stops the whole recovery with nothing changed. Restored files never gain set-id bits, and gain execute bits only when they already had them.
- **A write lock that records its writer.** While a write is running every other command, `lint` included, refuses with `vault-locked` ("a write is in progress"); a lock whose process has gone is cleared by `recover`.
- **Writes into a vault are all-or-nothing** (`init`, `ingest`, `segment`, `migrate-edition`, `compile` below `_index/`): staged, journaled with backups, applied by rename with `_ruleset.yaml` last and rolled back on failure, interrupt or termination signal. A signal that arrives during the rollback waits for it to finish, and the message says whether the vault was left as it was, the write had completed or `recover` must run.
- **`segment` removes the `spans/<key>.boundaries.jsonl` an earlier `boundaries` run left** when the key is segmented in another mode, and `diff-editions` reads ranges only for a source whose note says `segmenter: boundaries`.
- **`segment` reports each numbered line it read as a continuation** instead of a rule: `continuations` in JSON (line, number, the span it joined) and the check `numbered-continuations`, a notice that does not stop the run. `--strict-numbering` makes it a failure. A check now has the status `passed`, `failed` or `notice`.
- **The write lock records its writer's start time.** A lock whose process id now belongs to a process that started at another time is stale. The start time is read without a new dependency on Linux, macOS and Windows; where it cannot be read, the lock is judged by the process id alone.
- **`id-reserved-name`, `transaction-incomplete`, `source-text-empty`, `source-key-invalid` for reserved keys, `emit-inside-vault`, `out-inside-vault`, `journal-invalid`, `journal-other-host`, `boundaries-not-written` and `recovery-failed`** refusals or lint classes, each with a test that plants it.

### Fixed

- A numbered line that read like a decimal broken across lines was dropped or merged into the line before it even when it was a real rule. It is now a continuation only when it directly follows a line that stops short of a sentence end, is not the number the list reaches next and begins in lower case.
- A heading repeated under one parent gets `-2`, `-3` instead of failing `duplicate-locator`.
- Sources that supersede each other in a loop are refused as `ruleset-invalid`; `coverage` used to report that nothing needed covering.
- `overlap` refuses a source text with no words.
- `recover` no longer writes or deletes files a journal names outside the vault or in a hidden directory such as `.git`, and no longer reports success when a backup it needs is missing.
- A write that would create a file below a file (a vault whose `_index` is a file, for one) is refused before any journal is written; it used to leave a journal that nothing could roll back, and the message said the write could not be fully undone.
- A termination signal that arrives just before the rollback can defer signals is reported as not fully undone, not as leaving the vault as it was.
- `segment` says, with its own code (`boundaries-not-written`), that the vault was updated when only the `--emit-boundaries` file could not be written.
- A lock that names a process id too large to exist, or any value that is not a process id, no longer ends a command in a traceback.
- `lint` no longer enters hidden directories (`.git`, `.obsidian`) when it checks names Windows cannot hold, and `quotation-long` no longer reads an Obsidian callout as a quotation.
- A journal directory that holds no `journal.json` (the remains of a finished write) no longer refuses every command; the next write or `recover` removes it.
- `init` below a file refuses as `write-failed` instead of `vault-locked` on platforms whose `mkdir` reports that as an existing name.
- `ci/check_content_guard.py` reports text that holds NUL bytes (and still scans it with them removed; UTF-16 saved without a byte order mark is read that way only when its text is ASCII, and otherwise it is not valid UTF-8 and is skipped as binary) and refuses `--hash` of a term with no letter or digit.
- `migrate-edition` reports a note that changed while it ran as `migration-rewrite-invalid`.

### Removed

- The refusal codes `note-unreadable` and `note-front-matter-invalid` of `migrate-edition`: the notes they judged are read by the vault reader moments earlier, so no input reaches them.

The suite holds 845 tests.
