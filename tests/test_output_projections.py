from pipelines.findings.models import Finding, FindingStatus, RiskLevel
from pipelines.outputs import project_all


def _finding(**kwargs):
    values = {
        "finding_id": "F-1",
        "title": "Calculation not demonstrated",
        "finding": "The calculation is not demonstrated by the submission.",
        "status": FindingStatus.UNVERIFIED,
        "source_evidence_ids": ["E-1"],
        "requirement_ids": ["REQ-1"],
        "impact": "Acceptance cannot be confirmed.",
        "risk_level": RiskLevel.MEDIUM,
        "required_action": "Submit the calculation.",
        "output_status": "open",
    }
    values.update(kwargs)
    return Finding(**values)


def test_projection_outputs_preserve_traceability():
    outputs = project_all(
        [_finding()], {"requirements": [{"identity": {"requirement_id": "REQ-1"}}]}
    )

    comment = outputs["review_comments"][0]
    assert comment.finding_id == "F-1"
    assert comment.source_evidence_ids == ["E-1"]
    assert comment.requirement_ids == ["REQ-1"]
    assert outputs["gap_matrix"][0].finding_id == "F-1"
    assert outputs["technical_queries"][0].finding_id == "F-1"


def test_missing_evidence_is_not_classified_as_non_compliant():
    finding = _finding(source_evidence_ids=[], status=FindingStatus.UNVERIFIED)
    outputs = project_all([finding], {"requirements": []})

    assert outputs["compliance_matrix"][0].status == "not_demonstrated"


def test_high_risk_finding_projects_to_risk_register():
    outputs = project_all([_finding(risk_level=RiskLevel.HIGH)], {})

    risk = outputs["risk_register"][0]
    assert risk.finding_id == "F-1"
    assert risk.data["risk_level"] == "high"
    assert risk.source_evidence_ids == ["E-1"]
