"""Opt-in live smoke for the OpenAI Responses streaming adapter.

Requires OPENAI_API_KEY; the output excludes prompts, credentials, and answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections.abc import Sequence

from kogwistar.agent.openai_responses import OpenAIResponsesProvider
from kogwistar.agent.streaming import ProviderRequest


async def _run(model: str) -> dict[str, object]:
    trace_id = uuid.uuid4().hex
    started = time.perf_counter()
    provider = OpenAIResponsesProvider()
    kinds: list[str] = []
    response_id: str | None = None
    structured_output: object = None
    failure_code: str | None = None
    async for event in provider.stream(
        ProviderRequest(
            model=model,
            instructions="Return the exact requested object, without tools.",
            input="Return {\"status\":\"ok\",\"value\":7}.",
            response_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}, "value": {"type": "integer"}},
                "required": ["status", "value"],
                "additionalProperties": False,
            },
            max_output_tokens=128,
        )
    ):
        kinds.append(event.kind)
        response_id = event.response_id or response_id
        if event.kind == "response_completed":
            structured_output = event.structured_output
        elif event.kind == "response_failed":
            failure_code = event.error_code
    return {
        "provider_id": provider.provider_id,
        "model": model,
        "supported_event_types": sorted(set(kinds)),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "structured_output_valid": structured_output == {"status": "ok", "value": 7},
        "failure_code": failure_code,
        "trace_id": trace_id,
        "provider_response_id": response_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args.model))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["structured_output_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
