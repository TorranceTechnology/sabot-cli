# Driftwood fixture

Driftwood is an invented two-player card game of tides, boats and cargo. Its rules were written for these tests, in original wording, and resemble no published game. Every file here is plain UTF-8 text with LF line endings and ASCII characters only, so character offsets and byte offsets agree.

## The game in brief

Each player has a private Hand and a public Harbour. A shared face-down Stock supplies cards, and a shared face-up Wreck pile collects discards. A turn runs four phases in order: Ebb (draw to five cards), Sail (launch one boat, load cargo, sail loaded boats out for points), Flood (the tide marker rises, then each player in turn may play one Squall or pass) and Moor (discard to seven cards, then the turn passes and begins again at Ebb). A boat whose cargo outweighs its capacity is wrecked with everything aboard. A Squall beaches an opposing boat and strips its cargo, unless the boat is Buoyant or the tide marker is on its top mark. The game has two formats, the Short Tide (ten points win) and the Full Tide (twenty points win). One rule says the tide marker wraps from the top mark to the bottom mark and another says it stays on the top mark; the vault records that as an open question and resolves it.

## Files

| File | Purpose |
|---|---|
| `rules-e1.txt` | Edition one as `pdftotext -layout` output: title page, contents page with dot leaders, form feeds between pages, a bare page number in a `- n -` footer, dotted numbering with lettered sub-rules. 9 section headings and 42 rules, so 51 numbered items; 16 of them hold several sentences. Rule 5.4 wraps so that one continuation line begins with a digit (`3 units of weight ...`). |
| `rules-e2.txt` | Edition two: edition one with one planted change of each kind (below). |
| `handbook.md.txt` | The same game as a text with `#` headings and numbered items inside some headings, for `headings` mode. |
| `sheet.txt` | An unnumbered quick-start sheet of plain sentences in three paragraphs, followed by a page footer line. |
| `sheet-inserted.txt` | `sheet.txt` with one sentence inserted in the middle of the second paragraph. |
| `sheet.boundaries.jsonl` | A hand-written `boundaries` file for `sheet.txt`. |
| `vault/` | A clean schema v1 vault on edition one (`dw1`): 54 notes. |
| `benchmark-queries.yaml` | 15 queries with expected answers; its header explains the format. |

## Planted changes in `rules-e2.txt`

Locators are the edition-one and edition-two rule numbers. Every other rule, and every page number, is identical.

| Kind | Locator | Change |
|---|---|---|
| Editorial | `3.1` | The pronoun in the second sentence changes (`she may` becomes `they may`). |
| Substantive | `5.1a` | The launch limit changes from one boat to two. |
| Added | `9.4` | A new rule ends the solo-play section. |
| Removed | `7.3` | The game-end rule is deleted; no other number moves. |
| Moved unchanged | `4.2a` in edition one, `2.5` in edition two | The text is identical. |
| Moved and lightly reworded | `6.8` in edition one, `6.7a` in edition two | Two words change (`next` to `following`, `launching` to `launch`); word similarity is about 0.89. |

Expected classes for `diff-editions dw1 dw2`: `3.1` editorial, `5.1a` substantive, `9.4` and `6.7a` added, `7.3` and `6.8` removed, `4.2a` removed and `2.5` added with equal hashes (moved). `6.8` and `6.7a` are paired when both texts are given. The re-review set (`affected-notes`) is the citers of `5.1a`, `7.3`, `4.2a` and `6.8`: `act.launch-boat`, `win.first-to-target`, `ph.ebb` and `cl.launch-bonus`. The editorial change to `3.1` is cited by `cl.setup`, which migration marks stale for a light check.

## The vault

- `_ruleset.yaml` declares two extensions: the node type `win-condition` (parent `rule-process`, prefix `win.`) and the attribute `launch-cost` (on `action`, an integer).
- Every node type and every edge type of schema v1 is used. The vault holds a `precedes` chain of four phases closed by a `returns-to` loop, a refused note citing a subtree (`dw1#9/*`), a structural note citing another subtree (`dw1#1/*`), a derived note with a rationale, a superseded note linking its successor, a resolved open question with the clauses it concerns, and both variants with a parameter whose value differs.
- Every span of `dw1` is cited: whole, by subtree, or sentence by sentence (rules 4.1, 4.4, 6.1 and 6.2 are cited only by their sentences).
- Note bodies are original paraphrase; none shares a run of eight words with `rules-e1.txt`.
- `src.dw2` and `spans/dw2.jsonl` are present so edition diffs and migrations can run, but `dw2` is not listed in `_ruleset.yaml` `sources` and `src.dw2` has no `supersedes`. A strict lint of edition one therefore stays clean. `tests/support/plant.py` provides `register_second_edition`, which registers `dw2` as the successor of `dw1`; migration tests and the `source-superseded` defect start from it.
- The `win-condition` note is checked as a `rule-process`.

## Span files

`vault/spans/dw1.jsonl` and `vault/spans/dw2.jsonl` were written by the `segment` command from `rules-e1.txt` and `rules-e2.txt`:

```sh
sabot segment rules-e1.txt --source dw1 --mode numbered --numbering dotted-letter --vault <vault>
sabot segment rules-e2.txt --source dw2 --mode numbered --numbering dotted-letter --vault <vault>
```

A record is one JSON object per line with sorted keys and compact separators. A sentence record has locator `<span>/s<n>` and parent `<span>`. `offset` is the character offset in the rules text where the span (or sentence) starts; `position` counts records from zero in file order. `sha256` hashes the text after NFKC, leading-number removal, whitespace collapse and case folding (first 16 hex characters of SHA-256). `editorialSha256` hashes the same text with pronouns unified, keeping only letters and digits, so a difference in spacing, hyphenation at a line break or punctuation does not change it. When the normalisation changes, regenerate both files with the commands above; the tool's output is authoritative.

## Boundaries file

`sheet.boundaries.jsonl` has one record per line: span records carry `locator`, `parent`, `start`, `end` (exclusive character offsets into `sheet.txt`) and `page`; the `skip` record covers the page footer and carries a `reason`. Paragraph records are parents that contain their sentence records; the sentence records are the leaves. Every non-whitespace character of `sheet.txt` lies in exactly one leaf or the skip record. `sheet.txt` has no numbers, so in `sentences` mode its footer line becomes a sentence of its own.

## Support code

- `tests/support/plant.py` applies one named defect to a copy: one defect per lint class of schema v1 (named by the class), eleven boundaries defects and two segmenter defects. `defect_names()` lists them.
- `tests/support/scale_vault.py` generates a schema-valid vault of any size from invented words.
- `tests/support/minimal_pdf.py` writes a small text PDF that both `pypdf` and `pdftotext` can read.
