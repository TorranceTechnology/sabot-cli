# sabot-cli

Command-line tools for building games on the [Sabot](https://github.com/TorranceTechnology/sabot) rules engine.

A game built on the engine has to follow a published ruleset, and a ruleset is long, ambiguous in places and revised from time to time. These tools turn a ruleset into a typed rules graph and keep that graph honest: a folder of Markdown notes with YAML properties, each note tied to the exact spans of the source it rests on by location and hash, never by copied text. The graph opens as an Obsidian vault and needs no Obsidian to use. The tools check the notes against a schema, report which parts of the source are not yet cited, compare editions, search the graph, and write a machine-readable graph for a generator to read. Agent skills that drive the tools are planned and are not in this repository yet.

The notes follow schema v1. [docs/schema-v1.md](docs/schema-v1.md) is the reference to read before writing notes.

## Requirements and setup

- [uv](https://docs.astral.sh/uv/) and Python 3.12 or later. Development uses 3.14, set in `.python-version`.
- Poppler is optional. Its `pdftotext` program is needed only for `ingest --extractor pdftotext` and for the one test that exercises it, which is skipped when `pdftotext` is not on the `PATH`. PDF ingest uses the `pypdf` extractor by default and needs nothing else. Poppler is an external program, not a Python dependency. Setting `SABOT_CLI_REQUIRE_PDFTOTEXT=1` makes that test fail instead of skip when Poppler is missing; CI sets it where Poppler is installed.

```sh
uv sync
```

`uv sync` creates the environment from `uv.lock`. Nothing else needs configuring, and no tool uses the network or a model.

## Running

```sh
uv run sabot --help                 # list the commands
uv run sabot <command> --help       # one command's options
```

The `sabot` console script is also installed in the environment, and `uv run python -m sabot_cli` is the same program.

## Commands

| Command | Purpose | Writes |
|---|---|---|
| `init` | Create an empty vault. | The new vault. |
| `ingest` | Register a source edition as a source note. | A source note, `_ruleset.yaml`, and optionally a copy of the file and its text. |
| `segment` | Turn extracted text into a span file of locations and hashes. | `spans/<key>.jsonl`, the source note's `segmenter`, optionally a boundaries file. |
| `lint` | Check a vault against the schema. | Nothing. |
| `compile` | Write the machine graph. | `_index/graph.json`, or `--out`. |
| `coverage` | Check that every in-scope span is cited. | Nothing. |
| `diff-editions` | Classify span changes between two editions. | Nothing. |
| `migrate-edition` | Move citations to the successor edition. | The notes it changes, unless `--dry-run`. |
| `overlap` | Detect copied source wording in note bodies. | Nothing. |
| `recover` | Roll back a write that was killed. | Restores the vault; removes what the write left. |
| `schema` | Show the schema a vault is written to. | Nothing. |
| `search` | Find notes by property filters and full-text relevance. | Nothing. |
| `traverse` | List notes reachable from one note over chosen edges. | Nothing. |

The examples below run from the repository root against the Driftwood fixture, an invented card game in `tests/fixtures/driftwood`. Those that write use a scratch copy, so nothing in the repository changes:

```sh
DEMO=$(mktemp -d)
cp -R tests/fixtures/driftwood/vault "$DEMO/vault"
```

Each example shows the command, its output (standard output, then standard error) and its exit code. An output cut short ends with `...`. Every option and argument is in `--help`; the notes under each command give defaults that `--help` does not state. The `init`, `ingest` and `segment` examples build on each other.

### init

Creates a vault skeleton: `_ruleset.yaml` and the folders of the layout in the schema reference. The directory must not exist or must be empty.

```text
usage: sabot init [-h] --ruleset RULESET [--json] directory
```

```sh
uv run sabot init "$DEMO/new" --ruleset driftwood
```

```text
_ruleset.yaml
build/
build/constructs/
build/obligations/
clauses/
examples/
model/
...
```

The example exits 0. Exit codes: 0 created; 3 refused (ruleset id not lowercase letters, digits and hyphens starting with a letter, the path is not a directory, the directory is not empty). JSON document keys: `command`, `created`, `format`, `ruleset`.

### ingest

Registers one source edition: hashes the file, extracts its text, writes `sources/src.<key>.md` and adds the source to `_ruleset.yaml` (as one new entry after the last source, so comments and layout elsewhere in the file are kept). The text is extracted to `--text-out` when given; the original goes to `--store` when given. Both must lie outside the vault, because the vault holds no source text. `--label`, `--title`, `--edition` and `--retrieved` are required unless `--verify` is given. `--kind` defaults to `edition`. The format is detected from the file unless `--format` is given. `--verify` compares the file with the hash the vault registered and writes nothing.

```text
usage: sabot ingest [-h] --key KEY [--label LABEL] [--title TITLE]
                    [--edition EDITION] [--retrieved YYYY-MM-DD] [--url URL]
                    [--effective YYYY-MM-DD] [--format {pdf,html,text}]
                    [--kind {edition,errata,ruling,faq}] [--supersedes KEY]
                    [--amends KEY] [--archive-url ARCHIVE_URL]
                    [--extractor NAME] [--store DIR] [--text-out PATH]
                    [--verify] [--vault PATH] [--json]
                    file
```

```sh
uv run sabot ingest tests/fixtures/driftwood/rules-e1.txt --key dw1 \
  --label "Driftwood Rules, first edition" --title "Driftwood Rules, first edition" \
  --edition first --retrieved 2026-11-19 --vault "$DEMO/new"
```

```text
key: dw1
sha256: 7ba7ad9dd331e626340320923851ebebc3d998dffa0cafc0c6e43fb719be558f
bytes: 6429
extractor: text
note: sources/src.dw1.md
```

The example exits 0. Exit codes: 0 registered, or `--verify` and the hash matches; 1 only for `--verify` with a different hash; 2 a required option is missing; 3 refused (invalid or registered key, unreadable file, unknown format or extractor, a date that is not real, `--supersedes` or `--amends` naming an unregistered key, `--store` or `--text-out` inside the vault, extraction failed, a `sources` entry in `_ruleset.yaml` written in a form that cannot be extended in place). `textOut` and the `text-out` row name the file without its directory. JSON document keys: `bytes`, `command`, `extractor`, `format`, `key`, `note`, `sha256`, `sourceFormat`, `store`, `textOut`; with `--verify` they are `actualSha256`, `command`, `expectedSha256`, `format`, `key`, `match`.

### segment

Splits extracted text into spans and writes `spans/<key>.jsonl`, which holds each span's locator, parent, page, offset and hashes and none of its text. When one of its own checks fails it writes no span file. `--emit-boundaries FILE` is the exception: it writes the boundaries of a rule-based run even when a check failed, so the proposal can be repaired, and writes nothing when a boundaries file given with `--boundaries` was itself refused. Modes: `numbered` (rule numbers; `--numbering` defaults to `dotted`, and `dotted-letter` also reads `4.2a`), `headings`, `sentences` and `boundaries` (spans proposed in a boundaries file, which the tool checks and hashes). `--skip-pages` defaults to 0 and applies to `numbered` only; pages are counted by form feed, so text without form feeds is one page and nothing is skipped from it. A heading whose slug cannot be cited (such as `s2`, or one with no ASCII letters) fails the `locator-invalid` check.

```text
usage: sabot segment [-h] --source KEY
                     --mode {numbered,headings,sentences,boundaries}
                     [--boundaries FILE] [--emit-boundaries FILE]
                     [--numbering {dotted,dotted-letter}] [--skip-pages N]
                     [--vault PATH] [--json]
                     text_file
```

```sh
uv run sabot segment tests/fixtures/driftwood/rules-e1.txt --source dw1 \
  --mode numbered --numbering dotted-letter --vault "$DEMO/new"
```

```text
spans: 51
sentences: 39
check duplicate-locator: passed
check locator-invalid: passed
check no-spans: passed
check non-monotone: passed
check page-decreasing: passed
```

The example exits 0. Exit codes: 0 span file written; 1 a check failed and no span file was written; 3 refused (source not ingested, unreadable or non-UTF-8 text, a boundaries file given or missing for the mode). JSON document keys: `boundariesEmitted`, `checks`, `command`, `format`, `mode`, `path`, `sentences`, `source`, `spans`, `written`.

### lint

Checks every note, the span files they cite and `_ruleset.yaml` against the schema and prints one finding per line on standard output and a count on standard error. Warnings do not fail the command; `--strict` raises the build-readiness warnings (pending, stale, open questions, orphans, superseded sources) to errors. The lint classes are listed in the schema reference, and `--list-classes` prints them.

```text
usage: sabot lint [-h] [--strict] [--list-classes] [--vault PATH] [--json]
```

```sh
uv run sabot lint --strict --vault tests/fixtures/driftwood/vault
```

```text
sabot lint: 0 errors, 0 warnings
```

The example exits 0. Exit codes: 0 no errors; 1 at least one error; 3 the directory is not a vault. A `_ruleset.yaml` that exists but cannot be used is a finding, not a refusal. JSON document keys: `command`, `counts`, `findings`, `format`; with `--list-classes`, `classes`, `command`, `format`.

### compile

Writes the machine graph: every note with its attributes and paraphrase, every authored edge, every inverse edge (marked derived), the ruleset's extensions and a digest of the notes, `_ruleset.yaml` and the `*.jsonl` span files directly in `spans/` (a hidden or stray file there does not change it). It refuses to run on a vault that has lint errors. The default path is `_index/graph.json` in the vault. `--check` writes nothing and tells whether the file at that path is current. `--out` may not name a directory or a note of the vault (`out-is-a-directory`, `out-is-a-note`).

```text
usage: sabot compile [-h] [--out PATH] [--check] [--vault PATH] [--json]
```

```sh
uv run sabot compile --vault "$DEMO/vault"
```

```text
wrote _index/graph.json
summary         count
notes           54
authored edges  66
derived edges   66
extensions      2
vault digest    9ffbddbbb3e482ff44a64ff864786d1ddb013a4140b46be477697bba77a914d1
```

The example exits 0. Exit codes: 0 written, or `--check` and current; 1 the vault has lint errors (listed, nothing written), or `--check` and the file is missing or out of date; 3 the directory is not a vault or its ruleset is unusable. JSON document keys: `authoredEdges`, `authoredEdgesByType`, `command`, `compiled`, `derivedEdges`, `derivedEdgesByType`, `extensionCount`, `extensionsByKind`, `format`, `notes`, `notesByType`, `path`, `upToDate`, `vaultDigest`; when lint errors stop it, `command`, `compiled`, `counts`, `findings`, `format`.

### coverage

For each registered source that no other registered source supersedes, counts the spans in scope (all of them unless `_ruleset.yaml` narrows the scope), the spans cited and the spans not cited, and lists the spans cited in part only. A span counts as cited when a note cites it whole, cites each of its sentences, or, for a refused or structural note, cites a subtree that contains it. Notes that are pending or superseded do not count. A source that a later edition supersedes is left out, because `migrate-edition` moves its citations on; `--source KEY` measures one source whether or not it is superseded.

```text
usage: sabot coverage [-h] [--source KEY] [--vault PATH] [--json]
```

```sh
uv run sabot coverage --vault tests/fixtures/driftwood/vault
```

```text
source  in scope  cited  uncited  partial
dw1     51        51     0        0
citing notes dw1 mapped=46 refused=1 structural=1 superseded=1
```

The example exits 0. Exit codes: 0 every in-scope span is cited; 1 some are not; 3 refused (unknown `--source`, a registered source with no span file, an unreadable span file). JSON document keys: `command`, `complete`, `format`, `sources`.

### diff-editions

Compares the span files of two editions by hash and classifies each span as unchanged, editorial, substantive, added or removed, and finds spans that moved to another locator. With the extracted texts of both editions (`--old-text`, `--new-text`) it also counts changed words and pairs a removed span with a lightly reworded added one. It lists the notes to re-review. Differences are the report, not a failure, so the exit code is 0 unless input is refused. `--scope` may be repeated.

```text
usage: sabot diff-editions [-h] [--scope PREFIX] [--old-text PATH]
                           [--new-text PATH] [--vault PATH] [--json]
                           old_key new_key
```

```sh
uv run sabot diff-editions dw1 dw2 --vault tests/fixtures/driftwood/vault
```

```text
dw1 -> dw2
added: 3
editorial: 1
moved: 1
paired: 0
removed: 3
substantive: 1
unchanged: 46
...
```

The example exits 0. Exit codes: 0 compared; 3 refused (invalid or equal keys, a missing span file, unreadable text). JSON document keys: `affectedNotes`, `changedWords`, `classes`, `command`, `counts`, `format`, `moved`, `new`, `old`, `paired`, `scope`, `textsGiven`.

### migrate-edition

Moves each note's citations of the older edition to the newer one. Citations of unchanged and editorially changed spans are repointed; a note with a substantive or removed citation keeps its citations on the old edition. Any note with an editorial, substantive or removed change gets `stale: true` and a `stale-reason`, which a person clears after review. Every note is planned in memory first, so a note that cannot be edited safely refuses the whole run before anything is written. `--dry-run` writes nothing.

```text
usage: sabot migrate-edition [-h] [--dry-run] [--vault PATH] [--json]
                             old_key new_key
```

```sh
uv run sabot migrate-edition dw1 dw2 --dry-run --vault tests/fixtures/driftwood/vault
```

```text
dw1 -> dw2 (dry run, nothing written)
repointed: 45
stale-editorial: 1
stale-substantive: 1
stale-removed: 2
added spans to extract: 2
notes that would change: 49
...
```

The example exits 0. Exit codes: 0 migrated, or planned with `--dry-run`; 3 refused (invalid or equal keys, a missing span file, a note that cannot be rewritten line by line). A note whose front matter does not parse is not migrated and is listed under `unparsed` (JSON) or "notes not read" (text). JSON document keys: `added`, `changed`, `command`, `counts`, `dryRun`, `format`, `new`, `old`, `outcomes`, `unparsed`, `unresolved`, `written`.

### overlap

Measures the longest run of consecutive words each note body shares with the extracted source text, and reports its length and offsets in the body, never the words. A run of `--warn-at` words (default 8, or `--fail-at` when that is lower) warns and a run of `--fail-at` words (default 10) fails. With no `paths`, every note is checked; a path is read as given and then under the vault. The source text must lie outside the vault.

```text
usage: sabot overlap [-h] --source-text PATH [--fail-at WORDS]
                     [--warn-at WORDS] [--vault PATH] [--json]
                     [paths ...]
```

```sh
uv run sabot overlap clauses/cl.setup.md clauses/cl.hand-limit.md --source-text tests/fixtures/driftwood/rules-e1.txt --vault tests/fixtures/driftwood/vault
```

```text
note                      words  body offsets  status
clauses/cl.hand-limit.md  3      18-32         ok
clauses/cl.setup.md       3      133-148       ok

failed: 0
warned: 0
```

The example exits 0. Exit codes: 0 no run reaches `--fail-at`; 1 at least one does; 3 refused (source text inside the vault, a path outside the vault or not a note, unreadable text). JSON document keys: `command`, `failAt`, `failed`, `format`, `notes`, `unreadable`, `warnAt`, `warned`.

### recover

Rolls back a write that was killed part-way, and clears what it left: the journal directory (`.sabot-txn-*`), staged files (`.sabot-new-*`) and the write lock (`.sabot-lock`). It restores the vault to its state before that write and never completes it. Run it only when no other `sabot` process is writing the vault. See [Writes into a vault](#writes-into-a-vault).

```text
usage: sabot recover [-h] [--vault PATH] [--json]
```

```sh
uv run sabot recover --vault "$DEMO/vault"
```

```text
nothing to recover
```

The example exits 0. Exit codes: 0 recovered, or nothing to recover; 3 refused (not a vault, a journal that cannot be read, a rollback that could not be completed). JSON document keys: `clearedLock`, `command`, `format`, `removedStaged`, `rolledBack`.

### schema

Shows schema v1: node types, edge types, attributes, statuses and requirements. With `--vault` it merges in the extensions of that vault's `_ruleset.yaml` and marks them. `--type` or `--edge` shows one entry. Without `--vault` only the fixed schema is shown.

```text
usage: sabot schema [-h] [--type TYPE] [--edge EDGE] [--vault PATH] [--json]
```

```sh
uv run sabot schema --edge precedes
```

```text
edge type  domain                  range                   inverse  flags    origin
precedes   phase,clause,procedure  phase,clause,procedure  follows  acyclic  fixed
```

The example exits 0. Exit codes: 0 shown; 3 refused (unknown type or edge, unusable ruleset). JSON document keys: `command`, `edgeTypes`, `format`, `nodeTypes`, `ruleset`, `schemaVersion`; without `--type` or `--edge` also `attributes`, `constructKinds`, `statuses` and `statusRequirements`.

### search

Finds notes by property filters, by full-text relevance, or both. Give at least one term or one of `--type`, `--status` and `--where`.

```text
usage: sabot search [-h] [--all] [--type T] [--status S] [--where EXPR]
                    [--fields F,F] [--limit N] [--snippets] [--vault PATH]
                    [--json]
                    [terms ...]
```

Filters. `--where` takes `key=value`, `key!=value`, `key~text` (a case-insensitive substring), `has:key` or `missing:key`. **Conditions on the same property are alternatives, and conditions on different properties must all hold.** `--type` and `--status` are conditions on the properties `type` and `status`, and `has:key` and `missing:key` are conditions on `key`. So `--type clause --where type=keyword` selects clauses or keywords, and `--type clause --where status=refused` selects clauses that are refused. A list property matches when any item does; a link property compares by the target id, written bare or as a wikilink; a property that holds nothing (absent, null or an empty list) counts as absent, which `key!=value` accepts. Notes come back sorted by id when there are no terms.

Terms rank the notes that pass the filters. An argument of several words is a phrase whose words must be adjacent. Terms are alternatives unless `--all` is given. `--limit` defaults to 20 with terms and to no limit without. `--fields` adds properties to each row, `--snippets` adds an excerpt around the first match.

```sh
uv run sabot search tide marker --type clause --limit 3 --vault tests/fixtures/driftwood/vault
```

```text
rank  score   id                     type    status   title                         path
1     8.3065  cl.tide-wrap           clause  mapped   Tide marker wraps             clauses/cl.tide-wrap.md
2     6.3720  cl.tide-stay           clause  mapped   Tide marker stays at the top  clauses/cl.tide-stay.md
3     4.7636  cl.tide-wrap-decision  clause  derived  Marker wraps (decision)       clauses/cl.tide-wrap-decision.md
```

```sh
uv run sabot search --type clause --where status=refused --where status=superseded --vault tests/fixtures/driftwood/vault
```

```text
id                   type    status      title                       path
cl.hand-limit-draft  clause  superseded  Earlier hand limit reading  clauses/cl.hand-limit-draft.md
cl.solo-refused      clause  refused     Solo play                   clauses/cl.solo-refused.md
```

Both examples exit 0, as does a search that finds nothing. Exit codes: 0 searched; 2 usage (no term and no filter, a filter that is not one of the forms, a `--fields` name that is already a column); 3 refused (not a vault or an unusable ruleset). A note that cannot be read is skipped and named on standard error. JSON document keys: `command`, `format`, `results`; each result has `id`, `path`, `status`, `title` and `type`, the requested fields, and with terms `rank` and `score` and with `--snippets` `snippet`.

What full-text search indexes. Each note has two fields: its `title` property and its body. A wikilink in the body contributes its alias, or its target when it has none; text in code fences is indexed as written; a first level-one heading that repeats the title is skipped, so the title is not counted three times. Other properties are not indexed. A word is a run of ASCII letters and digits, lower-cased, with no stemming, so `squall's` is the words `squall` and `s`. The title counts twice the body. Ranking is BM25 over the notes that pass the filters, so a score depends on that set: scores are comparable only within one result list and mean nothing across two searches. Notes with equal scores are ordered by id.

### traverse

Lists the notes reachable from one note over edges, each once at its shortest distance, with the path that reached it. `--edge` may be repeated to follow only the named edges; an edge may be named by its authored name or its inverse in either direction. `--direction` defaults to `out`; `in` follows edges backwards under their inverse names, and `both` follows both. `--depth` defaults to 1 and ranges from 1 to 10. Rows are sorted by distance, then the last edge's name, then id. The start note is not listed, and a link to an id no note has is listed with no type or title.

```text
usage: sabot traverse [-h] [--edge E] [--direction {out,in,both}] [--depth N]
                      [--vault PATH] [--json]
                      id
```

```sh
uv run sabot traverse ph.ebb --direction both --vault tests/fixtures/driftwood/vault
```

```text
distance  id              type    title                 via
1         cl.turn-phases  clause  Four phases per turn  ph.ebb -defined-by-> cl.turn-phases
1         ph.sail         phase   Sail phase            ph.ebb -precedes-> ph.sail
1         ph.moor         phase   Moor phase            ph.ebb -returned-to-by-> ph.moor
```

The example exits 0. Exit codes: 0 traversed; 3 refused (unknown note id, unknown edge name, not a vault). JSON document keys: `command`, `format`, `results`; each result has `distance`, `id`, `title`, `type` and `via`, a list of hops with `edge`, `from` and `to`.

## Conventions shared by every command

**The vault argument.** Every command except `init` works on a vault named by `--vault PATH`, which defaults to the current directory. For `schema` the default is no vault, and the fixed schema is shown. `init` takes the directory to create instead. A directory without a `_ruleset.yaml` is not a vault and is refused with the code `not-a-vault`. `recover` takes `--vault` as well.

**Loading a vault.** Every command except `lint`, `init` and `recover` reads the ruleset, the schema version it names and the ruleset's extensions the same way, and refuses with exit 3 a vault where that fails: `ruleset-invalid` for a ruleset that cannot be read or has an extension that cannot be merged (a key written twice is one such case), `schema-version-unsupported` for a schema version this tool does not read. `lint` reports the same conditions as findings, `init` has no vault yet and `recover` has to work on a vault that cannot be read.

**`--json`.** Every command accepts `--json`, which makes standard output one JSON document with sorted keys, two-space indentation and a trailing line feed. Every document has `command` and `format` (the version of the output format, currently 1). Standard error carries human diagnostics only and no contract; in `--json` mode it is empty unless a note was skipped or a command has something to say about its input.

**Exit codes.**

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | The command ran and found something to fix: lint errors, an incomplete coverage, a failed check, a stale graph, an overlap that fails. |
| 2 | Usage error: an unknown option, a missing required option, a value out of range, or a search without term or filter. A message on standard error; no document on standard output. |
| 3 | Input refused: the command could not use what it was given. In `--json` mode standard output is `{"command", "error": {"code", "message"}, "format"}`; otherwise the message is on standard error as `sabot <command>: <code>: <message>`. |

`--help` exits 0.

**Writes into a vault.** `init`, `ingest`, `segment`, `migrate-edition` and `compile` (when its output lies in the vault) write through one transaction: every output is computed and checked in memory, staged as a hidden file beside its target, and then renamed into place with `_ruleset.yaml` last. A failure, an interrupt or a termination signal that Python can catch undoes the write, so the vault's bytes are what they were before; the command exits 3 with `write-failed` or `interrupted`. A file written outside the vault (`--store`, `--text-out`, `--emit-boundaries`, `compile --out` elsewhere) is staged and renamed too, and `ingest` removes what it wrote there when the vault write fails.

A process killed outright leaves a journal behind. Every command except `lint` and `recover` then refuses with `transaction-incomplete` until `sabot recover` rolls the write back; `lint` reports it as the `transaction-incomplete` class. A lock file, `.sabot-lock`, makes a second writer refuse with `vault-locked`; `recover` clears a stale one. Out of scope: two processes writing one vault through anything but these commands, and power loss on a file system that does not order a rename against the directory reaching disk.

**Determinism.** The same input gives the same standard output byte for byte on every run, whatever order the files were created in or the file system lists them in. Text is UTF-8 with line feeds. JSON keys are sorted, lists are sorted by a documented key, ranking scores are rounded to four decimal places, and hashes are fixed-length. Paths of vault files are written relative to the vault. No command reads a clock, uses randomness or the network, or runs a model. A test runs every command on two copies of a vault whose directories are listed in opposite orders and compares the output and every file written, and another checks that no output names the machine's paths, spelled as the platform writes them and as JSON escapes them. Only macOS has run the tests so far; the claim for Linux and Windows is a design goal until CI has run there.

**How an agent should call the tools.**

- Pass `--json` and `--vault` explicitly, and read standard output only.
- Read the exit code first. On 0 or 1 standard output is the command's document; on 3 it is the error document; on 2 there is none and standard error says why.
- Treat 1 as a result, not a crash: for `lint` and `coverage` the document lists what to fix.
- Run `lint --strict` before `compile`, and `coverage` before treating a source as fully cited. Run `migrate-edition --dry-run` before `migrate-edition`.
- The commands that write are `init`, `ingest`, `segment`, `compile` and `migrate-edition`; the others only read, except `recover`, which undoes an unfinished write. Give `--store`, `--text-out`, `--source-text` and `--emit-boundaries` paths outside the vault when the file holds source text.
- Do not edit span files by hand, and do not copy source wording into a note body; `overlap` and the `quotation-long` lint class catch both.

## Development

```sh
uv run pytest                           # tests
uv run ruff check                       # lint
uv run ruff format --check              # formatting
uv run mypy --strict src                # types
uv run python ci/check_content_guard.py # content guard
```

CI is set up to run these on Linux, macOS and Windows with Python 3.12 and 3.15; so far the tests have only been run on macOS. Tests drive the commands through `sabot_cli.cli.main` and the installed console script. A command that refuses input has a test that plants the refusal, and a test fails when a refusal code appears in the source that no test names. [tests/README.md](tests/README.md) lists the behaviours the tests hold and where each is tested. The Driftwood fixture, its source texts and its benchmark queries are described in [tests/fixtures/driftwood/README.md](tests/fixtures/driftwood/README.md).

## Layout

```
src/sabot_cli/
  cli.py            dispatcher; documents the shape every command module follows
  canonical.py      canonical JSON and its types, below every package that writes JSON
  output.py         the only writer of stdout: canonical JSON, tables, the error object
  exit_codes.py     the exit codes
  commands/         one module per command, and the options and helpers they share
  vault/            reads notes (text files, front matter, wikilinks) and writes them in transactions; knows no rules
  rules/            schema v1 (schema_v1.yaml), the ruleset file, lint checks, coverage, the compiled graph; imports sources and vault
  sources/          text extraction, segmentation, span files, boundaries, edition diff, migration planning, overlap; imports only vault
  search/           property filters, full-text ranking, traversal; imports only vault
tests/              pytest; README.md lists the behaviours tested; the Driftwood fixture (fixtures/driftwood); shared helpers (support/)
ci/                 content guard and its hash list
docs/               schema-v1.md, the notes reference
```

## Dependencies

Runtime dependencies are kept to the minimum and must be permissively licensed, with wheels for Linux, macOS and Windows, and no network or model use. `argparse` is used instead of a CLI framework.

- `PyYAML`: reads note front matter and the ruleset file, and writes the ruleset file; the standard library has no YAML parser. It uses libyaml when the installed wheel has it and falls back to its pure-Python parser otherwise.
- `pypdf`: extracts text from PDF sources in its layout mode; the standard library has no PDF reader, and a pure-Python package keeps PDF ingest free of an external program. It has no runtime dependencies of its own.

Development tools are `pytest`, `ruff`, `mypy` and `types-PyYAML`. Hatchling builds the package. Adding a runtime dependency requires a line here stating why.

## Content rule

The repository holds no ruleset text, card text or card art, and names no commercial game. Test fixtures use an invented game. The content guard in CI (`ci/check_content_guard.py`) refuses tracked text files that contain a term from a list kept only as SHA-256 hashes; to add a term, run `uv run python ci/check_content_guard.py --hash "<term>"` and append the output to `ci/content_guard_hashes.txt`.

## Relation to other repositories

- `sabot` is the engine. This repository never loads it; it reads and writes Markdown.
- `sabot-web` holds the console host that plays a game at a terminal. That host is a game client. This repository is a developer tool.

## Licence

MIT. See [LICENSE.md](LICENSE.md).
