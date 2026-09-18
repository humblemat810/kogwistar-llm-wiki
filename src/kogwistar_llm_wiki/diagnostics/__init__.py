"""Diagnostics, debug-run traces, and timing summaries."""

from .debug_helpers import (
    LiveTracePrinter,
    ParseStatisticsRecord,
    ParseStatisticsStore,
    aggregate_stage_timings,
    append_jsonl,
    build_parse_statistics_record,
    configure_debug_logging,
    dump_json,
    env_flag_enabled,
    format_live_trace,
    now_ms,
    summarize_semantic_tree,
    summarize_stage_timings,
)

__all__ = [
    "LiveTracePrinter",
    "ParseStatisticsRecord",
    "ParseStatisticsStore",
    "aggregate_stage_timings",
    "append_jsonl",
    "build_parse_statistics_record",
    "configure_debug_logging",
    "dump_json",
    "env_flag_enabled",
    "format_live_trace",
    "now_ms",
    "summarize_semantic_tree",
    "summarize_stage_timings",
]
