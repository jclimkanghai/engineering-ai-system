from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ReviewRequest, StageResult


@dataclass
class ReviewContext:
    request: ReviewRequest
    artifacts: dict[str, Any] = field(default_factory=dict)
    stage_results: list[StageResult] = field(default_factory=list)

    def put(self, key: str, value: Any) -> None:
        self.artifacts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)
