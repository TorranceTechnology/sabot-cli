"""Facts about the file system a test runs on."""

import os
from pathlib import Path

import pytest

requires_posix_permissions = pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "needs POSIX permission bits: on Windows a read-only directory does not stop a write, "
        "and a read-only file is an attribute whose effect on a rename is not asserted here"
    ),
)

requires_device_file_names = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot create a file named for a device, which is what the lint class guards",
)


def require_case_insensitive(directory: Path) -> None:
    """Skip the test unless `directory` is on a file system that ignores letter case."""
    probe = directory / "CaseProbe"
    probe.write_bytes(b"")
    try:
        insensitive = (directory / "caseprobe").exists()
    finally:
        probe.unlink()
    if not insensitive:
        pytest.skip(
            "needs a file system that ignores letter case (the default on macOS and Windows); "
            "this one is case-sensitive"
        )
