from __future__ import annotations

from typing import Any, Protocol

from pipelines.findings import EvidenceRef

from .adapter import OpenAIAnalysisAdapter, findings_from_response
from .models import AnalysisRequest, LLMResponse


class AnalysisAdapter(Protocol):
    """Provider-neutral interface for one structured analysis request."""

    def analyse(self, request: AnalysisRequest) -> LLMResponse: ...


def run_llm_analysis(
    *,
    mode: str,
    evidence: list[EvidenceRef],
    requirements: list[dict[str, Any]] | None = None,
    document_ids: list[str] | None = None,
    project_id: str | None = None,
    context: dict[str, Any] | None = None,
    adapter: AnalysisAdapter | None = None,
) -> dict[str, Any]:
    """Run one bounded LLM analysis pass and return pipeline-safe artifacts."""
    adapter = adapter or OpenAIAnalysisAdapter()
    request = AnalysisRequest(
        mode=mode,
        project_id=project_id,
        document_ids=document_ids or [],
        requirements=requirements or [],
        evidence=[e.__dict__ for e in evidence],
        context=context or {},
    )
    response = adapter.analyse(request)
    findings, errors, warnings = findings_from_response(response, evidence)
    return {
        "mode": mode,
        "model": response.model,
        "response_id": response.response_id,
        "findings": [f.to_dict() for f in findings],
        "finding_validation_errors": errors,
        "finding_validation_warnings": warnings,
        "usage": response.usage,
        "human_review_required": any(f.human_review_required for f in findings),
    }
