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

- **`recover`** rolls back a write that was killed and clears what it left. It refuses a journal that names a path outside the vault, a write that is still running on this host and a lock it cannot judge, unless `--force` is given.
- **A write lock that records its writer.** While a write is running every other command, `lint` included, refuses with `vault-locked` ("a write is in progress"); a lock whose process has gone is cleared by `recover`.
- **Writes into a vault are all-or-nothing** (`init`, `ingest`, `segment`, `migrate-edition`, `compile` below `_index/`): staged, journaled with backups, applied by rename with `_ruleset.yaml` last and rolled back on failure, interrupt or termination signal. A signal that arrives during the rollback waits for it to finish, and the message says whether the vault was left as it was, the write had completed or `recover` must run.
- **`segment` removes the `spans/<key>.boundaries.jsonl` an earlier `boundaries` run left** when the key is segmented in another mode, and `diff-editions` reads ranges only for a source whose note says `segmenter: boundaries`.
- **`segment` check `rule-text-unspanned`**: a line that begins with a rule number must have its text in some span.
- **`id-reserved-name`, `transaction-incomplete`, `source-text-empty`, `source-key-invalid` for reserved keys, `emit-inside-vault`, `out-inside-vault`, `journal-invalid` and `recovery-failed`** refusals or lint classes, each with a test that plants it.

### Fixed

- A numbered line that read like a decimal broken across lines was dropped or merged into the line before it even when it was a real rule. It is now a continuation only when it directly follows a line that stops short of a sentence end, is not the number the list reaches next and begins in lower case.
- A heading repeated under one parent gets `-2`, `-3` instead of failing `duplicate-locator`.
- Sources that supersede each other in a loop are refused as `ruleset-invalid`; `coverage` used to report that nothing needed covering.
- `overlap` refuses a source text with no words.
- `recover` no longer writes or deletes files outside the vault when a journal names them, and no longer reports success when a backup it needs is missing.
- A journal directory that holds no `journal.json` (the remains of a finished write) no longer refuses every command; the next write or `recover` removes it.
- `init` below a file refuses as `write-failed` instead of `vault-locked` on platforms whose `mkdir` reports that as an existing name.
- `ci/check_content_guard.py` reports text that holds NUL bytes (and still scans it with them removed) and refuses `--hash` of a term with no letter or digit.
- `migrate-edition` reports a note that changed while it ran as `migration-rewrite-invalid`.

### Removed

- The refusal codes `note-unreadable` and `note-front-matter-invalid` of `migrate-edition`: the notes they judged are read by the vault reader moments earlier, so no input reaches them.

The suite holds 759 tests.
