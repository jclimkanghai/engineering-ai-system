from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from engineering_ai_system.review_provider import LazyOpenAIAnalysisAdapter


def test_lazy_provider_creates_one_provider_adapter_per_calling_thread(monkeypatch):
    calls = []
    barrier = threading.Barrier(2)

    class FakeProviderAdapter:
        def __init__(self):
            self.thread_id = threading.get_ident()
            calls.append(self.thread_id)

        def analyse(self, request):
            barrier.wait(timeout=3)
            return self.thread_id, request

    monkeypatch.setattr(
        "pipelines.llm.adapter.OpenAIAnalysisAdapter", FakeProviderAdapter
    )
    adapter = LazyOpenAIAnalysisAdapter()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adapter.analyse, ("first", "second")))

    assert adapter.supports_concurrent_calls is True
    assert len(set(calls)) == 2
    assert {thread_id for thread_id, _ in results} == set(calls)
