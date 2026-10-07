import pytest

from pipelines.engineering_graph.adapters import FindingImportError, finding_to_issue
from pipelines.findings.models import Finding, FindingStatus, RiskLevel


def sample_finding(**changes):
    values = {
        "finding_id": "STR-024",
        "title": "Potential pile capacity shortfall",
        "finding": "Stated capacity is below stated demand.",
        "status": FindingStatus.POTENTIAL,
        "source_evidence_ids": ["E-1"],
        "requirement_ids": ["R-4"],
        "interpretation": "Compare the stated values.",
        "impact": "Foundation redesign may be required.",
        "risk_level": RiskLevel.HIGH,
        "risk_dimensions": ["structural", "programme"],
        "recommendation": "Request a checked calculation.",
        "required_action": "Designer to reassess foundation solution.",
        "responsibility_assignment_ids": ["RA-1"],
        "assumptions": ["Load combination is governing."],
        "uncertainties": ["Pile group interaction is not shown."],
        "conflicts": ["Drawing and calculation revisions differ."],
    }
    values.update(changes)
    return Finding(**values)


def test_adapter_preserves_finding_links_and_source_provenance():
    issue = finding_to_issue(
        sample_finding(),
        "project-a",
        evidence_by_id={
            "E-1": {
                "document_id": "CALC-01",
                "revision": "3",
                "locator": "§8.4",
                "text": "Demand 1,850 kN; capacity 1,420 kN.",
            }
        },
        validation_errors=[],
    )

    assert issue.issue_id == "v1-finding:project-a:STR-024"
    assert issue.source_finding_id == "STR-024"
    assert issue.evidence_ids == ["E-1"]
    assert issue.requirement_ids == ["R-4"]
    assert issue.source_document_ids == ["CALC-01"]
    assert issue.source_facts == ["Demand 1,850 kN; capacity 1,420 kN."]
    assert issue.recommendation == "Request a checked calculation."
    assert issue.required_action == "Designer to reassess foundation solution."
    assert issue.responsibility_assignment_ids == ["RA-1"]
    assert issue.risk_dimensions == ["structural", "programme"]
    assert issue.unknowns == ["Pile group interaction is not shown."]
    assert issue.status.value == "proposed"
    assert issue.human_review_required is True


def test_adapter_keeps_absent_source_information_explicitly_unknown():
    issue = finding_to_issue(
        sample_finding(source_evidence_ids=[]),
        "project-a",
        evidence_by_id={},
        validation_errors=[],
    )

    assert issue.evidence_ids == []
    assert issue.source_document_ids == []
    assert any("source evidence" in item.lower() for item in issue.unknowns)


def test_adapter_rejects_unvalidated_or_unresolved_citations():
    with pytest.raises(FindingImportError, match="validation"):
        finding_to_issue(
            sample_finding(),
            "project-a",
            evidence_by_id={},
            validation_errors=["unknown evidence ID E-1"],
        )
    with pytest.raises(FindingImportError, match="E-1"):
        finding_to_issue(
            sample_finding(), "project-a", evidence_by_id={}, validation_errors=[]
        )


def test_import_finding_persists_issue_evidence_document_and_revision_links(tmp_path):
    from pipelines.engineering_graph.adapters import import_finding
    from pipelines.engineering_graph.models import RelationshipType
    from pipelines.engineering_graph.store import SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    evidence = {
        "document_id": "CALC-01",
        "revision": "3",
        "title": "Pile calculation",
        "locator": "§8.4",
        "page": 22,
        "text": "Demand 1,850 kN; capacity 1,420 kN.",
        "issue_status": "approved",
        "governing_status": "current",
    }
    issue = import_finding(
        store,
        sample_finding(),
        "project-a",
        evidence_by_id={"E-1": evidence},
        validation_errors=[],
    )

    assert store.get_issue("project-a", issue.issue_id) == issue
    assert store.get_node("project-a", "E-1").attributes["page"] == 22
    assert store.get_node("project-a", "document:CALC-01").node_type.value == "document"
    assert (
        store.get_node("project-a", "document-revision:CALC-01:3").node_type.value
        == "document_revision"
    )
    issue_edges = store.get_edges("project-a", issue.issue_id)
    assert any(edge.target_node_id == "E-1" for edge in issue_edges)
    evidence_edges = store.get_edges("project-a", "E-1")
    assert any(
        edge.relationship == RelationshipType.DERIVED_FROM for edge in evidence_edges
    )

    # Replaying the same validated V1 record is safe and idempotent.
    import_finding(
        store,
        sample_finding(),
        "project-a",
        evidence_by_id={"E-1": evidence},
        validation_errors=[],
    )
    assert len(store.get_edges("project-a", issue.issue_id)) == len(issue_edges)
    store.close()


def test_import_rejects_reused_evidence_id_with_conflicting_source_content(tmp_path):
    from pipelines.engineering_graph.adapters import import_finding
    from pipelines.engineering_graph.store import GraphIntegrityError, SQLiteGraphStore

    store = SQLiteGraphStore(tmp_path / "graph.sqlite")
    base = {
        "document_id": "CALC-01",
        "revision": "3",
        "locator": "§8.4",
        "text": "Capacity 1.",
    }
    import_finding(
        store,
        sample_finding(),
        "project-a",
        evidence_by_id={"E-1": base},
        validation_errors=[],
    )
    changed = {**base, "revision": "4", "page": 23}

    with pytest.raises(
        (FindingImportError, GraphIntegrityError), match="content|conflict"
    ):
        import_finding(
            store,
            sample_finding(),
            "project-a",
            evidence_by_id={"E-1": changed},
            validation_errors=[],
        )
    assert store.get_node("project-a", "E-1").attributes["revision"] == "3"
    store.close()
