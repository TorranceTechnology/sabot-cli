"""The two checks that read a note's prose rather than its front matter."""

import re
from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.vault import find_wikilinks

QUOTATION_LIMIT = 60
_QUOTED = re.compile(r'"([^"]*)"|\u201c([^\u201d]*)\u201d')
_BLANK_LINE = re.compile(r"\n\s*\n")


def _check_quotation_long(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for paragraph in _BLANK_LINE.split(note.body):
            # A quote is looked for within one paragraph so a stray mark cannot pair with a
            # distant one and invent a long run.
            joined = " ".join(paragraph.split())
            for match in _QUOTED.finditer(joined):
                length = len(match.group(1) or match.group(2) or "")
                if length >= QUOTATION_LIMIT:
                    yield ctx.report(
                        note, f"the body quotes a run of {length} characters; paraphrase it"
                    )


def _check_body_link(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for link in find_wikilinks(note.body):
            yield ctx.report(note, f"the body holds {link}, which is not an edge")
