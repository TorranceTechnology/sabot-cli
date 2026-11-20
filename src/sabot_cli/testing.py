"""The seams the tests use to reach inside the tool.

`sabot_cli` has no stable Python interface: the commands and their output are the interface.
This module is the one place the tests reach in through, so that no test imports a module whose
name starts with an underscore. Nothing here is for any other use, and it may change without
notice.
"""

from sabot_cli.vault._process import process_start
from sabot_cli.vault._walk import set_listing as set_directory_listing
from sabot_cli.vault._yaml import C_SAFE_STRING_LOADER, PureSafeStringLoader
from sabot_cli.vault.transaction import HardStop, checkpoint_hook

__all__ = [
    "C_SAFE_STRING_LOADER",
    "HardStop",
    "PureSafeStringLoader",
    "checkpoint_hook",
    "process_start",
    "set_directory_listing",
]
