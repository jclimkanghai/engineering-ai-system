from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import LLMResponse


def test_unknown_evidence_from_model_is_rejected():
    response = LLMResponse(
        model="test",
        response_id="r-1",
        findings=[
            {
                "finding_id": "F-1",
                "title": "Unsupported",
                "finding": "Claim",
                "status": "unverified",
                "finding_class": "evidence_limitation",
                "qualification": "unknown_insufficient_information",
                "qualification_rationale": "The cited evidence is outside the supplied source set.",
                "qualification_basis": {
                    "source_authority": "unresolved",
                    "requirement_type": None,
                    "same_object": "unknown",
                    "same_object_basis": None,
                    "chronology": "unknown",
                    "project_stage": "unknown",
                    "materiality": "unknown",
                    "counter_evidence_ids": [],
                    "unknown_reason": "not_in_review_package",
                },
                "source_evidence_ids": ["E-999"],
                "requirement_ids": [],
                "interpretation": None,
                "steelman": None,
                "critic": None,
                "gap": None,
                "impact": None,
                "risk_level": "unknown",
                "risk_dimensions": [],
                "recommendation": None,
                "human_review_required": True,
                "human_review_reason": "unknown evidence",
                "confidence": "low",
                "assumptions": [],
                "uncertainties": ["Evidence unavailable"],
                "conflicts": [],
            }
        ],
    )

    findings, errors, _warnings = findings_from_response(response, [EvidenceRef("E-1")])

    assert findings == []
    assert any("Unknown evidence_id" in error for error in errors)
