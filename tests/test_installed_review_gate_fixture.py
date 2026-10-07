from pipelines.findings import EvidenceRef
from pipelines.llm.adapter import findings_from_response
from pipelines.llm.models import LLMResponse
from scripts.verify_installed_review_gates import _finding


def test_installed_review_gate_fixture_matches_current_finding_contract():
    finding = _finding("plan_alignment", ["evidence:EA"], ["requirement:R-LOAD"])

    findings, errors, _warnings = findings_from_response(
        LLMResponse("fixture", "plan", [finding]),
        [EvidenceRef("evidence:EA")],
        allowed_requirement_ids={"requirement:R-LOAD"},
    )

    assert errors == []
    assert findings[0].qualification.value == "observation"
