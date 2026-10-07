from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalysisRequest:
    mode: str
    project_id: str | None = None
    document_ids: list[str] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None


@dataclass(frozen=True)
class LLMResponse:
    model: str
    response_id: str | None
    findings: list[dict[str, Any]]
    usage: dict[str, Any] = field(default_factory=dict)
    raw_output: dict[str, Any] | None = None
    task_decisions: list[dict[str, Any]] = field(default_factory=list)
    lesson_reviews: list[dict[str, Any]] = field(default_factory=list)
    finding_disproofs: list[dict[str, Any]] = field(default_factory=list)
