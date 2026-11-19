"""Every byte a command writes to stdout goes through this module.

Keeping one writer is what makes output byte-identical across runs and operating
systems: UTF-8, LF line endings and, for JSON, sorted keys and a fixed indent.
"""

import json
import sys
from collections.abc import Mapping, Sequence

from sabot_cli.exit_codes import ExitCode

FORMAT_VERSION = 1

type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None


def canonical_json(document: Mapping[str, JsonValue]) -> str:
    """Serialise a document to its canonical text, with a trailing newline."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render an aligned plain-text table; row order is the caller's."""
    table = [list(headers), *(list(row) for row in rows)]
    widths = [max(len(row[column]) for row in table) for column in range(len(headers))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in table
    ]
    return "\n".join(lines) + "\n"


def error_document(command: str, code: str, message: str) -> dict[str, JsonValue]:
    """Build the object an agent parses when a command refuses its input."""
    return {
        "command": command,
        "format": FORMAT_VERSION,
        "error": {"code": code, "message": message},
    }


def write_text(text: str) -> None:
    """Write text to stdout as UTF-8 with LF endings, whatever the platform default is."""
    sys.stdout.flush()
    # The binary layer bypasses the text layer's newline translation (CRLF on Windows)
    # and locale encoding; a replaced stdout without one falls back to the text layer.
    binary = getattr(sys.stdout, "buffer", None)
    if binary is None:
        sys.stdout.write(text)
        return
    binary.write(text.encode("utf-8"))
    binary.flush()


def write_json(document: Mapping[str, JsonValue]) -> None:
    """Write one canonical JSON document to stdout."""
    write_text(canonical_json(document))


def write_diagnostic(message: str) -> None:
    """Write a human diagnostic to stderr, which carries no contract."""
    sys.stderr.write(message.rstrip("\n") + "\n")


def refuse(command: str, code: str, message: str, *, as_json: bool) -> int:
    """Report refused input in the requested format and return its exit code."""
    if as_json:
        write_json(error_document(command, code, message))
    else:
        write_diagnostic(f"sabot {command}: {code}: {message}")
    return int(ExitCode.REFUSED)
