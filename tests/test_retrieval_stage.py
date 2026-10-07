from pipelines.retrieval.stage import run_evidence_retrieval


def test_retrieval_stage_does_not_guess_missing_query():
    result = run_evidence_retrieval(
        type("Context", (), {"get": lambda *_: [], "request": None})()
    )

    assert result["evidence_retrieval_status"] == "NOT_REQUESTED"
    assert result["evidence_bundle"]["evidence"] == []
