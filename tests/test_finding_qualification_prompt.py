from pipelines.llm.adapter import SYSTEM_INSTRUCTIONS


def test_optional_scope_and_incomplete_commercial_evidence_are_not_confirmed_conflicts():
    prompt = " ".join(SYSTEM_INSTRUCTIONS.lower().split())
    assert "on-instruction" in prompt
    assert "pricing" in prompt
    assert "unknown" in prompt
    assert "confirmed technical inconsistency" in prompt
    assert "missing" in prompt


def test_asset_count_conflicts_require_matching_scope_and_denominator():
    prompt = " ".join(SYSTEM_INSTRUCTIONS.lower().split())
    assert "denominator" in prompt
    assert "same asset" in prompt
    assert "scope boundary" in prompt
