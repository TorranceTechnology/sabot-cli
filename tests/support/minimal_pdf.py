"""A standard-library writer for small text-only PDFs.

Tests build their PDF fixture at run time with this module, so no binary file is committed.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

_LINES_PER_PAGE = 50
_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN = 50
_FONT_SIZE = 12
_LEADING = 14


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: Sequence[str]) -> bytes:
    top = _PAGE_HEIGHT - _MARGIN
    commands = [f"BT /F1 {_FONT_SIZE} Tf {_LEADING} TL {_MARGIN} {top} Td"]
    commands += [f"({_escape(line)}) Tj T*" for line in lines]
    commands.append("ET")
    return "\n".join(commands).encode("latin-1")


def _object(number: int, body: bytes) -> bytes:
    return f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"


def write_pdf(path: Path, lines: Sequence[str]) -> None:
    """Write `lines` as Helvetica text, one line per row, paginated at 50 rows.

    Characters outside Latin-1 are refused because the font is a standard one without an
    embedded encoding. The output depends only on `lines`.
    """
    for line in lines:
        line.encode("latin-1")
    pages = [list(lines[i : i + _LINES_PER_PAGE]) for i in range(0, len(lines), _LINES_PER_PAGE)]
    pages = pages or [[]]

    first_page_object = 4
    page_objects = [first_page_object + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_objects)
    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for number, page_lines in zip(page_objects, pages, strict=True):
        bodies.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {number + 1} 0 R >>"
            ).encode("ascii")
        )
        stream = _content_stream(page_lines)
        bodies.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(output))
        output += _object(number, body)
    xref_position = len(output)
    output += f"xref\n0 {len(bodies) + 1}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(bytes(output))
