# Schema v1 reference

This is the reference for writing the notes of a rules graph. Read it before you write or review notes, whether you are a person or an agent. The schema itself lives in `src/sabot_cli/rules/schema_v1.yaml`; `sabot schema` prints it, `sabot lint --list-classes` prints the lint classes, and `sabot lint --strict` is the test of whether a note is finished. Where this page and the tools disagree, the tools are right and this page is wrong.

Every example note below is a note of the Driftwood fixture vault (`tests/fixtures/driftwood/vault`), an invented two-player game, copied without change. Each passes `sabot lint --strict` in that vault. The wording in the notes is original paraphrase.

## Contents

1. [The vault](#the-vault)
2. [The note](#the-note)
3. [Attributes](#attributes)
4. [Node types](#node-types)
5. [Edge types](#edge-types)
6. [Span references](#span-references)
7. [Span files and boundaries files](#span-files-and-boundaries-files)
8. [The ruleset file](#the-ruleset-file)
9. [Source notes, editions and errata](#source-notes-editions-and-errata)
10. [Lint classes](#lint-classes)
11. [From the graph to a conformance clause list](#from-the-graph-to-a-conformance-clause-list)
12. [Stale notes and the edition update loop](#stale-notes-and-the-edition-update-loop)
13. [Language limits](#language-limits)

## The vault

A vault is a directory that holds a file named `_ruleset.yaml` and the notes of one ruleset. It is an ordinary folder of Markdown files, so it also opens as an Obsidian vault, but no tool here needs Obsidian. `sabot init <directory> --ruleset <id>` creates an empty vault with this layout:

```text
_ruleset.yaml          the ruleset file
spans/                 one span file per source: spans/<key>.jsonl
sources/               source notes, src.<key>.md
clauses/ terms/ variants/ parameters/ rulings/ examples/ questions/ relations/
model/                 zones/ phases/ procedures/ actions/ decisions/ keyword-actions/
                       card-types/ characteristics/ keywords/ rule-processes/ windows/ patterns/
build/                 constructs/ obligations/
_index/graph.json      written by `sabot compile`; not created by init
```

The folders are a convention. A note is found wherever it lies under the vault root: every file ending in `.md` is read, except files named `README.md` and everything inside a directory whose name starts with `_` or `.`. What a note is comes from its `type` property, never from its folder. The extracted text of a source is never kept in the vault; only the span file, which holds locations and hashes, is.

The three sides of the graph, named by the `side` of each node type, are the source side (what the published text says), the model side (the game's structure in the rules engine's terms) and the build side (artefacts of the generated game).

## The note

A note is a Markdown file: a YAML mapping between two lines that hold only `---`, then the body. The file name without `.md` equals the note's `id`. A key may appear once in the mapping, at any depth: a note that writes `status` twice is refused (`frontmatter-invalid`) rather than read as its last value.

The body follows one convention:

1. A level-one heading (`# Title`) that repeats the `title` property.
2. The paraphrase, one or more paragraphs in the author's own words. This is the part of the note that says what the source means.
3. Optionally a `## Notes` section for commentary, history or decisions that are not the paraphrase.

The tools use the convention this way. `compile` records as a note's paraphrase the text between the first `# ` heading and the first `## ` heading. `search` ignores a first heading that repeats the title. `lint` refuses a quoted run of 60 or more characters in the body (`quotation-long`) and warns about any wikilink in the body (`body-link`), because links belong in the front matter. `overlap` measures the longest run of words a body shares with the extracted source text. Write the paraphrase in your own words; do not copy the source.

Front matter values are read narrowly so that the editor cannot change a note's meaning: only `true` and `false`, plain decimal integers and plain decimal numbers are read as such, and everything else, including `yes`, `on` and `2026-01-15`, stays text. Dates must be written `YYYY-MM-DD`. Quote text that YAML would otherwise read as something else.

An edge is a front matter key whose name is an edge type and whose value is a list of quoted wikilinks:

```yaml
precedes:
  - "[[ph.sail]]"
```

A link is `[[id]]`, `[[id#heading]]` or `[[id|alias]]`; only the id counts. It must be quoted: an unquoted `[[id]]` is read by YAML as a nested list and fails with `link-malformed`.

File `clauses/cl.hand-limit.md`, a mapped clause with one citation and one edge:

```markdown
---
id: cl.hand-limit
type: clause
title: Hand limit
status: mapped
confidence: high
rev: 2
schema: 1
modality: must
clause-id: DW-HAND-LIMIT
tags:
  - "hand"
source:
  - "dw1#4.5"
part-of:
  - "[[ph.moor]]"
---

# Hand limit

At the moor phase a hand larger than seven is cut down to seven by discarding.
```

## Attributes

### Required on every note

| Attribute | Type | Rule |
|---|---|---|
| `id` | id | Lowercase letters, digits and hyphens in parts separated by dots, starting with a letter (`^[a-z][a-z0-9-]*(\.[a-z0-9-]+)*$`). Stable and never reused. Equals the file name without `.md` and starts with the prefix of the note's type. The part before the first dot may not be a name Windows reserves for a device (`con`, `prn`, `aux`, `nul`, `com1` to `com9`, `lpt1` to `lpt9`, in any letter case), because a note file is named for its id and could not be checked out there (`id-reserved-name`). |
| `type` | enum | A node type: a fixed type below or one the ruleset declares. |
| `title` | text | A short label in the author's own words. |
| `status` | enum | One of `mapped`, `refused`, `derived`, `structural`, `pending`, `superseded`. |
| `confidence` | enum | `high`, `medium` or `low`: how sure the author is that the note says what the source means. |
| `rev` | int | Revision counter, at least 1, raised on every edit that changes meaning. |
| `schema` | int | The schema version the note was written under; equals the ruleset's `schemaVersion`. |

A required attribute that is absent, null, empty text or an empty list is missing (`required-missing`).

### Optional attributes

An attribute that names node types in the column "Allowed on" is refused on any other type (`attribute-unknown`). A key that is neither an attribute nor an edge name nor an inverse name is refused for the same reason, unless the ruleset declares it.

| Attribute | Type | Allowed on | Allowed values or form | Meaning |
|---|---|---|---|---|
| `source` | list of span references | any |  | Span references that ground the note. |
| `modality` | enum | `clause` | `must`, `must-not`, `may`, `definition`, `procedure`, `outcome` | What kind of statement a clause makes. |
| `scope` | list of ids | any |  | Variant ids the note applies under; empty or absent means every variant. Not the ruleset's `scope`, which lists spans. |
| `chooser` | enum | `decision-point` | `turn-player`, `non-turn-player`, `controller`, `owner`, `affected-player`, `rules` | Who makes the choice. |
| `refusal` | text | any |  | Why the engine will not model this; required when the status is `refused`. |
| `rationale` | text | any |  | Why a derived note exists; required when the status is `derived`. |
| `construct` | list of construct kinds | any |  | The engine construct kinds a note is meant to become; a hint on all but construct notes, and required on construct notes. The kinds are `zone`, `phase`, `phase-transition`, `command`, `affordance`, `reducer`, `guard`, `trigger`, `interceptor`, `state-check`, `window-blocking`, `window-lapsing`, `modifier-layer`, `visibility-grant`, `ability`, `card-script`, `random-stream`, `definition-data`, `match-end` and `none`. |
| `admits` | enum | `timing-window` | `any`, `pass-only` | What players may do inside the window. |
| `simultaneity` | enum | `procedure` | `batch` | Marks a procedure whose steps apply together. |
| `kind` | enum | `open-question`, `source` | open-question: `source-defect`, `restatement-conflict`, `terminology-conflict`, `gap`, `inference`; source: `edition`, `errata`, `ruling`, `faq` | The kind of open question, or the kind of source document. |
| `ownership` | enum | `zone` | `player`, `shared` | Whether each player has an instance of the zone or all share one. |
| `visibility` | enum | `zone` | `public`, `hidden`, `owner` | Who can see the cards in the zone. |
| `ordered` | bool | `zone` |  | Whether the order of cards in the zone matters. |
| `values` | list of text | `parameter` |  | One `variant-id=value` string per variant the parameter differs in. |
| `relation-type` | enum | `relation` | any edge type | The edge type the relation note stands for. |
| `condition` | text | `relation` |  | A qualifier on the relation, in the author's words. |
| `stale` | bool | any |  | True when an edition migration flagged a cited span as changed. |
| `stale-reason` | enum | any | `editorial`, `substantive`, `removed` | Why the note is stale. |
| `clause-id` | text | any |  | An existing clause-map id to keep when a map is compiled from the graph. |
| `scope-key` | text | any |  | The classification scope key a refused note accounts for. |
| `surface` | text | `construct` |  | The classification element id a construct note stands for. |
| `fixtures` | list of text | `test-obligation` |  | Fixture ids that discharge the obligation. |
| `tests` | list of text | `test-obligation` |  | Test names that discharge the obligation. |
| `tags` | list of text | any |  | Free-form labels. |
| `key` | text | `source` |  | The source key used at the start of span references. |
| `label` | text | `source` |  | The name clause maps and reports use for the source. |
| `edition` | text | `source` |  | The edition name or number. |
| `effective` | date | `source` |  | The date the edition took effect. |
| `url` | text | `source` |  | Where the document was obtained. |
| `archive-url` | text | `source` |  | An archived copy of the page, when one exists. |
| `retrieved` | date | `source` |  | The date the copy was obtained. |
| `bytes` | int | `source` |  | The size of the original file. |
| `sha256` | text | `source` |  | The SHA-256 of the original file. |
| `extractor` | text | `source` |  | The tool and version that extracted the text. |
| `segmenter` | text | `source` |  | The segmentation mode used for the span file. |
| `supersedes` | text | `source` |  | The key of the source this one replaces. |
| `amends` | text | `source` |  | The key of the source this one corrects. |
| `spans` | text | `source` |  | The vault-relative path of the span file. |

A value of the wrong kind for its attribute (text, date, whole number, true or false, a value outside an enum, a list element of the wrong kind) is reported as `enum-invalid`, whatever the attribute's type.

### Requirements by status

`status` says how a note relates to the source text. Lint enforces these requirements (`status-requirement`, and `superseded-without-successor` for superseded notes):

| Status | Meaning | Lint requires |
|---|---|---|
| `mapped` | The engine models what the cited text says. | `source` |
| `refused` | The engine will not model it. | `source` and `refusal`; the note must have no `implemented-by` edge. |
| `derived` | A design default or decision that no source text states. | `rationale` (no `source` needed). |
| `structural` | Framing that carries no obligation, such as section headings, a contents list or a registered source. | Nothing further. |
| `pending` | Not yet decided. `--strict` makes it an error. | Nothing further. |
| `superseded` | Replaced by a newer note; kept so the history stays visible. | An authored `refines` or `resolved-by` edge to its successor. Its citations do not count toward coverage. |

The schema file says `source` is needed by every status except `derived`; lint enforces it for `mapped` and `refused` only.

File `clauses/cl.solo-refused.md`, a refused clause that cites a whole section by subtree:

```markdown
---
id: cl.solo-refused
type: clause
title: Solo play
status: refused
confidence: high
rev: 1
schema: 1
modality: procedure
scope-key: "scope:solo-play"
refusal: Solo play needs an automated opponent policy, which the rules engine does not model, so the section is out of scope.
source:
  - "dw1#9/*"
references:
  - "[[cl.formats]]"
---

# Solo play

The one-player variant is declined as a whole section.
```

File `clauses/cl.tide-wrap-decision.md`, a derived clause that records a decision:

```markdown
---
id: cl.tide-wrap-decision
type: clause
title: Marker wraps (decision)
status: derived
confidence: medium
rev: 1
schema: 1
modality: outcome
rationale: The wrap reading is a complete procedure while the stay reading is a one-line clarification, so the procedure is taken as the rule.
refines:
  - "[[cl.tide-wrap]]"
---

# Marker wraps (decision)

The marker returns to the bottom mark after the top mark. The clarification that it stays put is set aside.
```

File `clauses/cl.hand-limit-draft.md`, a superseded clause with a notes section:

```markdown
---
id: cl.hand-limit-draft
type: clause
title: Earlier hand limit reading
status: superseded
confidence: low
rev: 1
schema: 1
modality: must
source:
  - "dw1#4.5"
refines:
  - "[[cl.hand-limit]]"
part-of:
  - "[[ph.moor]]"
---

# Earlier hand limit reading

An earlier extraction read the limit as six cards. It stays in the vault so the history of the correction remains visible.

## Notes

Replaced by the hand limit note.
```

### Requirements by node type

These are required in addition to the attributes required on every note (`type-requirement`). A declared node type is checked as the fixed type it extends.

| Node type | Also requires |
|---|---|
| `clause` | `modality` |
| `decision-point` | `chooser` |
| `zone` | `ownership`, `visibility`, `ordered` |
| `parameter` | `values`, and an authored `applies-in` edge |
| `relation` | `relation-type` |
| `open-question` | `kind` |
| `timing-window` | `admits` |
| `construct` | `construct` |
| `test-obligation` | another note naming it with a `tested-by` edge |
| `source` | `key`, `label`, `sha256`, `bytes`, `retrieved`, `spans` |
| `example` | an authored `illustrates` edge |
| `ruling` | an authored `illustrates` or `refines` edge |

Every other node type has no requirement of its own.

File `model/zones/zone.hand.md`, a zone with its three required attributes:

```markdown
---
id: zone.hand
type: zone
title: Hand
status: mapped
confidence: high
rev: 1
schema: 1
ownership: player
visibility: owner
ordered: false
source:
  - "dw1#2.2"
references:
  - "[[zone.harbour]]"
---

# Hand

Private cards held by one player. Only the holder sees them and their arrangement is irrelevant.
```

File `parameters/par.target-score.md`, a parameter whose value differs between two variants:

```markdown
---
id: par.target-score
type: parameter
title: Target score
status: mapped
confidence: high
rev: 1
schema: 1
values:
  - "var.short=10"
  - "var.full=20"
source:
  - "dw1#7.2"
applies-in:
  - "[[var.short]]"
  - "[[var.full]]"
---

# Target score

The number of points that ends the game. It is smaller in the quick format than in the long one.
```

File `relations/rel.launch-needs-card.md`, an edge written as a note so that it can carry a condition. `compile` records it as a node; it adds no edge of its own:

```markdown
---
id: rel.launch-needs-card
type: relation
title: Launching needs a card from hand
status: mapped
confidence: high
rev: 1
schema: 1
relation-type: requires
condition: the card paid comes from the same hand as the boat
source:
  - "dw1#5.1"
references:
  - "[[act.launch-boat]]"
  - "[[zone.hand]]"
---

# Launching needs a card from hand

Reifies the launch cost as a requirement tied to the hand.
```

## Node types

The id of a note starts with the prefix of its type. The id and the file name must agree.

| Node type | Id prefix | Side | Meaning |
|---|---|---|---|
| `source` | `src.` | source | A pinned copy of one published document. |
| `clause` | `cl.` | source | One normative statement. |
| `term` | `term.` | source | A word the rules define. |
| `variant` | `var.` | source | A play format or option. |
| `parameter` | `par.` | source | A value that differs between variants. |
| `ruling` | `rul.` | source | A published decision about a situation. |
| `example` | `ex.` | source | A worked case from the source or written for a test. |
| `open-question` | `oq.` | source | An ambiguity or defect that waits for a human decision. |
| `zone` | `zone.` | model | A place cards can be. |
| `phase` | `ph.` | model | A phase or step of a turn. |
| `procedure` | `proc.` | model | A multi-step routine used from several places. |
| `action` | `act.` | model | Something a player may choose to do. |
| `decision-point` | `dp.` | model | A choice the rules give to a named player. |
| `keyword-action` | `ka.` | model | A verb with a defined meaning. |
| `card-type` | `ct.` | model | A kind of card, or a group of kinds. |
| `characteristic` | `ch.` | model | A value a card carries or derives. |
| `keyword` | `kw.` | model | A named ability shorthand. |
| `rule-process` | `rp.` | model | A check applied to the game state whenever the rules call for it. |
| `timing-window` | `tw.` | model | A point at which players may act or respond. |
| `trigger-pattern` | `tp.` | model | A class of event that can set off an effect. |
| `replacement-pattern` | `rpl.` | model | A class of event that an effect can replace. |
| `ongoing-pattern` | `og.` | model | A class of lasting modification and where it applies in order. |
| `relation` | `rel.` | model | An edge written as a note so it can carry qualifiers. |
| `construct` | `cst.` | build | A named artefact of the generated game. |
| `test-obligation` | `to.` | build | A requirement for a test, normally generated. |

## Edge types

An edge is written on the note it leaves, under the edge name, as a list of quoted wikilinks. `domain` is the set of node types an edge may leave and `range` the set it may reach; `any` admits every type. Lint checks both against the type of each note, or the fixed type a declared type extends (`edge-domain`, `edge-range`).

| Edge | From (domain) | To (range) | Inverse | Flags | Meaning |
|---|---|---|---|---|---|
| `defines` | `clause` | `term`, `zone`, `phase`, `keyword`, `keyword-action`, `card-type`, `characteristic`, `rule-process`, `timing-window`, `procedure`, `action`, `parameter`, `variant` | `defined-by` | none | The clause gives the target its meaning. |
| `refines` | `clause`, `keyword`, `ruling` | `clause`, `keyword` | `refined-by` | none | The source narrows or details the target. |
| `restates` | `clause` | `clause` | `restates` | symmetric | Both clauses state one rule in different places. |
| `overrides` | `clause`, `keyword` | `clause`, `keyword`, `action` | `overridden-by` | none | Where both apply, the source wins. |
| `exception-to` | `clause`, `keyword` | `clause`, `keyword` | `has-exception` | none | The source carves a bounded case out of the target. |
| `precedes` | `phase`, `clause`, `procedure` | `phase`, `clause`, `procedure` | `follows` | acyclic | The source happens before the target. |
| `returns-to` | `phase`, `clause` | `phase` | `returned-to-by` | none | An explicit loop back; exempt from the acyclic rule of `precedes`. |
| `part-of` | `phase`, `clause`, `procedure`, `rule-process` | `phase`, `procedure` | `has-part` | none | The source is a component of the target. |
| `invokes` | `clause`, `phase`, `rule-process` | `procedure` | `invoked-by` | none | The source runs the target routine. |
| `occurs-in` | `action`, `timing-window`, `trigger-pattern`, `decision-point` | `phase`, `procedure` | `hosts` | none | The source takes place during the target. |
| `requires` | `action`, `clause` | any | `required-by` | none | The source needs the target to hold or exist. |
| `triggers` | `clause`, `action`, `procedure`, `rule-process`, `keyword-action` | `trigger-pattern` | `triggered-by` | none | The source produces an event of the target class. |
| `defers` | `clause`, `keyword` | `trigger-pattern`, `timing-window` | `deferred-by` | none | The source postpones collection of the target or narrows it. |
| `replaces` | `clause`, `replacement-pattern` | `clause`, `procedure` | `replaced-by` | none | The source substitutes for the target. |
| `decided-by` | `clause` | `decision-point` | `decides` | none | The target says who chooses. |
| `parameterised-by` | `clause`, `rule-process` | `parameter` | `parameterises` | none | The source reads its number or setting from the target. |
| `applies-in` | `clause`, `parameter` | `variant` | `governs` | none | The source holds in the target variant. |
| `references` | any | any | `referenced-by` | none | An untyped cross-reference, to be replaced by a typed edge where one fits. |
| `conflicts-with` | `clause` | `clause` | `conflicts-with` | symmetric | The two clauses cannot both be followed; an open question must record it. |
| `resolved-by` | `open-question` | `clause` | `resolves` | none | The target is the decision that closes the question. |
| `illustrates` | `example`, `ruling` | any | `illustrated-by` | none | The source shows the target at work. |
| `implemented-by` | any | `construct` | `implements` | none | The target construct realises the source; written by the build stage. |
| `tested-by` | any | `test-obligation` | `verifies` | none | The target obligation checks the source; written by the generator. |

Rules for edges:

- **Inverses are computed and never written.** Write only the edge name on the note it leaves. `sabot compile` adds the inverse edge to the graph, marked `derived`, and `search` and `traverse` show it under the inverse name when they follow an edge backwards. Writing an inverse name in front matter is an error (`inverse-authored`); the message names the edge to write on the other note.
- **A symmetric edge is written on one end only.** `restates` and `conflicts-with` are their own inverse. Writing the pair on both notes is an error (`symmetric-duplicated`).
- **An acyclic edge may not form a cycle** (`precedes-cycle`, which checks every edge flagged acyclic, including declared ones). `returns-to` exists so that a loop, such as the last phase of a turn returning to the first, is written without breaking the `precedes` chain.
- **`conflicts-with` needs an open question.** Some `open-question` note must cite a span that both clauses cite (`conflict-without-question`). The same pair may not also be joined by `restates` (`restates-conflicts-pair`), and a pair joined by `overrides` should not also be joined by `exception-to` (`overrides-and-exception`, a warning).
- **An edge target must exist** (`link-dangling`), and every value must be a quoted wikilink (`link-malformed`).
- **`superseded`, `example` and `ruling` notes need their edges** as the tables above say.
- No edge name and no inverse name may equal an attribute name.

File `questions/oq.tide-overflow.md`, an open question that records a conflict between two clauses and names the clause that resolves it:

```markdown
---
id: oq.tide-overflow
type: open-question
title: What happens at the top of the tide track
status: mapped
confidence: medium
rev: 1
schema: 1
kind: restatement-conflict
source:
  - "dw1#6.1/s2"
  - "dw1#8.3"
references:
  - "[[cl.tide-stay]]"
  - "[[cl.tide-wrap]]"
resolved-by:
  - "[[cl.tide-wrap-decision]]"
---

# What happens at the top of the tide track

One rule sends the marker back to the bottom mark and another keeps it at the top mark. Both cannot be followed.

## Notes

Resolved by adopting the wrap reading.
```

## Span references

A span reference is how a note says which part of the source it rests on. The grammar is:

```text
<source-key>#<locator>              the span itself
<source-key>#<locator>/s<n>         sentence n of the span, counting from 1
<source-key>#<locator>/*            the span and everything beneath it
```

- A source key is lowercase letters, digits, dots and hyphens, starting with a letter or digit (`^[a-z0-9][a-z0-9.-]*$`). It is the `key` of a source note.
- A locator is one or more segments separated by `/`. A segment is lowercase letters and digits, which may be joined by single dots or hyphens (`4.2a`, `hand-limit`). A locator may not end in a segment that reads as a sentence (`s1`, `s2`, and so on), because that ending would be read as the sentence form.
- The **sentence form** names one sentence of a span. Sentence records exist only for spans of two or more sentences; a span of one sentence has none, and `dw1#3.2/s1` is `span-unknown` when `dw1#3.2` is a single sentence.
- The **subtree form** names a span together with all spans beneath it in the span file. A plain citation covers only the span's own text, which for a parent is the text before its first child, such as a heading.
- **Only notes with the status `refused` or `structural` may cite a subtree** (`span-subtree-misused`). A refusal or a piece of framing can honestly account for a whole section; a mapped note cannot. `coverage` also counts a subtree citation only from those two statuses.
- A reference that is not in the grammar, or names a span or sentence the source's span file lacks, or names a source with no readable span file, is `span-unknown`.

Coverage counts a span as cited when it is cited whole, when each of its sentences is cited, or when a `refused` or `structural` note cites the subtree of it or of an ancestor. Citations by `pending` and `superseded` notes do not count.

File `sources/src.dw1.md` is shown in [Source notes](#source-notes-editions-and-errata).

## Span files and boundaries files

A span file, `spans/<key>.jsonl`, lists the spans of one source. Each line is one JSON object with sorted keys and compact separators; the file ends with a line feed. There is deliberately no text field: a record holds only where a span is and what it hashes to, so the vault holds no source text. The `segment` command writes the file; do not edit it by hand.

| Key | Meaning |
|---|---|
| `source` | The source key. |
| `locator` | The locator of the span or sentence. |
| `span` | `<source>#<locator>`, the form a citation uses. |
| `kind` | `span`, or `sentence` for one sentence of a span. |
| `parent` | The locator of the parent span, or null. A sentence record's parent is its span. |
| `page` | The page the span starts on. |
| `offset` | Character offset in the extracted text where the span starts. |
| `words` | Word count of the normalised text. |
| `sentences` | Sentence count of a span; 1 on a sentence record. |
| `position` | Position of the record in the file, from zero. |
| `sha256` | The first 16 hex characters of the SHA-256 of the normalised text. |
| `editorialSha256` | The same, over a form that ignores spacing, punctuation and pronoun gender, so an editorial change can be told from a substantive one. |

Example record:

```json
{"editorialSha256":"1ac97d522c5a419d","kind":"span","locator":"1.3","offset":980,"page":3,"parent":"1","position":3,"sentences":3,"sha256":"d6e0c3f00e47f85c","source":"dw1","span":"dw1#1.3","words":16}
```

Segmentation modes decide the locators: `numbered` uses the rule numbers of the text (`4.2a`, parent by number), `headings` uses heading slugs joined by `/` with numbered items beneath a heading, `sentences` gives every sentence a locator `s-<8 hex>` taken from its hash, and `boundaries` takes the spans from a boundaries file. `segment` writes no span file unless its own checks pass: no duplicate locators, every locator citable (`locator-invalid`), at least one span, locators not descending among siblings, and page numbers not decreasing. A sentence that appears twice gets `s-<8 hex>` for its first occurrence and `s-<8 hex>-2`, `-3` for the repeats.

A boundaries file proposes spans for the `boundaries` mode, so that an agent can mark the spans of a document the rule-based modes cannot read and the tool still checks and hashes them. One JSON object per line. Offsets are character offsets into the extracted text, the end exclusive.

| Record | Keys | Meaning |
|---|---|---|
| Span | `locator`, `parent` (or null), `start`, `end`, and optionally `page` | One span. |
| Skip | `skip` (true), `reason`, `start`, `end` | Text that is deliberately not a span, such as a page number. |

A record that other records name as their parent is a parent. Its range covers its children, and its own text is whatever lies before its first child. Every non-whitespace character of the text must lie in exactly one leaf span, one skip or one parent's own text. The checks, each reported by name, are `child-outside-parent`, `duplicate-locator`, `gap`, `locator-malformed`, `missing-parent`, `offset-order`, `offset-out-of-range`, `overlap`, `parent-cycle` and `record-malformed`. `segment --emit-boundaries FILE` writes the boundaries of a rule-based segmentation, including one whose checks failed, so that it can be repaired and fed back; a boundaries-mode run whose file was refused writes none. The fixture holds an example, `tests/fixtures/driftwood/sheet.boundaries.jsonl`.

## The ruleset file

`_ruleset.yaml` holds the ruleset's identity, its registered sources, its scope and its schema extensions. It has exactly five keys; any other key is refused.

File `_ruleset.yaml`, as in the fixture:

```yaml
ruleset: driftwood
schemaVersion: 1
sources:
  - key: dw1
    note: src.dw1
scope:
  dw1: []
extensions:
  nodeTypes:
    win-condition:
      parent: rule-process
      prefix: "win."
      note: A rule that ends the match once a score or position is reached.
  attributes:
    launch-cost:
      on: [action]
      type: int
```

| Key | Meaning |
|---|---|
| `ruleset` | The ruleset's id. Lowercase letters, digits and hyphens, starting with a letter. |
| `schemaVersion` | The schema version; 1. Each note's `schema` must equal it. |
| `sources` | The registered sources. Each entry has `key` (the source key), `note` (the id of its source note, which must exist) and optionally `supersedes` (the key of a registered source it replaces, never its own). Keys are unique. The note must be a source note whose `key` is the entry's `key` and whose `supersedes` is the entry's `supersedes`, or both have none. `ingest` adds an entry as text after the last source, so comments and layout elsewhere in the file are kept. |
| `scope` | For each source key, the locator prefixes that are in scope for coverage. An empty list, or no entry, puts the whole source in scope. A prefix covers the span with that locator and spans beneath it, so `4` covers `4`, `4.1` and `4/a`, and does not cover `40`. Every key must be registered. This is not the note-level `scope` attribute, which lists variants. |
| `extensions` | Additions to the fixed schema; see below. |

### Extensions

A ruleset may add to schema v1 and may not remove or redefine anything in it. There are four kinds. Each is a mapping under `extensions`; a missing or empty kind is fine.

**Node types** (`nodeTypes`). Each entry has a `parent` (a fixed node type), a `prefix` and optionally a `note`. The example above declares `win-condition`, checked as a `rule-process`.

**Edge types** (`edgeTypes`). Each entry has a `parent` (a fixed edge type), an `inverse`, and optionally `domain`, `range`, `note`, `acyclic` and `symmetric`. A domain or range left out is the parent's. The flags are not inherited; leave them out and they are false. For example:

```yaml
extensions:
  edgeTypes:
    gates:
      parent: requires
      inverse: gated-by
      domain: [action]
      range: [zone]
      note: The action may be taken only while the target zone is available.
```

**Attributes** (`attributes`). Each entry has a `type` (`text`, `int`, `bool`, `enum` or `list`), optionally `on` (node types the attribute applies to; absent means any), and for an enum its `values`. A declared attribute is never required, and a declared list holds text only. For example:

```yaml
extensions:
  attributes:
    table-talk:
      on: [clause]
      type: enum
      values: [allowed, forbidden]
```

**Enums** (`enums`). Each entry names a fixed enum attribute and lists values to append to it, such as `modality: [advice]`. Only enum attributes whose values are listed in the schema can be extended; `type`, `status` and `relation-type` take their values from the schema's own lists and cannot.

Rules an extension must obey (`ruleset-invalid` reports each breach):

- A node type, edge type or attribute name is lowercase letters, digits and hyphens, starting with a letter.
- A node type does not redefine a fixed type, names a fixed type as its parent, and has a prefix of lowercase letters, digits and hyphens ending in a dot that no other type uses.
- An edge type names a fixed edge type as its parent. Its name and its inverse are new: not an attribute name, not a fixed edge name or inverse, and not used twice among the declarations. Its domain and range name known node types, fixed or declared.
- An attribute name does not reuse a fixed attribute name, an edge name or an inverse name. Its `on` lists known node types, and an enum has at least one value.
- An unknown key anywhere in the file is refused, and values that YAML would read as numbers must be quoted where text is required.

A declared type checks as its parent: it takes the parent's requirements and may use the edges and attributes of the parent. If the declarations cannot be merged, `lint` reports `ruleset-invalid` and skips every check that needs the merged schema.

## Source notes, editions and errata

A source note, `sources/src.<key>.md`, pins one published document: the key spans cite, a label, the SHA-256 and size of the original file, how its text was extracted, and where its span file lives. `sabot ingest` writes it. It has the type `source` and the status `structural`.

File `sources/src.dw1.md`:

```markdown
---
id: src.dw1
type: source
title: Driftwood Rules, first edition
status: structural
confidence: high
rev: 1
schema: 1
kind: edition
key: dw1
label: Driftwood Rules, first edition
edition: first
effective: "2026-01-15"
retrieved: "2026-11-19"
bytes: 6429
sha256: "7ba7ad9dd331e626340320923851ebebc3d998dffa0cafc0c6e43fb719be558f"
extractor: text
segmenter: numbered
spans: "spans/dw1.jsonl"
---

# Driftwood Rules, first edition

The first published text of the Driftwood rules, kept as plain text with numbered rules in nine sections.
```

The `kind` says what the document is:

- `edition` is a full text of the rules. A later edition **supersedes** an earlier one: register it with `ingest --supersedes <old-key>`, which sets `supersedes` on the new source note and in `_ruleset.yaml`. From then on `lint` reports every note that still cites the older key (`source-superseded`).
- `errata` corrects an edition. Register it as its own source with `ingest --kind errata --amends <key>`; the new note records `amends`. Notes cite its spans under its own key, so the correction and the text it corrects stay separate. `amends` is recorded on the note only; `_ruleset.yaml` has no field for it, and `lint` does not read it.
- `ruling` and `faq` are published decisions and answers kept as sources in the same way. They are not the `ruling` node type, which is a note about one decision.

Every source has its own span file, written by `segment` after `ingest`. A registered source whose span file is absent makes `coverage` refuse. `coverage` leaves out a source that another registered source supersedes, unless it is named with `--source`. The original file stays outside the vault; `ingest --verify` compares a file with the registered hash.

## Lint classes

`sabot lint` prints one finding per line: severity, class, note id (or the file path when there is no note), message. Findings are sorted by path, class and message. The exit code is 1 when any finding is an error and 0 otherwise. Under `--strict`, the five classes marked "strict" in the table are raised from warnings to errors; every other class has one severity. `sabot lint --list-classes` prints this table.

| Class | Severity | What it refuses |
|---|---|---|
| `frontmatter-invalid` | error | A note whose front matter is missing, not closed, not YAML, not a mapping with text keys, names a key more than once, or whose file is not UTF-8. The note is not loaded. |
| `required-missing` | error | A required attribute that is absent, null, empty text or an empty list. |
| `id-malformed` | error | An `id` that is not text or does not match the id pattern. |
| `id-file-mismatch` | error | An `id` that differs from the file name without `.md`. |
| `id-duplicate` | error | Two notes with one id. The later one in path order is reported and not loaded. |
| `id-prefix-mismatch` | error | An `id` that does not start with the prefix of the note's type. |
| `id-reserved-name` | error | An `id`, or a note file name, whose first dot-separated part is a Windows device name. |
| `type-unknown` | error | A `type` that is neither a fixed node type nor declared. |
| `enum-invalid` | error | A value of the wrong kind or outside the allowed values: text, `YYYY-MM-DD` date, whole number (with its minimum), true or false, an enum value, or a list element of the wrong kind. |
| `attribute-unknown` | error | A key that is not an attribute, edge name or inverse name, or an attribute on a node type it is not allowed on. |
| `status-requirement` | error | A status without what it requires: `mapped` needs `source`, `refused` needs `source` and `refusal` and no `implemented-by` edge, `derived` needs `rationale`. |
| `type-requirement` | error | A node type without what it requires: the attributes and edges in the table of type requirements. |
| `span-unknown` | error | A `source` entry that is not a span reference, or names a span or sentence its span file does not hold, or a source with no readable span file. |
| `span-subtree-misused` | error | A `/*` citation on a note whose status is not `refused` or `structural`. |
| `link-malformed` | error | An edge value that is not a quoted wikilink. |
| `link-dangling` | error | An edge that names a note that does not exist. |
| `edge-domain` | error | An edge that leaves a note of a type outside its domain. |
| `edge-range` | error | An edge that reaches a note of a type outside its range. |
| `inverse-authored` | error | An inverse edge name written in front matter. |
| `symmetric-duplicated` | error | A symmetric edge written on both ends. |
| `precedes-cycle` | error | A cycle among the edges of an acyclic edge type (`precedes` and any declared acyclic edge). |
| `conflict-without-question` | error | A `conflicts-with` pair that no open question records by citing a span of each clause. |
| `restates-conflicts-pair` | error | One pair of clauses joined by both `restates` and `conflicts-with`. |
| `superseded-without-successor` | error | A `superseded` note with no `refines` or `resolved-by` edge. |
| `schema-version-mismatch` | error | A note whose `schema` differs from the ruleset's `schemaVersion`. |
| `ruleset-invalid` | error | A `_ruleset.yaml` that cannot be read, names a key twice, has unknown keys, has a wrong schema version, an unregistered or missing source, a source whose note is not a source note or has another `key` or another `supersedes`, or an extension that breaks the rules above. |
| `transaction-incomplete` | error | A journal directory (`.sabot-txn-*`) or a staged file (`.sabot-new-*`) left at the vault by a write that was killed. `sabot recover` rolls it back. |
| `quotation-long` | error | A quoted run of 60 or more characters in a body: paraphrase it. |
| `overrides-and-exception` | warning | One pair of notes joined by both `overrides` and `exception-to`. |
| `body-link` | warning | A wikilink in a body, which is not an edge. |
| `pending` | strict | A note whose status is `pending`. |
| `stale` | strict | A note with `stale: true`; the message gives `stale-reason`. |
| `question-open` | strict | An open question with no `resolved-by` edge. |
| `orphan` | strict | A note that authors no edge and that no other note names. Source notes are exempt. |
| `source-superseded` | strict | A note that cites a source which a registered source supersedes. |

`lint` checks the notes, the span files they cite and `_ruleset.yaml`. It never reads the extracted source text; `overlap` is the tool that does, for copied wording.

## From the graph to a conformance clause list

`sabot compile` writes `_index/graph.json`: every note as a node with its type, path, attributes and compiled paraphrase, every authored edge, every derived inverse edge, the ruleset's extensions and a digest of the vault: the notes, `_ruleset.yaml` and the `*.jsonl` files directly in `spans/`, so a hidden or stray file there does not change it. It refuses a vault with lint errors. `compile --check` exits 1 when the written graph is out of date.

No command in this repository writes a conformance clause list or a classification manifest yet. This section states how the note attributes are meant to map onto them, as the consumer repositories of the engine carry them, so that a generator and an author agree. Check it against the generator when one exists.

**Clause list.** A row stands for one note whose status is `mapped`, `derived` or `refused`. Notes that are `structural` or `superseded` have no row, and a note that is still `pending` blocks the list: it is not ready to be mapped. An author writes only the span references and the paraphrase; every other field is computed in the same way for every game.

| Row field | Taken from |
|---|---|
| `clauseId` | The note's `clause-id` when it has one, so an existing id survives; otherwise the note id. |
| `source` | The `label` of the source note the note cites. A derived note cites nothing; consumer repositories mark such rows with the source `derived`. |
| `section` | The locators of the note's cited spans in document order, joined by `, `. |
| `page` | The `page` of the first cited span in its span file, present whenever the source has pages. |
| `paraphrase` | The compiled paraphrase of the note body. |
| `status` | `MAPPED` for a mapped or derived note, `REFUSED` for a refused one. |
| `fixtures` | The `fixtures` of the `test-obligation` notes that point at the note through `tested-by`; always present, an empty list for a refused row. |
| `tests` | The `tests` of those obligations; always present, an empty list when none names a test. |
| `refusalReason` | The note's `refusal`; present exactly when the row is `REFUSED`. |

**Surface classification.** The classification manifest has one entry per element of the game definition, keyed `<kind>:<name>` such as `command:launch-boat`. A `construct` note supplies the element: `surface` is its element id and `construct` lists the engine construct kinds it stands for. The entry's `classification` is `MAPPED` with the `fixtures` of the construct's test obligations, or `REFUSED` with a `reason`. A deliberately unsupported part of the rules is a `scope:<name>` entry, always `REFUSED`: the `scope-key` of a refused note names the entry the note accounts for and its `refusal` is the reason.

File `build/constructs/cst.launch-boat-command.md`, a construct that stands for the element `command:launch-boat`:

```markdown
---
id: cst.launch-boat-command
type: construct
title: Launch command
status: derived
confidence: high
rev: 1
schema: 1
construct:
  - "command"
surface: "command:launch-boat"
rationale: A build-stage artefact standing for the launch action.
---

# Launch command

The engine command that performs a launch.
```

File `build/obligations/to.launch-boat.md`, a test obligation that names the fixture and the test that discharge it. A test obligation needs another note to name it with `tested-by`, so lint accepts it once the action that it checks points at it:

```markdown
---
id: to.launch-boat
type: test-obligation
title: Launch limit check
status: derived
confidence: high
rev: 1
schema: 1
rationale: A generated requirement from the launch action.
fixtures:
  - "launch-one-boat"
tests:
  - "LaunchLimitTests"
---

# Launch limit check

Checks that a second launch in one turn is refused.
```

File `model/actions/act.launch-boat.md`, the action that points at both build notes. It also uses the declared attribute `launch-cost`, which this vault's `_ruleset.yaml` declares:

```markdown
---
id: act.launch-boat
type: action
title: Launch a boat
status: mapped
confidence: high
rev: 1
schema: 1
launch-cost: 1
tags:
  - "launch"
source:
  - "dw1#5.1"
  - "dw1#5.1a"
occurs-in:
  - "[[ph.sail]]"
requires:
  - "[[zone.hand]]"
triggers:
  - "[[tp.boat-launched]]"
implemented-by:
  - "[[cst.launch-boat-command]]"
tested-by:
  - "[[to.launch-boat]]"
---

# Launch a boat

The mover pays a card, then puts a boat from hand into the harbour. Only one launch is allowed per turn.
```

## Stale notes and the edition update loop

A note is **stale** when a new edition changed a span it cites. `migrate-edition` marks it by adding `stale: true` and a `stale-reason`:

| `stale-reason` | A cited span in the new edition is |
|---|---|
| `editorial` | Changed only in spacing, punctuation, hyphenation or pronoun gender. |
| `substantive` | Changed in wording or meaning. |
| `removed` | Gone, or no longer under the root a subtree citation named. |

A note takes the most severe reason among its citations. The mark is a request to a person: it never clears itself, and `lint --strict` reports every stale note as an error until someone removes it. Editorial and clean notes are repointed to the new edition. A note with a substantive or removed citation keeps all its citations on the old edition until it is reviewed.

The loop, in six steps, with `<old>` and `<new>` the source keys:

1. **Register the new edition.** `sabot ingest <file> --key <new> --supersedes <old> ...` records it and makes `lint` report every note still citing `<old>`.
2. **Segment it.** `sabot segment <text> --source <new> --mode ...` writes `spans/<new>.jsonl`.
3. **Read the differences.** `sabot diff-editions <old> <new> --old-text <file> --new-text <file>` classifies each span as unchanged, editorial, substantive, added, removed or moved, and lists the notes to re-review.
4. **Migrate the citations.** `sabot migrate-edition <old> <new> --dry-run`, then again without `--dry-run`. Clean and editorial citations move to `<new>`; affected notes are marked stale.
5. **Review each stale note.** Read the new span, correct the paraphrase if the meaning changed, point the citations at `<new>`, raise `rev`, and delete the `stale` and `stale-reason` lines. Write notes for added spans.
6. **Check the result.** `sabot lint --strict` must be clean, `sabot coverage` must show every in-scope span cited (it leaves out `<old>`, which `<new>` supersedes; `--source <old>` measures it anyway), and `sabot compile` writes the new graph.

## Language limits

The tools that read source text are written for English. The hash normalisers apply an English pronoun table to build the editorial hash. The sentence splitter ends a sentence at `.`, `!` or `?` followed by whitespace and then a capital letter, digit or opening quote, and knows only a short list of English abbreviations; prose that starts sentences in lower case, or uses other abbreviations, is split wrongly and the span file records the wrong splits. Full-text search reads a word as a run of ASCII letters and digits with no stemming, so text in other scripts is not found by it. Segmenting a source in another language needs a reviewed boundaries file and has no support beyond that.
