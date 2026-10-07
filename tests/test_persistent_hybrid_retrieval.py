from pipelines.retrieval.models import EvidenceChunk, RetrievalQuery
from pipelines.retrieval.store import PersistentEvidenceStore


def test_store_reopens_and_filters_metadata(tmp_path):
    db = tmp_path / "evidence.sqlite3"
    store = PersistentEvidenceStore(db)
    store.upsert(
        EvidenceChunk(
            evidence_id="E-1",
            document_id="DOC-1",
            document_number="SPEC-1",
            title="Specification",
            revision="A",
            revision_date=None,
            issue_status="issued",
            governing_status="governing",
            discipline="civil",
            section="Piling",
            locator="section=Piling;chunk=1",
            text="Pile testing is required.",
        )
    )
    store.close()

    reopened = PersistentEvidenceStore(db)
    assert reopened.count() == 1
    assert reopened.get("E-1").document_id == "DOC-1"
    assert [
        c.evidence_id
        for c in reopened.filtered(RetrievalQuery(query="", document_ids=["DOC-1"]))
    ] == ["E-1"]
    reopened.close()


def test_injected_semantic_score_has_deterministic_tie_break(tmp_path):
    from pipelines.retrieval.hybrid import HybridRetriever

    store = PersistentEvidenceStore(tmp_path / "e.sqlite3")
    for evidence_id, text in (("E-1", "pile requirement"), ("E-2", "pile requirement")):
        store.upsert(
            EvidenceChunk(
                evidence_id=evidence_id,
                document_id="DOC-1",
                document_number=None,
                title=None,
                revision="A",
                revision_date=None,
                issue_status=None,
                governing_status=None,
                discipline=None,
                section=None,
                locator=evidence_id,
                text=text,
            )
        )
    hits = HybridRetriever(
        store,
        semantic_scorer=lambda _q, chunks: {chunk.evidence_id: 1.0 for chunk in chunks},
    ).retrieve(RetrievalQuery(query="pile", top_k=2))

    assert [hit.evidence_id for hit in hits] == ["E-1", "E-2"]
    store.close()


def test_upsert_many_batches_rows_and_preserves_updates(tmp_path):
    store = PersistentEvidenceStore(tmp_path / "batch.sqlite3")

    def chunks():
        for evidence_id in ("E-1", "E-2", "E-3"):
            yield EvidenceChunk(
                evidence_id=evidence_id,
                document_id="DOC-1",
                document_number=None,
                title=None,
                revision="A",
                revision_date=None,
                issue_status=None,
                governing_status=None,
                discipline=None,
                section=None,
                locator=evidence_id,
                text=f"text for {evidence_id}",
            )

    assert store.upsert_many(chunks()) == 3
    assert store.count() == 3
    store.upsert_many(
        [
            EvidenceChunk(
                evidence_id="E-2",
                document_id="DOC-2",
                document_number=None,
                title=None,
                revision="B",
                revision_date=None,
                issue_status=None,
                governing_status=None,
                discipline=None,
                section=None,
                locator="E-2",
                text="updated text",
            )
        ]
    )
    assert store.get("E-2").document_id == "DOC-2"
    assert store.get("E-2").text == "updated text"
    store.close()
