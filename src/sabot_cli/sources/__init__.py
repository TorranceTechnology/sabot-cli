"""Turn source documents into span records: locations and hashes, never text.

`diff_editions`, `overlap` and the migration planner are further modules beside the ones here;
each reads `SpanIndex` and shares `_normalise`. This package knows nothing of rules or of the
command line: it does not import `rules`, `search`, `commands` or `output`.
"""

from sabot_cli.sources._diff import EditionDiff, diff_editions
from sabot_cli.sources._extract import DEFAULT_EXTRACTOR, EXTRACTORS, extract_text
from sabot_cli.sources._files import read_edition_ranges, read_span_file, read_text_file
from sabot_cli.sources._migrate import NoteMigration, StaleReason, plan_note_migration
from sabot_cli.sources._overlap import NoteOverlap, OverlapReport, overlap
from sabot_cli.sources._records import SpanIndex, SpanRecord
from sabot_cli.sources._scope import ancestry, in_scope
from sabot_cli.sources._segment import SegmentResult, segment
from sabot_cli.sources._self_checks import CheckResult
from sabot_cli.sources._spanref import SpanRef, SpanRefError, parse_span_ref, sentence_number
from sabot_cli.sources._types import (
    SPANS_DIRECTORY,
    ExtractedText,
    SourceError,
    SourceFormat,
    check_key,
    is_source_key,
    span_file_relative,
)

__all__ = [
    "DEFAULT_EXTRACTOR",
    "EXTRACTORS",
    "SPANS_DIRECTORY",
    "CheckResult",
    "EditionDiff",
    "ExtractedText",
    "NoteMigration",
    "NoteOverlap",
    "OverlapReport",
    "SegmentResult",
    "SourceError",
    "SourceFormat",
    "SpanIndex",
    "SpanRecord",
    "SpanRef",
    "SpanRefError",
    "StaleReason",
    "ancestry",
    "check_key",
    "diff_editions",
    "extract_text",
    "in_scope",
    "is_source_key",
    "overlap",
    "parse_span_ref",
    "plan_note_migration",
    "read_edition_ranges",
    "read_span_file",
    "read_text_file",
    "segment",
    "sentence_number",
    "span_file_relative",
]
