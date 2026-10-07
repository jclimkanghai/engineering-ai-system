from evals.runner import (
    evaluate_case,
    load_cases,
    score_human_rubric,
    summarize_human_scores,
    summarize_results,
)


def _case(**overrides):
    case = {
        "case_id": "ev-grounded-01",
        "category": "evidence_grounding",
        "complexity": "simple",
        "mode": "truth",
        "prompt": "Assess the requirement using supplied evidence.",
        "request": {
            "project_id": "SYNTHETIC-EVAL-PROJECT",
            "document_ids": ["SYN-DOC-1"],
            "requirements": [
                {"requirement_id": "SYN-REQ-1", "text": "Synthetic requirement."}
            ],
            "evidence": [
                {
                    "evidence_id": "SYN-E-1",
                    "document_id": "SYN-DOC-1",
                    "revision": "A",
                    "locator": "section=2",
                    "text": "Synthetic evidence supports the requirement.",
                }
            ],
        },
        "expectations": {
            "minimum_findings": 1,
            "require_evidence_for_each_finding": True,
        },
    }
    case.update(overrides)
    return case


def _finding(**overrides):
    finding = {
        "finding_id": "F-1",
        "title": "Supported observation",
        "finding": "The synthetic evidence supports the requirement.",
        "status": "confirmed",
        "finding_class": "tender_observation",
        "qualification": "observation",
        "qualification_rationale": "The supplied evidence supports a bounded observation only.",
        "qualification_basis": {
            "source_authority": "established",
            "requirement_type": "design_guidance",
            "same_object": "not_applicable",
            "same_object_basis": None,
            "chronology": "current",
            "project_stage": "detailed_design",
            "materiality": "immaterial",
            "counter_evidence_ids": [],
            "unknown_reason": None,
        },
        "source_evidence_ids": ["SYN-E-1"],
        "requirement_ids": ["SYN-REQ-1"],
        "risk_level": "low",
        "risk_dimensions": [],
        "human_review_required": False,
        "confidence": "high",
        "assumptions": [],
        "uncertainties": [],
        "conflicts": [],
    }
    finding.update(overrides)
    return finding


def test_case_corpus_has_fifty_cases_and_multiple_complexity_strata():
    cases = load_cases()

    assert len(cases) == 50
    assert len({case["case_id"] for case in cases}) == 50
    assert {case["complexity"] for case in cases} == {"simple", "medium", "complex"}


def test_valid_grounded_response_passes_deterministic_gates():
    result = evaluate_case(_case(), {"findings": [_finding()]})

    assert result["passed"] is True
    assert result["failed_gates"] == []


def test_missing_response_fails_instead_of_defaulting_to_empty():
    result = evaluate_case(_case(), {"findings": []}, response_present=False)

    assert result["gates"]["response_present"] is False
    assert result["passed"] is False


def test_response_model_is_retained_in_evaluation_result():
    result = evaluate_case(
        _case(), {"findings": [_finding()], "model": "synthetic-model-v1"}
    )

    assert result["response_model"] == "synthetic-model-v1"


def test_present_empty_response_is_distinct_from_missing_response():
    case = _case(expectations={"minimum_findings": 0})
    result = evaluate_case(case, {"findings": []})

    assert result["gates"]["response_present"] is True
    assert result["response_model"] is None


def test_unknown_evidence_id_fails_traceability_gate():
    response = {"findings": [_finding(source_evidence_ids=["INVENTED-EVIDENCE"])]}

    result = evaluate_case(_case(), response)

    assert result["passed"] is False
    assert "evidence_ids_known" in result["failed_gates"]


def test_no_evidence_case_rejects_confirmed_claim_and_requires_uncertainty():
    case = _case(
        request={
            "project_id": "SYNTHETIC-EVAL-PROJECT",
            "document_ids": [],
            "requirements": [],
            "evidence": [],
        },
        expectations={
            "minimum_findings": 1,
            "require_evidence_for_each_finding": False,
            "require_abstention_without_evidence": True,
        },
    )
    response = {
        "findings": [
            _finding(
                source_evidence_ids=[],
                requirement_ids=[],
                status="confirmed",
                uncertainties=[],
            )
        ]
    }

    result = evaluate_case(case, response)

    assert result["passed"] is False
    assert {"abstains_without_evidence", "uncertainty_explicit"}.issubset(
        result["failed_gates"]
    )


def test_safety_sensitive_case_requires_human_review():
    case = _case(
        expectations={
            "minimum_findings": 1,
            "require_evidence_for_each_finding": True,
            "human_review_required": True,
        }
    )
    result = evaluate_case(case, {"findings": [_finding(human_review_required=False)]})

    assert result["passed"] is False
    assert "human_review_gate" in result["failed_gates"]


def test_alignment_case_checks_independent_verdict_not_just_finding_structure():
    case = _case(
        expectations={
            "minimum_findings": 1,
            "alignment_checks": {"plan_alignment": "misaligned"},
        }
    )
    aligned = _finding(
        finding_id="plan_alignment", risk_dimensions=["alignment:aligned"]
    )
    incorrect = evaluate_case(case, {"findings": [aligned]})
    assert "alignment_verdicts" in incorrect["failed_gates"]

    misaligned = _finding(
        finding_id="plan_alignment", risk_dimensions=["alignment:misaligned"]
    )
    correct = evaluate_case(case, {"findings": [misaligned]})
    assert correct["gates"]["alignment_verdicts"] is True


def test_lesson_case_requires_expected_status_and_current_citations():
    case = _case(expectations={"lesson_statuses": {"ORG-1": "conflict"}})
    review = {
        "lesson_id": "ORG-1",
        "status": "conflict",
        "rationale": "The lesson conflicts with the current requirement.",
        "requirement_ids": ["SYN-REQ-1"],
        "evidence_ids": ["SYN-E-1"],
    }
    conflict_finding = _finding(
        conflicts=["ORG-1 conflicts with the current requirement."],
        human_review_required=True,
    )
    valid = evaluate_case(
        case, {"findings": [conflict_finding], "lesson_reviews": [review]}
    )
    assert valid["passed"] is True

    uncited = evaluate_case(
        case,
        {
            "findings": [conflict_finding],
            "lesson_reviews": [
                {
                    **review,
                    "requirement_ids": [],
                }
            ],
        },
    )
    assert "lesson_citations" in uncited["failed_gates"]
    omitted = evaluate_case(case, {"findings": [conflict_finding]})
    assert "lesson_verdicts" in omitted["failed_gates"]
    hidden = evaluate_case(case, {"findings": [], "lesson_reviews": [review]})
    assert "lesson_conflict_finding" in hidden["failed_gates"]


def test_summary_reports_gate_failures_by_complexity():
    results = [
        {"case_id": "A", "complexity": "simple", "passed": True, "failed_gates": []},
        {
            "case_id": "B",
            "complexity": "complex",
            "passed": False,
            "failed_gates": ["human_review_gate"],
        },
    ]

    summary = summarize_results(results)

    assert summary["passed"] is False
    assert summary["pass_rate"] == 0.5
    assert summary["by_complexity"]["complex"]["failed"] == 1


def test_human_rubric_requires_minimum_total_and_critical_dimensions():
    scores = {
        "source_traceability": 2,
        "evidence_classification": 2,
        "revision_control": 1,
        "uncertainty": 2,
        "steelman_quality": 1,
        "completeness": 2,
        "professional_boundary": 1,
        "response_fit": 1,
    }

    result = score_human_rubric(scores)

    assert result["total"] == 12
    assert result["passed"] is True
    assert summarize_human_scores({"CASE-1": scores})["reviewed_cases"] == 1


def test_human_rubric_fails_if_a_critical_dimension_is_zero():
    scores = {
        "source_traceability": 2,
        "evidence_classification": 0,
        "revision_control": 2,
        "uncertainty": 2,
        "steelman_quality": 2,
        "completeness": 2,
        "professional_boundary": 2,
        "response_fit": 2,
    }

    result = score_human_rubric(scores)

    assert result["total"] >= 12
    assert result["passed"] is False
