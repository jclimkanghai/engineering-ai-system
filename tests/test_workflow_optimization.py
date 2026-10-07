import json
from pathlib import Path

from pipelines.llm.models import LLMResponse
from pipelines.findings import EvidenceRef
from pipelines.review.analysis import _analysis_cache_key
from pipelines.llm.models import AnalysisRequest
from pipelines.retrieval.chunker import chunk_markdown
from pipelines.retrieval.store import PersistentEvidenceStore
from pipelines.review.analysis import run_evidence_analysis
from pipelines.review.context import ReviewContext
from pipelines.review.integration import (
    consolidate_findings,
    engineering_qa,
    triage_requirements,
)
from pipelines.review.models import ReviewRequest, StageStatus
from pipelines.review.orchestrator import ReviewPipeline
from pipelines.review.stages import FunctionStage


def test_analysis_cache_key_changes_when_qualification_prompt_changes(monkeypatch):
    import pipelines.review.analysis as analysis

    request = AnalysisRequest(mode="allin", evidence=[])
    first = _analysis_cache_key(request, [EvidenceRef("E1", excerpt="source")])
    monkeypatch.setattr(
        analysis,
        "SYSTEM_INSTRUCTIONS",
        analysis.SYSTEM_INSTRUCTIONS + "\nChanged qualification instruction.",
    )
    second = _analysis_cache_key(request, [EvidenceRef("E1", excerpt="source")])

    assert first != second


def test_page_markers_are_preserved_in_chunks_and_store(tmp_path: Path):
    chunks = chunk_markdown(
        document_id="DOC-1",
        markdown="# Scope\n[PAGE: 4]\nThe pump shall be tested.\n[PAGE:5]\nThe valve must close.",
    )

    assert [chunk.page for chunk in chunks] == [4, 5]
    assert chunks[0].locator.startswith("page=4;")
    assert chunks[1].locator.startswith("page=5;")

    store = PersistentEvidenceStore(tmp_path / "evidence.sqlite3")
    store.upsert_many(chunks)
    assert store.get(chunks[0].evidence_id).page == 4
    store.close()


def test_revision_branch_is_skipped_without_comparison_documents():
    calls = []
    pipeline = ReviewPipeline(
        [
            FunctionStage("document_control", lambda _context: {"ready": True}),
            FunctionStage(
                "revision_change",
                lambda _context: calls.append(True) or {"changes": []},
                ("ready",),
            ),
        ]
    )

    result = pipeline.run(
        ReviewRequest(document_ids=["DOC-1"], require_human_gate=False)
    )

    assert result.final_status == StageStatus.COMPLETE
    assert result.stages[1].status == StageStatus.SKIPPED
    assert calls == []


def test_review_result_uses_the_latest_consolidated_findings_once():
    pipeline = ReviewPipeline(
        [
            FunctionStage(
                "analysis", lambda _context: {"findings": [{"finding_id": "F-1"}]}
            ),
            FunctionStage(
                "consolidation", lambda _context: {"findings": [{"finding_id": "F-1"}]}
            ),
        ]
    )

    result = pipeline.run(
        ReviewRequest(document_ids=["DOC-1"], require_human_gate=False)
    )

    assert [item["finding_id"] for item in result.findings] == ["F-1"]


def test_unknown_responsibility_is_a_warning_not_a_qa_blocker():
    context = ReviewContext(ReviewRequest(document_ids=["DOC-1"]))
    context.put("requirements", [])
    context.put("analysis_status", "COMPLETE")
    context.put(
        "responsibility_assignments",
        [
            {
                "assignment_id": "RA-1",
                "human_review_required": True,
                "party_id": None,
            }
        ],
    )

    result = engineering_qa(context)

    assert result["qa"]["status"] == "PASS"
    assert any(
        "unknown party" in warning.lower() for warning in result["qa"]["warnings"]
    )


def test_invalid_citation_is_repaired_once_for_the_affected_batch():
    requirement_id = "REQ-1"
    valid_finding = {
        "finding_id": "F-1",
        "title": "Test finding",
        "finding": "The requirement needs review.",
        "status": "potential",
        "finding_class": "verification_item",
        "qualification": "verification_required",
        "qualification_rationale": "The requirement evidence needs further review.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "current_project_requirement",
            "same_object": "matched",
            "same_object_basis": "Same document requirement and evidence.",
            "chronology": "current",
            "project_stage": "detailed_design",
            "materiality": "material",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": [requirement_id],
        "requirement_ids": [requirement_id],
        "risk_level": "medium",
        "risk_dimensions": [],
        "human_review_required": True,
        "confidence": "medium",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
    }

    class RepairAdapter:
        def __init__(self):
            self.requests = []

        def analyse(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(
                    "test",
                    "r1",
                    [{**valid_finding, "source_evidence_ids": ["E-MISSING"]}],
                )
            return LLMResponse("test", "r2", [valid_finding])

    context = ReviewContext(
        ReviewRequest(
            document_ids=["DOC-1"],
            require_human_gate=False,
            metadata={"analysis_batch_size": 1},
        )
    )
    context.put(
        "requirements",
        [
            {
                "identity": {
                    "requirement_id": requirement_id,
                    "source_text": "The pump shall be tested.",
                },
                "provenance": {"source_document_id": "DOC-1"},
            }
        ],
    )
    result = run_evidence_analysis(context, RepairAdapter(), mode="truth")

    assert result["analysis_status"] == "COMPLETE"
    assert result["analysis_retries"] == 1
    assert result["analysis_errors"] == []
    assert len(result["findings"]) == 1


def test_analysis_cache_reuses_a_completed_batch(tmp_path: Path):
    requirement_id = "REQ-CACHE"

    class CountingAdapter:
        def __init__(self):
            self.calls = 0

        def analyse(self, _request):
            self.calls += 1
            return LLMResponse("test", f"r{self.calls}", [])

    def context():
        value = ReviewContext(
            ReviewRequest(
                document_ids=["DOC-1"],
                require_human_gate=False,
                metadata={"analysis_cache_path": str(tmp_path / "analysis-cache.json")},
            )
        )
        value.put(
            "requirements",
            [
                {
                    "identity": {
                        "requirement_id": requirement_id,
                        "source_text": "The pump shall be tested.",
                    },
                    "provenance": {"source_document_id": "DOC-1"},
                }
            ],
        )
        return value

    adapter = CountingAdapter()
    first = run_evidence_analysis(context(), adapter, mode="truth")
    second = run_evidence_analysis(context(), adapter, mode="truth")

    assert first["analysis_status"] == "COMPLETE"
    assert second["analysis_status"] == "COMPLETE"
    assert second["analysis_cache_hits"] == 1
    assert second["analysis_calls"] == 0
    assert adapter.calls == 1


def test_run_manifest_records_stage_checkpoints(tmp_path: Path):
    manifest_path = tmp_path / "run.json"
    pipeline = ReviewPipeline(
        [
            FunctionStage("document_control", lambda _context: {"ready": True}),
        ]
    )

    result = pipeline.run(
        ReviewRequest(
            document_ids=["DOC-1"],
            require_human_gate=False,
            metadata={"run_manifest_path": str(manifest_path)},
        )
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.final_status == StageStatus.COMPLETE
    assert manifest["status"] == "COMPLETE"
    assert manifest["stages"][0]["stage_id"] == "document_control"
    assert manifest["stages"][0]["status"] == "COMPLETE"


def test_requirement_triage_preserves_registry_and_reduces_duplicate_queue():
    context = ReviewContext(ReviewRequest(document_ids=["DOC-1"], review_mode="allin"))
    context.put(
        "requirements",
        [
            {
                "identity": {
                    "requirement_id": "REQ-1",
                    "source_text": "The pump shall be tested.",
                },
                "classification": {
                    "requirement_type": "mandatory",
                    "requirement_category": "testing",
                },
            },
            {
                "identity": {
                    "requirement_id": "REQ-2",
                    "source_text": "  The pump SHALL be tested.  ",
                },
                "classification": {
                    "requirement_type": "mandatory",
                    "requirement_category": "testing",
                },
            },
            {
                "identity": {
                    "requirement_id": "REQ-3",
                    "source_text": "For information only.",
                },
                "classification": {
                    "requirement_type": "informational",
                    "requirement_category": "other",
                },
            },
        ],
    )

    result = triage_requirements(context, "allin")

    assert result["requirement_triage"]["input_count"] == 3
    assert result["requirement_triage"]["cluster_count"] == 2
    assert result["requirement_triage"]["deduplicated_count"] == 1
    assert len(result["analysis_requirements"]) == 1
    assert "compliance" in result["analysis_plan"]["lenses"]
    duplicate_cluster = next(
        cluster
        for cluster in result["requirement_clusters"].values()
        if len(cluster["member_requirement_ids"]) == 2
    )
    assert set(duplicate_cluster["member_requirement_ids"]) == {"REQ-1", "REQ-2"}


def test_finding_consolidation_unions_traceability_without_merging_different_text():
    context = ReviewContext(ReviewRequest(document_ids=["DOC-1"]))
    context.put(
        "findings",
        [
            {
                "finding_id": "F-1",
                "title": "Missing test",
                "finding": "The test record is absent.",
                "required_action": "Provide the record.",
                "source_evidence_ids": ["E-1"],
                "requirement_ids": ["REQ-1"],
                "risk_level": "medium",
                "confidence": "medium",
                "human_review_required": True,
                "metadata": {},
            },
            {
                "finding_id": "F-2",
                "title": "Missing test",
                "finding": "The test record is absent.",
                "required_action": "Provide the record.",
                "source_evidence_ids": ["E-2"],
                "requirement_ids": ["REQ-2"],
                "risk_level": "high",
                "confidence": "high",
                "human_review_required": False,
                "metadata": {},
            },
            {
                "finding_id": "F-3",
                "title": "Different issue",
                "finding": "The calculation is unclear.",
                "required_action": "Clarify the calculation.",
                "source_evidence_ids": ["E-3"],
                "requirement_ids": ["REQ-3"],
                "risk_level": "low",
                "confidence": "low",
                "human_review_required": True,
                "metadata": {},
            },
        ],
    )

    result = consolidate_findings(context)

    assert result["finding_merge"]["input_count"] == 3
    assert result["finding_merge"]["output_count"] == 2
    merged = next(item for item in result["findings"] if item["finding_id"] == "F-1")
    assert merged["source_evidence_ids"] == ["E-1", "E-2"]
    assert merged["requirement_ids"] == ["REQ-1", "REQ-2"]
    assert merged["risk_level"] == "high"
    assert merged["human_review_required"] is True
