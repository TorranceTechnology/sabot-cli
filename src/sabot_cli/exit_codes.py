"""Process exit codes shared by every command."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Meaning of each exit status a command may return."""

    OK = 0
    FINDINGS = 1
    USAGE = 2
    REFUSED = 3
