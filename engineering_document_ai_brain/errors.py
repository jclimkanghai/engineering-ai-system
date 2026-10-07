"""Recoverable provider failure without leaking provider response details."""


class AnalysisUnavailable(RuntimeError):
    """Analysis did not complete; any durable V2 output remains unverified."""
