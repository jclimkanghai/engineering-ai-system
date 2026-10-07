from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pipelines.llm.models import AnalysisRequest, LLMResponse
from pipelines.registry.document_manifest import DocumentRecord, DocumentRegistry
from pipelines.review.models import ReviewRequest, ReviewResult
from pipelines.review.production import build_production_pipeline


class RecordingAdapter:
    def __init__(self, responder: Callable[[AnalysisRequest], LLMResponse]) -> None:
        self.responder = responder
        self.requests: list[AnalysisRequest] = []

    def analyse(self, request: AnalysisRequest) -> LLMResponse:
        self.requests.append(request)
        return self.responder(request)


def response_for_request(request: AnalysisRequest) -> LLMResponse:
    if not request.evidence or not request.requirements:
        findings = []
    else:
        first_requirement = request.requirements[0]
        requirement_id = first_requirement.get("identity", {}).get("requirement_id")
        if not requirement_id:
            requirement_id = first_requirement["requirement_id"]
        evidence = request.evidence[0]
        findings = [
            {
                "finding_id": "SYN-F-001",
                "title": "Synthetic test evidence located",
                "finding": "The synthetic test record reports a completed hydrostatic test.",
                "status": "confirmed",
                "finding_class": "verification_item",
                "qualification": "verification_required",
                "qualification_rationale": "The test record requires engineering verification.",
                "qualification_basis": {
                    "source_authority": "established",
                    "requirement_type": "current_project_requirement",
                    "same_object": "matched",
                    "same_object_basis": "The cited line and test record are the same scope.",
                    "chronology": "current",
                    "project_stage": "detailed_design",
                    "materiality": "material",
                    "counter_evidence_ids": [evidence["evidence_id"]],
                    "unknown_reason": None,
                },
                "source_evidence_ids": [evidence["evidence_id"]],
                "requirement_ids": [requirement_id],
                "interpretation": "The retrieved synthetic test record addresses the test requirement.",
                "steelman": None,
                "critic": None,
                "gap": None,
                "impact": None,
                "risk_level": "low",
                "risk_dimensions": [],
                "recommendation": None,
                "human_review_required": False,
                "human_review_reason": None,
                "confidence": "medium",
                "assumptions": [],
                "uncertainties": [],
                "conflicts": [],
            }
        ]
    return LLMResponse(
        model="synthetic-test-adapter", response_id="SYN-RESP-001", findings=findings
    )


def valid_recording_adapter() -> RecordingAdapter:
    return RecordingAdapter(response_for_request)


def make_review_fixture(
    tmp_path: Path,
    adapter: RecordingAdapter | None,
    *,
    approval=None,
    require_human_gate: bool = True,
    include_evidence: bool = True,
    review_mode: str = "compliance",
    requirement_text: str | None = None,
) -> ReviewResult:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec_path = tmp_path / "synthetic_spec.md"
    requirement_fixture = (
        Path(__file__).parents[1] / "fixtures" / "e2e" / "synthetic_requirement.md"
    )
    spec_text = requirement_fixture.read_text(encoding="utf-8")
    if requirement_text is not None:
        spec_text = "# Synthetic Requirement\n\n" + requirement_text.strip() + "\n"
    spec_path.write_text(spec_text, encoding="utf-8")
    evidence_path = tmp_path / "synthetic_test_record.md"
    evidence_path.write_text(
        "# Synthetic Inspection Record\n\n"
        "Hydrostatic result: PASS; pressure line: SYN-LINE-001.\n",
        encoding="utf-8",
    )

    registry_path = tmp_path / "registry.json"
    registry = DocumentRegistry(registry_path)
    registry.register(
        DocumentRecord(
            document_id="SYN-DOC-SPEC-001",
            document_number="SYN-SPEC-001",
            title="Synthetic Test Specification",
            document_type="specification",
            discipline="process",
            revision="A",
            issue_status="issued",
            governing_status="governing",
            source_file="SYN-SPEC-001.md",
            source_hash="a" * 64,
            metadata={"output_files": {"markdown": str(spec_path)}},
        )
    )
    document_ids = ["SYN-DOC-SPEC-001"]
    if include_evidence:
        registry.register(
            DocumentRecord(
                document_id="SYN-DOC-TEST-001",
                document_number="SYN-TEST-001",
                title="Synthetic Hydrostatic Test Record",
                document_type="test_record",
                discipline="process",
                revision="A",
                issue_status="issued",
                governing_status="supporting",
                source_file="SYN-TEST-001.md",
                source_hash="b" * 64,
                metadata={"output_files": {"markdown": str(evidence_path)}},
            )
        )
        document_ids.append("SYN-DOC-TEST-001")

    request = ReviewRequest(
        document_ids=document_ids,
        review_mode=review_mode,
        require_human_gate=require_human_gate,
        approval=approval,
        metadata={
            "registry_path": str(registry_path),
            "evidence_store_path": str(tmp_path / "evidence.sqlite3"),
        },
    )
    return build_production_pipeline(review_mode, analysis_adapter=adapter).run(request)


def stage_data(result: ReviewResult, stage_id: str) -> dict:
    return next(stage.data for stage in result.stages if stage.stage_id == stage_id)
