# Tests

The tests run each command in process through `sabot_cli.cli.main` and assert on standard output, standard error and the exit code. Two tests also run the installed console script as a subprocess. Vaults are copies of the invented Driftwood fixture (see [fixtures/driftwood/README.md](fixtures/driftwood/README.md)), and `support/plant.py` applies one named defect to a copy. Run them with `uv run pytest`.

The first line of a test module cites the behaviour IDs it covers. The table below defines those IDs.

## Behaviours

| ID | Behaviour | Test file |
|---|---|---|
| INI-1 | `init` creates an empty vault that `lint` accepts. | `test_init.py` |
| INI-2 | `init` refuses a directory that is not empty and writes nothing. | `test_init.py` |
| ING-1 | `ingest` records the SHA-256 and byte count of a text source in its source note. | `test_ingest.py` |
| ING-2 | `ingest` extracts the text of a PDF, with the default extractor and with `pdftotext` when selected. | `test_ingest.py` |
| ING-3 | `ingest` refuses `--text-out` inside the vault and writes nothing. | `test_ingest.py` |
| ING-4 | `ingest` refuses a key that is already registered. | `test_ingest.py` |
| ING-5 | `ingest --verify` reports a file that differs from the registered hash and writes nothing. | `test_ingest.py` |
| ING-6 | `--store` keeps the original under `<key>.<hash16>.<ext>`, and a store inside the vault is refused. | `test_ingest.py` |
| SEG-1 | Numbered mode gives one span per numbered rule. | `test_segment.py` |
| SEG-2 | A span file holds no word of the source text. | `test_segment.py` |
| SEG-3 | A wrapped line that starts with a digit joins the open span instead of starting one, unless it is the number the list reaches next or follows a blank line. | `test_segment.py`, `test_source_edge_cases.py` |
| SEG-4 | Contents entries with dot leaders and page-number lines make no span and add no words to one. | `test_segment.py` |
| SEG-5 | The self-checks name a duplicate or out-of-order locator, and nothing is written. | `test_segment.py` |
| SEG-6 | In sentences mode an inserted sentence leaves every other sentence's locator unchanged. | `test_segment.py` |
| SEG-7 | Headings mode gives slug-path locators, and dotted-letter numbering makes `4.2a` a child of `4.2`. | `test_segment.py` |
| SEG-8 | Boundaries mode hashes the spans a boundaries file proposes, and a second run gives identical bytes. | `test_segment.py` |
| SEG-9 | Boundaries mode names each defect of a boundaries file (gap, overlap, bad offset, duplicate locator, missing parent and the others) and writes nothing. | `test_segment.py` |
| SEG-10 | A rule-based result, emitted as a boundaries file and segmented again in boundaries mode, gives the same hashes. | `test_segment.py` |
| LNT-1 | `lint --strict` reports nothing on the fixture vault. | `test_lint.py` |
| LNT-P | For each lint class, a vault with one planted defect reports exactly that class, where it was planted. | `test_lint_probes.py` |
| LNT-2 | The lint classes the tool lists and the classes the probes plant are the same set. | `test_lint_probes.py` |
| LNT-3 | `--strict` raises pending, stale and open-question notes to errors. | `test_lint.py` |
| LNT-4 | A note of a vault extension is checked as its parent type. | `test_lint.py` |
| LNT-5 | An orphan note is a warning, and an error under `--strict`. | `test_lint.py` |
| CMP-1 | `compile` adds inverse edges and marks them derived. | `test_compile.py` |
| CMP-2 | `compile` refuses a vault with lint errors and writes nothing. | `test_compile.py` |
| CMP-3 | `compile --check` reports an out-of-date graph and then a current one. | `test_compile.py` |
| CMP-4 | The compile summary counts the vault's extensions and types. | `test_compile.py` |
| COV-1 | `coverage` passes when every in-scope span is cited. | `test_coverage.py` |
| COV-2 | `coverage` lists an uncited span. | `test_coverage.py` |
| COV-3 | `coverage` lists the sentences of a partly cited span that are not cited. | `test_coverage.py` |
| COV-4 | A refused note citing a subtree covers every span beneath it. | `test_coverage.py` |
| DIF-1 | `diff-editions` puts each changed span in its class. | `test_diff_editions.py` |
| DIF-2 | A pronoun-only change is editorial without any source text. | `test_diff_editions.py` |
| DIF-3 | With both texts given, a moved and lightly reworded span is paired with the one it replaced. | `test_diff_editions.py` |
| DIF-4 | The notes to re-review are exactly the citers of changed, removed or moved spans. | `test_diff_editions.py` |
| MIG-1 | `migrate-edition` re-points citations of unchanged and moved spans to the new edition. | `test_migrate_edition.py` |
| MIG-2 | Notes citing editorial, substantive or removed spans are marked stale with the matching reason. | `test_migrate_edition.py` |
| MIG-3 | `migrate-edition` writes nothing with `--dry-run`, and a second run changes nothing. | `test_migrate_edition.py` |
| MIG-4 | `lint --strict` fails until every note is on the new edition, and passes once the stale notes are fixed. | `test_migrate_edition.py` |
| OVL-1 | `overlap` fails a note that shares a run of words with the source, without printing the words. | `test_overlap.py` |
| OVL-2 | `overlap` passes the fixture vault's paraphrases. | `test_overlap.py` |
| SCH-1 | `schema` lists every fixed node type and edge type without a vault. | `test_schema_command.py` |
| SCH-2 | `schema --vault` marks a vault's extensions with their parents. | `test_schema_command.py` |
| QRY-1 | `search --type` and `--status` return exactly the matching notes, sorted by id. | `test_search_filters.py` |
| QRY-2 | `--where` on a link-valued property finds the notes that link to a target. | `test_search_filters.py` |
| QRY-3 | The `!=`, `~`, `has:` and `missing:` filters each select the expected notes. | `test_search_filters.py` |
| QRY-4 | A note that cannot be read is skipped and named on standard error. | `test_search_filters.py` |
| SRC-1 | A title match outranks a body match. | `test_search.py` |
| SRC-2 | A note matching more of the query terms ranks higher. | `test_search.py` |
| SRC-3 | A quoted phrase needs adjacent words, and `--all` needs every term. | `test_search.py` |
| SRC-4 | `--type` and `--limit` return at most the limit, all of the type asked for. | `test_search.py` |
| SRC-5 | Notes with equal scores are ordered by id. | `test_search.py` |
| SRC-6 | Terms rank only the notes that pass the filters. | `test_search.py` |
| TRV-1 | `traverse` lists the direct targets of a note at distance 1. | `test_traverse.py` |
| TRV-2 | Following incoming edges names each by its inverse, such as `has-part` and `overridden-by`. | `test_traverse.py` |
| TRV-3 | `--edge` and `--depth` limit the walk to the named edges and hops. | `test_traverse.py` |
| TRV-4 | A cycle ends the walk and lists each note once. | `test_traverse.py` |
| TRV-5 | An unknown start id is refused. | `test_traverse.py` |
| DET-1 | Every command gives byte-identical output and files whatever order the vault's directories are listed in. | `test_determinism.py` |
| DET-2 | No command's output names a temporary directory of the machine. | `test_determinism.py` |
| PKG-1 | The console script and `python -m sabot_cli` run and list every command. | `test_entry_point.py` |
| PKG-2 | Every command refuses a missing vault with the documented JSON error object. | `test_entry_point.py` |
| PKG-3 | Each package imports only from the layers below it. | `test_layering.py` |

## Other test groups

These cover rules that have no ID of their own.

| Subject | Test file |
|---|---|
| Writes into a vault are all-or-nothing, a killed write is refused until `recover`, `recover` trusts nothing a journal names, the lock records its writer, and a signal during rollback waits | `test_transaction.py` |
| Every command loads the ruleset and schema in the same way | `test_vault_loading.py` |
| A key written twice is refused, never read as the last value | `test_duplicate_keys.py` |
| Windows device names in note ids, file names and tracked paths | `test_reserved_names.py` |
| A registered source and its source note agree | `test_ruleset_agreement.py` |
| Registering a source edits `_ruleset.yaml` in place | `test_ruleset_writer.py` |
| The packaged schema is checked as it loads | `test_schema_definition.py` |
| Output is byte-identical across runs and listing orders, sorted, and names no machine path | `test_determinism.py` |
| Rules the tools state once: source key, span files, text files, front matter | `test_shared_rules.py` |
| The span-reference grammar | `test_span_ref.py` |
| Edge cases of segmentation, diffing and migration | `test_source_edge_cases.py` |
| The update loop of a new edition, end to end | `test_update_loop.py` |
| The YAML loader's typing of scalars | `test_yaml_loader.py` |
| Every refusal code in the source is named by a test | `test_refusal_probes.py` |
| The content guard in `ci/` | `test_content_guard.py` |

## Notes for writing tests

- A command that refuses input has a test that plants the refusal and checks that nothing was written.
- Tests that create files must not assume a case-sensitive file system, a POSIX permission model or `/` in a temporary path. Outputs name files relative to the vault with `/`; temporary paths use the platform's separator. `support/filesystem.py` has the markers for tests that need a case-insensitive file system, POSIX permissions or the ability to create a file named for a Windows device.
- The vault walk lists directories through one function (`sabot_cli.vault.walk.directory_entries`), so `force_listing_order` in `conftest.py` can make a test see directories in a chosen order.
- Tests import only public modules of `sabot_cli`, none whose name starts with an underscore.
