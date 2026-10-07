from pipelines.retrieval.chunker import chunk_markdown


def test_chunk_markdown_preserves_provenance_and_stable_ids():
    kwargs = dict(
        document_id="DOC-1",
        document_number="SPEC-1",
        revision="A",
        discipline="civil",
        markdown="# Piling\n\nThe Contractor shall test the pile.",
    )
    first = chunk_markdown(**kwargs)
    second = chunk_markdown(**kwargs)

    assert first[0].evidence_id == second[0].evidence_id
    assert first[0].document_id == "DOC-1"
    assert first[0].section == "Piling"
    assert first[0].text == "The Contractor shall test the pile."
