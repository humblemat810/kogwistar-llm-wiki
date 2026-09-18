"""Compatibility facade for long-run parser execution."""

from kg_doc_parser.workflow_ingest.layerwise_llm import build_layerwise_llm_callbacks

from .parsing.layered_workflow import (
    _build_provider_layer_callbacks,  # noqa: F401 - compatibility export
    _summarize_budget_events,
)
from .parsing.layered_workflow import (
    run_workflow_layered_parse as _run_workflow_layered_parse,
)
from .parsing.longrun_child import run_longrun_parser_child as _run_longrun_parser_child
from .parsing.parse_quality import (
    basic_sense_eval_from_graph_payload as _basic_sense_eval_from_graph_payload,
)


def run_workflow_layered_parse(**kwargs: object):
    """Run the layered parser while preserving root-level patch seams."""
    return _run_workflow_layered_parse(
        **kwargs,
        build_callbacks=build_layerwise_llm_callbacks,
        basic_sense_eval=_basic_sense_eval_from_graph_payload,
    )


def run_longrun_parser_child(payload: dict[str, object]) -> None:
    """Run the child process while preserving root-level test seams."""
    return _run_longrun_parser_child(
        payload,
        workflow_parser=run_workflow_layered_parse,
        summarize_budget_events=_summarize_budget_events,
    )


__all__ = [
    "run_longrun_parser_child",
    "run_workflow_layered_parse",
]
