from __future__ import annotations

from kogwistar_llm_wiki.models import IngestPipelineRequest


def build_request(workspace_id: str = "demo") -> IngestPipelineRequest:
    return IngestPipelineRequest(
        workspace_id=workspace_id,
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text=(
            "Acme Contract\n\n"
            "Payment Terms\n"
            "Invoices are due within 30 days.\n\n"
            "Termination\n"
            "Either party may terminate with notice."
        ),
    )
