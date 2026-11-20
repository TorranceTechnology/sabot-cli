"""The checks on `_ruleset.yaml`: its declarations, and its agreement with the source notes."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.rules.ruleset import RULESET_FILE, RulesetSource
from sabot_cli.rules.schema import ruleset_problems


def _check_ruleset_invalid(ctx: LintContext) -> Iterator[Report]:
    for problem in ruleset_problems(ctx.fixed_schema, ctx.ruleset, ctx.vault.notes.keys()):
        yield Report(RULESET_FILE, None, problem)
    for source in ctx.ruleset.sources:
        disagreement = _disagreement(ctx, source)
        if disagreement is not None:
            yield Report(RULESET_FILE, None, disagreement)


def _disagreement(ctx: LintContext, source: RulesetSource) -> str | None:
    """How the source note a registration names contradicts it, or None when they agree.

    A note that does not exist is reported by `ruleset_problems`, so it is not repeated here.
    """
    note = ctx.vault.notes.get(source.note)
    if note is None:
        return None
    where = f"source {source.key!r} names the note {source.note!r}"
    if note.properties.get("type") != "source":
        return f"{where}, which is not a source note"
    key = note.properties.get("key")
    if key != source.key:
        return f"{where}, whose key is {key!r}"
    supersedes = note.properties.get("supersedes")
    if supersedes != source.supersedes:
        return (
            f"{where}, which supersedes {supersedes!r} while the ruleset says {source.supersedes!r}"
        )
    return None
