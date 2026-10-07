"""Single host-side translation from review requests to the Reviewer contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from engineering_ai_reviewer import ReviewerUnavailable
from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import AnalysisRequest
from pipelines.llm.stage import AnalysisAdapter


class ReviewerAnalysisAdapter:
    """Translate and validate provider responses for the normalized Reviewer API."""

    def __init__(self, adapter: AnalysisAdapter) -> None:
        self.adapter = adapter

    @property
    def supports_concurrent_calls(self) -> bool:
        return getattr(self.adapter, "supports_concurrent_calls", False) is True

    @property
    def deterministic_test_adapter(self) -> bool:
        return getattr(self.adapter, "deterministic_test_adapter", False) is True

    def analyse(self, request: dict[str, Any]) -> dict[str, Any]:
        evidence = [EvidenceRef(**item) for item in request["evidence"]]
        requirements = request["requirements"]
        review_context = deepcopy(request["context"])
        review_context["candidate_findings"] = deepcopy(
            request.get("candidate_findings", [])
        )
        analysis_request = AnalysisRequest(
            mode=request["mode"],
            project_id=request["project_id"],
            document_ids=deepcopy(request.get("document_ids", [])),
            requirements=deepcopy(requirements),
            evidence=[asdict(item) for item in evidence],
            context=review_context,
            instructions=request.get("instructions"),
        )
        try:
            response = self.adapter.analyse(analysis_request)
        except Exception as exc:
            raise ReviewerUnavailable(
                "AI alignment reviewer is unavailable; retry the retained review"
            ) from exc
        if not isinstance(response.findings, list) or len(response.findings) > 100:
            raise ValueError(
                "Alignment finding validation failed: invalid findings list"
            )
        for item in response.findings:
            if not isinstance(item, dict):
                raise ValueError("Alignment finding validation failed: invalid finding")
            for key in (
                "source_evidence_ids",
                "requirement_ids",
                "risk_dimensions",
                "assumptions",
                "uncertainties",
                "conflicts",
            ):
                values = item.get(key, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    raise ValueError(
                        "Alignment finding validation failed: invalid citation list"
                    )
        findings, errors, _warnings = findings_from_response(
            response, evidence, {item["node_id"] for item in requirements}
        )
        if errors:
            raise ValueError(
                "Alignment finding validation failed: " + "; ".join(errors)
            )
        return {
            "findings": [finding.to_dict() for finding in findings],
            "model": response.model,
            "response_id": response.response_id,
            "usage": deepcopy(response.usage),
            "finding_disproofs": deepcopy(response.finding_disproofs),
        }
