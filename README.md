# sabot-cli

Command-line tools and agent skills for building games on the [Sabot](https://github.com/TorranceTechnology/sabot) rules engine.

The tools turn a published ruleset into a typed rules graph and help an agent work with it. The graph is a folder of Markdown notes with YAML properties, so it opens as an Obsidian vault and needs no Obsidian to use.

## Status

Early. The `sabot` command exists with every command's options declared. Every command works except `search` and `traverse`; each of those exits 3 with the error code `not-implemented` until it is built. Planned, in order:

- A schema for card-game rules graphs.
- Graph tools: ingest, segment, lint, compile, coverage and edition diff.
- Search over a graph: by property, by full text, and by following edges.
- Agent skills that drive the tools through extraction, review and test generation.

## Commands

All are declared; none is implemented yet.

| Command | Purpose |
|---|---|
| `init` | Create an empty vault. |
| `ingest` | Register a source edition as a source note. |
| `segment` | Turn extracted text into a span file of locations and hashes. |
| `lint` | Check a vault against the schema. |
| `compile` | Write the machine graph. |
| `coverage` | Check that every in-scope span is cited. |
| `diff-editions` | Classify span changes between two editions. |
| `migrate-edition` | Move citations to the successor edition. |
| `overlap` | Detect copied source wording in note bodies. |
| `schema` | Show the schema a vault is written to. |
| `search` | Find notes by property filters and full-text relevance. |
| `traverse` | List notes reachable from one note over chosen edges. |

Every command accepts `--json`, which writes one JSON document to stdout. Exit codes: `0` success, `1` findings, `2` usage error, `3` input refused. A refusal in `--json` mode still prints `{"command", "format", "error": {"code", "message"}}` on stdout.

## Setup, run and test

Prerequisites: [uv](https://docs.astral.sh/uv/) and Python 3.12 or later (development uses 3.14, set in `.python-version`). Poppler's `pdftotext` is needed only for PDF ingest, once that command exists; it is an external program, not a Python dependency.

```sh
uv sync                                  # create the environment from uv.lock
uv run sabot --help                      # list the commands
uv run sabot <command> --help            # one command's options

uv run pytest                            # tests
uv run ruff check                        # lint
uv run ruff format --check               # formatting
uv run mypy --strict src                 # types
python ci/check_content_guard.py         # content guard
```

## Layout

```
src/sabot_cli/
  cli.py            dispatcher; documents the shape every command module follows
  output.py         the only writer of stdout: canonical JSON, tables, the error object
  exit_codes.py     the exit codes
  commands/         one module per command
tests/              pytest, driven through sabot_cli.cli.main or the installed console script
ci/                 content guard and its hash list
```

## Dependencies

Runtime dependencies are kept to the minimum and must be permissively licensed, with wheels for Linux, macOS and Windows, and no network or model use. `argparse` is used instead of a CLI framework.

- `PyYAML`: reads note front matter and the ruleset file; the standard library has no YAML parser.

Adding a runtime dependency requires a line here stating why.

## Content rule

The repository holds no ruleset text, card text or card art, and names no commercial game. Test fixtures use an invented game. The content guard in CI (`ci/check_content_guard.py`) refuses tracked text files that contain a term from a list kept only as SHA-256 hashes; to add a term, run `python ci/check_content_guard.py --hash "<term>"` and append the output to `ci/content_guard_hashes.txt`.

## Relation to other repositories

- `sabot` is the engine. This repository never loads it; it reads and writes Markdown.
- `sabot-web` holds the console host that plays a game at a terminal. That host is a game client. This repository is a developer tool.

## Licence

MIT. See [LICENSE.md](LICENSE.md).
