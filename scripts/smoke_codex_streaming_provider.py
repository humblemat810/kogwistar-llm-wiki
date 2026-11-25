"""Manual smoke for the locally signed-in Codex App Server streaming adapter.

Run from the repository root after signing in with the Codex CLI:
    python scripts/smoke_codex_streaming_provider.py --model <codex-model>

The report intentionally excludes prompts, credentials, and response contents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import Sequence

from kogwistar.agent.streaming import ProviderRequest

from kogwistar_llm_wiki.codex.streaming_provider import CodexAppServerProvider


async def _run(model: str) -> dict[str, object]:
    trace_id = uuid.uuid4().hex
    started = time.perf_counter()
    kinds: list[str] = []
    response_id: str | None = None
    completed = False
    failure_code: str | None = None
    failure_reason: str | None = None
    provider = CodexAppServerProvider()
    request = ProviderRequest(
        model=model,
        instructions="Return exactly the requested JSON object. Do not call tools.",
        input="Return {\"status\":\"ok\",\"value\":7}.",
        response_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}, "value": {"type": "integer"}},
            "required": ["status", "value"],
            "additionalProperties": False,
        },
        max_output_tokens=128,
    )
    structured_result: object = None
    async for event in provider.stream(request):
        kinds.append(event.kind)
        response_id = event.response_id or response_id
        if event.kind == "response_completed":
            completed = True
            structured_result = event.structured_output
        elif event.kind == "response_failed":
            failure_code = event.error_code
            failure_reason = event.message[:300]
    return {
        "provider_id": provider.provider_id,
        "model": model,
        "supported_event_types": sorted(set(kinds)),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "structured_output_valid": completed and structured_result == {"status": "ok", "value": 7},
        "failure_code": failure_code,
        "failure_reason": failure_reason,
        "trace_id": trace_id,
        "provider_response_id": response_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Model name supported by the signed-in Codex CLI")
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args.model))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["structured_output_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
