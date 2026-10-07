from pipelines.retrieval.engine import EvidenceRetrievalEngine
from pipelines.retrieval.models import RetrievalQuery


def test_engine_filters_by_document_and_returns_evidence(tmp_path):
    path = tmp_path / "spec.md"
    path.write_text("# Piling\n\nThe Contractor shall test the pile.", encoding="utf-8")
    engine = EvidenceRetrievalEngine()
    engine.add_markdown_file(path, document_id="DOC-1", revision="A")

    bundle = engine.query(
        RetrievalQuery(
            query="Contractor test pile",
            document_ids=["DOC-1"],
            top_k=3,
        )
    )

    assert bundle.evidence
    assert bundle.evidence[0]["document_id"] == "DOC-1"


def test_empty_query_is_explicit():
    bundle = EvidenceRetrievalEngine().query(RetrievalQuery(query=""))

    assert bundle.evidence == []
