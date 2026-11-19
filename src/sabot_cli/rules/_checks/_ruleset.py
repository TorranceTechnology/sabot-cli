"""The check on `_ruleset.yaml`'s declarations."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.rules.ruleset import RULESET_FILE
from sabot_cli.rules.schema import ruleset_problems


def _check_ruleset_invalid(ctx: LintContext) -> Iterator[Report]:
    for problem in ruleset_problems(ctx.fixed_schema, ctx.ruleset, ctx.vault.notes.keys()):
        yield Report(RULESET_FILE, None, problem)
