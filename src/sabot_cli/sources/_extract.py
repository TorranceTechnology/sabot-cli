"""Turn a source file into text: PDF through an external extractor, HTML and text in-process."""

import io
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pypdf

from sabot_cli.sources._types import ExtractedText, SourceError, SourceFormat

# Names `--extractor` accepts for PDF input, the default first. An entry here and a branch in
# `_extract_pdf` add an extractor without adding command surface.
EXTRACTORS: tuple[str, ...] = ("pypdf", "pdftotext")
DEFAULT_EXTRACTOR = EXTRACTORS[0]

_VERSION = re.compile(r"pdftotext version (\S+)")
_BLOCK_TAGS = frozenset(
    {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section"}
    | {"table", "tr", "ul"}
)
_SKIPPED_TAGS = frozenset({"head", "script", "style"})


def extract_text(
    path: Path, fmt: SourceFormat, extractor: str = DEFAULT_EXTRACTOR
) -> ExtractedText:
    """Extract a source's text; raise `SourceError` when it cannot be read or extracted.

    `extractor` names the PDF tool and is ignored for other formats.
    """
    if fmt == "pdf":
        return _extract_pdf(path, extractor)
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as error:
        raise SourceError(
            "file-unreadable", f"the file cannot be read: {error.strerror}"
        ) from error
    except UnicodeDecodeError as error:
        raise SourceError("text-undecodable", "the file is not valid UTF-8") from error
    if fmt == "html":
        return ExtractedText(_html_to_text(text), "html-stdlib")
    return ExtractedText(text, "text")


def _extract_pdf(path: Path, extractor: str) -> ExtractedText:
    if not path.is_file():
        raise SourceError("file-unreadable", "the file does not exist or is not a file")
    if extractor == "pdftotext":
        return _extract_with_pdftotext(path)
    return _extract_with_pypdf(path)


def _extract_with_pypdf(path: Path) -> ExtractedText:
    try:
        reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()))
        pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
    except OSError as error:
        raise SourceError(
            "file-unreadable", f"the file cannot be read: {error.strerror}"
        ) from error
    except Exception as error:
        # pypdf raises its own errors for damaged or encrypted files but also bare ValueError,
        # KeyError and TypeError from inside a malformed object; a narrower list would let some
        # damaged files escape as a traceback instead of a refusal.
        raise SourceError("extraction-failed", f"pypdf failed: {error}") from error
    # Each page ends with a newline and a form feed, the way `pdftotext -layout` writes it, so
    # page numbering in `segment` does not depend on the extractor.
    text = "".join(f"{page.rstrip()}\n\f" if page.strip() else "\f" for page in pages)
    return ExtractedText(text, f"pypdf {pypdf.__version__} layout")


def _extract_with_pdftotext(path: Path) -> ExtractedText:
    executable = shutil.which("pdftotext")
    if executable is None:
        raise SourceError("extractor-missing", "pdftotext is not installed or not on the PATH")
    command = [executable, "-layout", "-enc", "UTF-8", str(path.resolve()), "-"]
    done = subprocess.run(command, capture_output=True, check=False)
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise SourceError(
            "extraction-failed", f"pdftotext failed: {detail[0] if detail else 'no message'}"
        )
    try:
        text = done.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceError("extraction-failed", "pdftotext wrote text that is not UTF-8") from error
    return ExtractedText(text, f"pdftotext {_pdftotext_version(executable)} -layout")


def _pdftotext_version(executable: str) -> str:
    done = subprocess.run([executable, "-v"], capture_output=True, check=False)
    match = _VERSION.search((done.stderr + done.stdout).decode("utf-8", errors="replace"))
    return match.group(1) if match else "unknown"


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skipping:
            self._skipping -= 1
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(re.sub(r"\s+", " ", data))


def _html_to_text(markup: str) -> str:
    collector = _TextCollector()
    collector.feed(markup)
    collector.close()
    lines = [" ".join(line.split()) for line in "".join(collector.parts).split("\n")]
    tidy: list[str] = []
    for line in lines:
        if line or (tidy and tidy[-1]):
            tidy.append(line)
    while tidy and not tidy[-1]:
        tidy.pop()
    return "\n".join(tidy) + "\n" if tidy else ""
