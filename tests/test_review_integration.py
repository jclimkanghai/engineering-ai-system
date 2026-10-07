from pipelines.review.context import ReviewContext
from pipelines.review.integration import engineering_qa
from pipelines.review.models import ReviewRequest
from tests.support.review_harness import (
    RecordingAdapter,
    make_review_fixture,
    response_for_request,
    stage_data,
    valid_recording_adapter,
)


def test_engineering_qa_blocks_missing_provenance():
    context = ReviewContext(ReviewRequest(document_ids=["DOC-1"]))
    context.put(
        "requirements",
        [
            {
                "identity": {"requirement_id": "REQ-1", "source_text": "shall test"},
                "provenance": {},
            }
        ],
    )

    result = engineering_qa(context)

    assert result["qa"]["status"] == "BLOCKED"
    assert result["human_review_ready"] is False


def test_engineering_qa_blocks_unresolved_output_links():
    context = ReviewContext(ReviewRequest(document_ids=["DOC-1"]))
    context.put("requirements", [])
    context.put(
        "controlled_outputs",
        {
            "review_comments": [
                {
                    "output_id": "OUT-1",
                    "finding_id": "F-1",
                    "source_evidence_ids": ["E-MISSING"],
                }
            ],
        },
    )
    context.put(
        "audit_trail",
        [
            {
                "finding_id": "F-1",
                "unresolved_links": ["evidence:E-MISSING"],
            }
        ],
    )

    result = engineering_qa(context)

    assert result["qa"]["status"] == "BLOCKED"
    assert any("unknown evidence" in item.lower() for item in result["qa"]["blockers"])


def test_requirement_source_text_is_indexed_from_identity(tmp_path):
    from pipelines.retrieval.integration import build_engine_from_context

    context = ReviewContext(
        ReviewRequest(
            document_ids=["DOC-1"],
            metadata={"evidence_store_path": str(tmp_path / "evidence.sqlite3")},
        )
    )
    context.put(
        "requirements",
        [
            {
                "identity": {
                    "requirement_id": "REQ-1",
                    "source_text": "The pump shall be tested.",
                },
                "provenance": {"source_document_id": "DOC-1", "source_location": "§4"},
            }
        ],
    )
    engine = build_engine_from_context(context)

    assert engine.index.get("REQ-1") is not None
    assert engine.index.get("REQ-1").text == "The pump shall be tested."
    assert engine.persistent_store.count() == 1


def test_compliance_stage_passes_retrieved_evidence_to_adapter(tmp_path):
    adapter = valid_recording_adapter()

    result = make_review_fixture(tmp_path, adapter)

    request = adapter.requests[0]
    assert request.mode == "compliance"
    assert request.evidence[0]["document_id"] in {
        "SYN-DOC-SPEC-001",
        "SYN-DOC-TEST-001",
    }
    assert result.findings[0]["source_evidence_ids"] == [
        request.evidence[0]["evidence_id"]
    ]
    stage = stage_data(result, "compliance_analysis")
    assert stage["coverage"][0]["status"] == "evidence_retrieved"
    assert stage["response_metadata"][0] == {
        "model": "synthetic-test-adapter",
        "response_id": "SYN-RESP-001",
        "usage": {},
    }


def test_compliance_stage_reports_no_evidence_without_calling_adapter(tmp_path):
    adapter = valid_recording_adapter()

    result = make_review_fixture(tmp_path, adapter, include_evidence=False)

    assert adapter.requests == []
    assert result.findings == []
    assert (
        stage_data(result, "compliance_analysis")["coverage"][0]["status"]
        == "insufficient_evidence"
    )


def test_compliance_stage_rejects_unknown_evidence_and_requirement_ids(tmp_path):
    def invented_response(request):
        response = response_for_request(request)
        response.findings[0]["source_evidence_ids"] = ["SYN-EVIDENCE-INVENTED"]
        response.findings[0]["requirement_ids"] = ["SYN-REQ-INVENTED"]
        return response

    result = make_review_fixture(tmp_path, RecordingAdapter(invented_response))

    data = stage_data(result, "compliance_analysis")
    assert result.findings == []
    assert any(
        "SYN-EVIDENCE-INVENTED" in message for message in data["validation_errors"]
    )
    assert any("SYN-REQ-INVENTED" in message for message in data["validation_errors"])


def test_compliance_stage_rejects_findings_without_requirement_links(tmp_path):
    def unlinked_response(request):
        response = response_for_request(request)
        response.findings[0]["requirement_ids"] = []
        return response

    result = make_review_fixture(tmp_path, RecordingAdapter(unlinked_response))

    data = stage_data(result, "compliance_analysis")
    assert result.findings == []
    assert any(
        "must cite requirement" in message for message in data["validation_errors"]
    )


def test_confirmed_noncompliance_is_not_projected_as_compliant(tmp_path):
    def response_says_test_not_performed(request):
        response = response_for_request(request)
        response.findings[0]["finding"] = (
            "The synthetic test record confirms the required test was not performed."
        )
        return response

    result = make_review_fixture(
        tmp_path,
        RecordingAdapter(response_says_test_not_performed),
    )

    compliance_outputs = stage_data(result, "output_projection")["controlled_outputs"][
        "compliance_matrix"
    ]
    assert compliance_outputs[0]["data"]["compliance_status"] == "pending_information"


def test_duplicate_finding_ids_across_requirement_calls_block_qa(tmp_path):
    result = make_review_fixture(
        tmp_path,
        valid_recording_adapter(),
        requirement_text=(
            "Supplier shall perform a hydrostatic test on the synthetic pressure line.\n"
            "Supplier shall document the result in the synthetic test record."
        ),
    )

    data = stage_data(result, "compliance_analysis")
    assert any("Duplicate finding ID" in error for error in data["validation_errors"])
    assert stage_data(result, "engineering_qa")["qa"]["status"] == "BLOCKED"


def test_competitor_mode_supplies_evidence_to_compliance_stage(tmp_path):
    result = make_review_fixture(
        tmp_path,
        valid_recording_adapter(),
        review_mode="competitor",
    )

    compliance = stage_data(result, "compliance_analysis")
    assert compliance["analysis_status"] == "COMPLETE"
    assert "evidence_engine" not in next(
        stage.errors
        for stage in result.stages
        if stage.stage_id == "compliance_analysis"
    )


def test_truth_mode_retrieves_evidence_and_returns_structured_assessments(tmp_path):
    adapter = valid_recording_adapter()

    result = make_review_fixture(tmp_path, adapter, review_mode="truth")

    request = adapter.requests[0]
    assert request.mode == "truth"
    assert "Required Truth Mode Output" in request.instructions
    assert request.evidence[0]["document_id"] in {
        "SYN-DOC-SPEC-001",
        "SYN-DOC-TEST-001",
    }
    truth = stage_data(result, "truth_check")
    assert truth["analysis_status"] == "COMPLETE"
    assert (
        truth["truth_assessments"][0]["requirement_id"]
        == request.requirements[0]["identity"]["requirement_id"]
    )
    assert truth["truth_assessments"][0]["source_evidence_ids"] == [
        request.evidence[0]["evidence_id"]
    ]
    assert (
        truth["truth_assessments"][0]["fact"][0]["text"] == request.evidence[0]["text"]
    )
    assert result.findings[0]["finding_id"].startswith("truth:")


def test_truth_mode_without_adapter_reports_unassessed(tmp_path):
    result = make_review_fixture(tmp_path, None, review_mode="truth")

    truth = stage_data(result, "truth_check")
    assert truth["analysis_status"] == "NOT_CONNECTED"
    assert truth["truth_assessments"][0]["status"] == "unassessed"
    assert result.findings == []


def test_truth_mode_rejects_unknown_evidence_citation(tmp_path):
    def invented_response(request):
        response = response_for_request(request)
        response.findings[0]["source_evidence_ids"] = ["SYN-UNKNOWN-EVIDENCE"]
        return response

    result = make_review_fixture(
        tmp_path, RecordingAdapter(invented_response), review_mode="truth"
    )

    truth = stage_data(result, "truth_check")
    assert truth["analysis_status"] == "PARTIAL"
    assert truth["truth_assessments"] == []
    assert any("SYN-UNKNOWN-EVIDENCE" in error for error in truth["validation_errors"])
    assert stage_data(result, "engineering_qa")["qa"]["status"] == "BLOCKED"


def test_truth_mode_keeps_model_claim_and_judgement_separate_from_source_facts(
    tmp_path,
):
    def interpretive_response(request):
        response = response_for_request(request)
        response.findings[0]["finding"] = "The pressure line may be unsuitable."
        response.findings[0]["status"] = "unverified"
        response.findings[0]["impact"] = "Potential programme delay."
        return response

    result = make_review_fixture(
        tmp_path, RecordingAdapter(interpretive_response), review_mode="truth"
    )
    assessment = stage_data(result, "truth_check")["truth_assessments"][0]

    assert assessment["fact"][0]["text"]
    assert assessment["model_assessment"] == "The pressure line may be unsuitable."
    assert assessment["engineering_judgement"] is None
    assert assessment["impact"] == "Potential programme delay."


def test_truth_mode_allows_same_provider_id_for_separate_requirements(
    tmp_path, monkeypatch
):
    import pipelines.review.production as production

    extract = production.requirement_extraction

    def extract_two(context):
        result = extract(context)
        second = {
            **result["requirements"][0],
            "identity": {
                **result["requirements"][0]["identity"],
                "requirement_id": "SYN-REQ-SECOND",
                "source_text": "Supplier shall document the hydrostatic test result.",
            },
        }
        return {**result, "requirements": [*result["requirements"], second]}

    monkeypatch.setattr(production, "requirement_extraction", extract_two)
    result = make_review_fixture(
        tmp_path,
        valid_recording_adapter(),
        review_mode="truth",
    )

    truth = stage_data(result, "truth_check")
    assert len(truth["truth_assessments"]) == 2
    ids = [item["finding_id"] for item in truth["truth_assessments"]]
    assert len(set(ids)) == 2
    assert not any(
        "Duplicate truth finding ID" in error for error in truth["validation_errors"]
    )


def test_truth_mode_empty_model_response_is_unassessed(tmp_path):
    def empty_response(request):
        response = response_for_request(request)
        return type(response)(
            model=response.model,
            response_id=response.response_id,
            findings=[],
            usage=response.usage,
        )

    result = make_review_fixture(
        tmp_path, RecordingAdapter(empty_response), review_mode="truth"
    )

    truth = stage_data(result, "truth_check")
    assert truth["analysis_status"] == "PARTIAL"
    assert truth["truth_assessments"][0]["status"] == "unassessed"
    assert any("no findings" in error.lower() for error in truth["validation_errors"])


def test_allin_preserves_compliance_findings_when_conclusion_is_unconnected(tmp_path):
    adapter = valid_recording_adapter()
    result = make_review_fixture(tmp_path, adapter, review_mode="allin")

    conclusion = stage_data(result, "engineering_conclusion")
    outputs = stage_data(result, "output_projection")["controlled_outputs"]
    assert result.findings[0]["finding_id"] == "SYN-F-001"
    assert len(result.findings) == 2
    compliance = stage_data(result, "compliance_analysis")
    assert [item["finding_id"] for item in compliance["findings"]] == ["SYN-F-001"]
    assert conclusion["analysis_status"] == "NOT_CONNECTED"
    assert "findings" not in conclusion
    assert len({finding["finding_id"] for finding in result.findings}) == 2
    assert any(
        record["finding_id"] == "SYN-F-001"
        for records in outputs.values()
        for record in records
    )


def test_engineering_qa_keeps_unknown_responsibility_as_review_item():
    context = ReviewContext(ReviewRequest(document_ids=["SYN-DOC-001"]))
    context.put("requirements", [])
    context.put(
        "responsibility_assignments",
        [
            {
                "assignment_id": "SYN-ASGN-001",
                "party_id": None,
                "human_review_required": True,
            }
        ],
    )

    result = engineering_qa(context)

    assert result["qa"]["status"] == "PASS"
    assert result["qa"]["blockers"] == []
    assert result["review_items"][0]["assignment_id"] == "SYN-ASGN-001"


def test_engineering_qa_blocks_compliance_validation_errors():
    context = ReviewContext(ReviewRequest(document_ids=["SYN-DOC-001"]))
    context.put("requirements", [])
    context.put("finding_validation_errors", ["Unknown evidence ID: SYN-E-BAD"])

    result = engineering_qa(context)

    assert result["qa"]["status"] == "BLOCKED"
    assert any("SYN-E-BAD" in item for item in result["qa"]["blockers"])
