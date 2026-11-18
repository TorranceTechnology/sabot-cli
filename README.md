# sabot-cli

Command-line tools and agent skills for building games on the [Sabot](https://github.com/TorranceTechnology/sabot) rules engine.

The tools turn a published ruleset into a typed rules graph and help an agent work with it. The graph is a folder of Markdown notes with YAML properties, so it opens as an Obsidian vault and needs no Obsidian to use.

## Status

Early. The repository holds no tools yet. Planned, in order:

- A schema for card-game rules graphs.
- Graph tools: ingest, segment, lint, compile, coverage and edition diff.
- Search over a graph: by property, by full text, and by following edges.
- Agent skills that drive the tools through extraction, review and test generation.

## Relation to other repositories

- `sabot` is the engine. This repository never loads it; it reads and writes Markdown.
- `sabot-web` holds the console host that plays a game at a terminal. That host is a game client. This repository is a developer tool.

## Setup, run and test

Nothing to run yet. The tools will be a Python package managed with `uv`; this section will carry the commands when the first tool lands.

## Licence

MIT. See [LICENSE.md](LICENSE.md).
