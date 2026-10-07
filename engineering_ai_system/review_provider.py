"""Host-selected lazy provider adapters for review workflows."""

from __future__ import annotations

import threading


class LazyOpenAIAnalysisAdapter:
    """Use one lazy OpenAI client per worker thread for safe concurrent calls."""

    supports_concurrent_calls = True

    def __init__(self) -> None:
        self._thread_local = threading.local()

    def analyse(self, request):
        adapter = getattr(self._thread_local, "adapter", None)
        if adapter is None:
            from pipelines.llm.adapter import OpenAIAnalysisAdapter

            adapter = OpenAIAnalysisAdapter()
            self._thread_local.adapter = adapter
        return adapter.analyse(request)
