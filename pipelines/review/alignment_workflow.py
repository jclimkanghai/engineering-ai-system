"""Compatibility entry point for AI Reviewer in the Engineering AI host."""

from engineering_ai_reviewer import (
    build_alignment_reviewer as _build_alignment_reviewer,
)
from pipelines.llm.stage import AnalysisAdapter

from .reviewer_adapter import ReviewerAnalysisAdapter


def build_alignment_reviewer(adapter: AnalysisAdapter):
    """Keep the existing host import path and provider adapter contract."""
    reviewer_adapter = ReviewerAnalysisAdapter(adapter)
    reviewer = _build_alignment_reviewer(reviewer_adapter)
    reviewer.supports_concurrent_calls = reviewer_adapter.supports_concurrent_calls
    reviewer.deterministic_test_adapter = reviewer_adapter.deterministic_test_adapter
    return reviewer


__all__ = ["build_alignment_reviewer"]
