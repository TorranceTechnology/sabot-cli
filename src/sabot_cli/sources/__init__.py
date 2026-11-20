"""Turn source documents into span records: locations and hashes, never text.

`diff_editions`, `overlap` and the migration planner are further modules beside the ones here;
each reads `SpanIndex` and shares `_normalise`.
"""

from sabot_cli.sources._diff import EditionDiff, MovedSpan, PairedSpan, diff_editions
from sabot_cli.sources._extract import DEFAULT_EXTRACTOR, EXTRACTORS, extract_text
from sabot_cli.sources._files import (
    check_key,
    read_edition_ranges,
    read_edition_spans,
    read_text_file,
)
from sabot_cli.sources._migrate import (
    NoteMigration,
    StaleReason,
    plan_note_migration,
    rewrite_note_text,
    stale_reason_to_write,
)
from sabot_cli.sources._overlap import NoteOverlap, OverlapReport, overlap
from sabot_cli.sources._records import SpanIndex, SpanRecord, read_spans
from sabot_cli.sources._segment import SegmentResult, segment
from sabot_cli.sources._self_checks import CheckResult
from sabot_cli.sources._spanref import SpanRef, SpanRefError, parse_span_ref
from sabot_cli.sources._types import (
    SOURCE_KEY_PATTERN,
    ExtractedText,
    Numbering,
    SegmentMode,
    SourceError,
    SourceFormat,
)

__all__ = [
    "DEFAULT_EXTRACTOR",
    "EXTRACTORS",
    "SOURCE_KEY_PATTERN",
    "CheckResult",
    "EditionDiff",
    "ExtractedText",
    "MovedSpan",
    "NoteMigration",
    "NoteOverlap",
    "Numbering",
    "OverlapReport",
    "PairedSpan",
    "SegmentMode",
    "SegmentResult",
    "SourceError",
    "SourceFormat",
    "SpanIndex",
    "SpanRecord",
    "SpanRef",
    "SpanRefError",
    "StaleReason",
    "check_key",
    "diff_editions",
    "extract_text",
    "overlap",
    "parse_span_ref",
    "plan_note_migration",
    "read_edition_ranges",
    "read_edition_spans",
    "read_spans",
    "read_text_file",
    "rewrite_note_text",
    "segment",
    "stale_reason_to_write",
]
