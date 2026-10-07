from pipelines.accountability import classify_assignment


def test_explicit_assignment_preserves_source_basis():
    assignment = classify_assignment(
        party_id="CONTRACTOR",
        role="action_owner",
        responsibility_type="submit revised calculation",
        source_evidence_ids=["E-1"],
        source_class="explicit",
    )

    assert assignment.source_class == "explicit"
    assert assignment.source_evidence_ids == ["E-1"]


def test_missing_party_is_explicitly_unknown():
    assignment = classify_assignment(
        party_id=None,
        role="approver",
        responsibility_type="acceptance",
        source_evidence_ids=[],
        source_class="unknown",
    )

    assert assignment.party_id is None
    assert assignment.human_review_required is True
