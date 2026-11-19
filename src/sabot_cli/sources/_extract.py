"""Turn a source file into text: PDF through an external extractor, HTML and text in-process."""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from sabot_cli.sources._types import ExtractedText, SourceError, SourceFormat

# Names `--extractor` accepts for PDF input. A second entry here and a branch in
# `_extract_pdf` add an extractor without adding command surface.
EXTRACTORS: tuple[str, ...] = ("pdftotext",)

_VERSION = re.compile(r"pdftotext version (\S+)")
_BLOCK_TAGS = frozenset(
    {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer"}
    | {"h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section"}
    | {"table", "tr", "ul"}
)
_SKIPPED_TAGS = frozenset({"head", "script", "style"})


def extract_text(path: Path, fmt: SourceFormat) -> ExtractedText:
    """Extract a source's text; raise `SourceError` when it cannot be read or extracted."""
    if fmt == "pdf":
        return _extract_pdf(path)
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


def _extract_pdf(path: Path) -> ExtractedText:
    executable = shutil.which("pdftotext")
    if executable is None:
        raise SourceError("extractor-missing", "pdftotext is not installed or not on the PATH")
    if not path.is_file():
        raise SourceError("file-unreadable", "the file does not exist or is not a file")
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
