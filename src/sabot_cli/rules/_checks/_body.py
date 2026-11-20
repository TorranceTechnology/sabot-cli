"""The two checks that read a note's prose rather than its front matter."""

import re
from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.vault import find_wikilinks

QUOTATION_LIMIT = 60
_QUOTED = re.compile(r'"([^"]*)"|\u201c([^\u201d]*)\u201d|\u2018([^\u2019]*)\u2019')
_BLANK_LINE = re.compile(r"\n\s*\n")
_BLOCKQUOTE_LINE = re.compile(r"^\s{0,3}>\s?")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def _check_quotation_long(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for paragraph in _BLANK_LINE.split(note.body):
            lines = paragraph.split("\n")
            quoted = [line for line in lines if _BLOCKQUOTE_LINE.match(line)]
            others = [line for line in lines if not _BLOCKQUOTE_LINE.match(line)]
            lengths = []
            if quoted:
                # A block quote is one quotation however it is wrapped.
                block = " ".join(_BLOCKQUOTE_LINE.sub("", line).strip() for line in quoted)
                lengths.append(len(" ".join(block.split())))
            # A quote is looked for within one paragraph so a stray mark cannot pair with a
            # distant one and invent a long run.
            joined = " ".join(" ".join(others).split())
            lengths += [
                len(next(g for g in m.groups() if g is not None)) for m in _QUOTED.finditer(joined)
            ]
            for length in lengths:
                if length >= QUOTATION_LIMIT:
                    yield ctx.report(
                        note, f"the body quotes a run of {length} characters; paraphrase it"
                    )


def _without_code(body: str) -> str:
    """The body with fenced code blocks and inline code spans removed.

    A link written inside code is an example of the syntax, not a link. A fence that is never
    closed runs to the end of the body, as it does when the note is rendered.
    """
    kept: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        opening = _FENCE.match(line)
        if fence is None:
            if opening is not None:
                fence = opening.group(1)[0] * len(opening.group(1))
            else:
                kept.append(_INLINE_CODE.sub("", line))
        elif opening is not None and line.strip().startswith(fence):
            fence = None
    return "\n".join(kept)


def _check_body_link(ctx: LintContext) -> Iterator[Report]:
    for note in ctx.notes:
        for link in find_wikilinks(_without_code(note.body)):
            yield ctx.report(note, f"the body holds {link}, which is not an edge")
