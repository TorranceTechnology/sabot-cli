"""Facts about the file system a test runs on."""

from pathlib import Path

import pytest


def require_case_insensitive(directory: Path) -> None:
    """Skip the test unless `directory` is on a file system that ignores letter case."""
    probe = directory / "CaseProbe"
    probe.write_bytes(b"")
    try:
        insensitive = (directory / "caseprobe").exists()
    finally:
        probe.unlink()
    if not insensitive:
        pytest.skip("the file system is case-sensitive")
