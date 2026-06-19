from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from .ingest_pipeline import IngestPipeline
from .models import IngestPipelineArtifacts, IngestPipelineRequest


OperationMode = Literal["parse_first", "maintenance_first", "hybrid"]


@dataclass(frozen=True, slots=True)
class OperationModeComparison:
    operation_mode: OperationMode
    artifacts: IngestPipelineArtifacts
    initial_latency_ms: int
    accepted_patch_count: int
    fallback_rate: float
    graph_quality: str
    total_model_cost: float


def compare_ingest_operation_modes(
    pipeline: IngestPipeline,
    request: IngestPipelineRequest,
    *,
    modes: tuple[OperationMode, ...] = ("parse_first", "maintenance_first", "hybrid"),
) -> list[OperationModeComparison]:
    """Run one document-shaped request through operation modes and collect metrics."""

    comparisons: list[OperationModeComparison] = []
    base_workspace = request.workspace_id
    for mode in modes:
        mode_request = request.model_copy(
            update={
                "workspace_id": f"{base_workspace}-{mode}",
                "operation_mode": mode,
            }
        )
        started = time.perf_counter()
        artifacts = pipeline.run(mode_request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        comparisons.append(
            OperationModeComparison(
                operation_mode=mode,
                artifacts=artifacts,
                initial_latency_ms=elapsed_ms,
                accepted_patch_count=0,
                fallback_rate=0.0,
                graph_quality=artifacts.graph_status,
                total_model_cost=0.0,
            )
        )
    return comparisons
