import json

from pipelines.llm.adapter import OpenAIAnalysisAdapter
from pipelines.llm.models import AnalysisRequest


class FakeResponse:
    id = "resp-test"
    output_text = json.dumps({"findings": []})
    usage = None


class FakeClient:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


def test_adapter_uses_strict_schema_and_store_false():
    client = FakeClient()
    response = OpenAIAnalysisAdapter(client=client, model="test-model").analyse(
        AnalysisRequest(mode="truth", evidence=[])
    )

    call = client.calls[0]
    assert call["model"] == "test-model"
    assert call["store"] is False
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert response.findings == []


def test_adapter_supports_per_mode_model_override(monkeypatch):
    client = FakeClient()
    monkeypatch.setenv("OPENAI_MODEL_RISK", "risk-model")

    response = OpenAIAnalysisAdapter(client=client, model="default-model").analyse(
        AnalysisRequest(mode="risk", evidence=[])
    )

    assert client.calls[0]["model"] == "risk-model"
    assert response.model == "risk-model"


def test_execution_planning_uses_material_decision_schema():
    from types import SimpleNamespace

    class PlanningClient:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                output_text='{"findings":[],"task_decisions":[],"lesson_reviews":[]}',
                id="planning-fixture",
                usage=None,
            )

    client = PlanningClient()
    response = OpenAIAnalysisAdapter(client=client).analyse(
        AnalysisRequest(mode="execution_planning", evidence=[])
    )
    schema = client.kwargs["text"]["format"]["schema"]
    assert "task_decisions" in schema["required"]
    assert "lesson_reviews" in schema["required"]
    lesson_schema = schema["properties"]["lesson_reviews"]["items"]
    assert set(lesson_schema["required"]) == set(lesson_schema["properties"])
    assert response.task_decisions == []
    assert response.lesson_reviews == []
