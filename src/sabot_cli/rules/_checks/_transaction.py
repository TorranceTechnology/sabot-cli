"""The check for a write that was started and not finished."""

from collections.abc import Iterator

from sabot_cli.rules._checks._context import LintContext
from sabot_cli.rules._checks._model import Report
from sabot_cli.vault.transaction import JOURNAL_PREFIX, incomplete_work


def _check_transaction_incomplete(ctx: LintContext) -> Iterator[Report]:
    for path in incomplete_work(ctx.vault.root):
        name = path.rsplit("/", 1)[-1]
        if name.startswith(JOURNAL_PREFIX):
            yield Report(path, None, "an earlier write was not finished; run sabot recover")
        else:
            yield Report(
                path, None, "a staged file was left by an unfinished write; run sabot recover"
            )
