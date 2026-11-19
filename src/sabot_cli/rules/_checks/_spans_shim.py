"""Span types and readers used by the checks, re-exported from `sabot_cli.sources`."""

from sabot_cli.sources import SourceError as SpanFileError
from sabot_cli.sources import SpanIndex, SpanRecord, SpanRef, parse_span_ref, read_spans

__all__ = ["SpanFileError", "SpanIndex", "SpanRecord", "SpanRef", "parse_span_ref", "read_spans"]
