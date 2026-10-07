import pytest

from pipelines.review.production import build_production_pipeline


@pytest.mark.parametrize(
    "mode",
    [
        "truth",
        "steelman",
        "gap",
        "critic",
        "scope",
        "compliance",
        "change",
        "risk",
        "competitor",
        "x10think",
        "allin",
    ],
)
def test_supported_modes_build(mode):
    pipeline = build_production_pipeline(mode)

    assert pipeline.stage_ids[0] == "document_control"
