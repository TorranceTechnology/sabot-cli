"""Turn source documents into span records: locations and hashes, never text.

`diff_editions`, `migrate_edition` and `overlap` are added as further modules beside the ones
here; each reads `SpanIndex` and shares `_normalise`, so none of them restructures this package.
"""

from sabot_cli.sources._extract import EXTRACTORS, extract_text
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
    "EXTRACTORS",
    "SOURCE_KEY_PATTERN",
    "CheckResult",
    "ExtractedText",
    "Numbering",
    "SegmentMode",
    "SegmentResult",
    "SourceError",
    "SourceFormat",
    "SpanIndex",
    "SpanRecord",
    "SpanRef",
    "SpanRefError",
    "extract_text",
    "parse_span_ref",
    "read_spans",
    "segment",
]
