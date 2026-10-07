"""Explicit, fail-closed host execution modes."""

from enum import StrEnum


class ExecutionMode(StrEnum):
    TEST = "TEST"
    DEMO = "DEMO"
    GOVERNED = "GOVERNED"


def resolve_mode(value: ExecutionMode | str) -> ExecutionMode:
    try:
        return ExecutionMode(value)
    except ValueError as exc:
        raise ValueError("Execution mode must be TEST, DEMO or GOVERNED") from exc


def require_demo_adapters(*adapters) -> None:
    if not adapters or any(
        adapter is None or getattr(adapter, "synthetic_only", False) is not True
        for adapter in adapters
    ):
        raise ValueError(
            "DEMO mode requires every configured analysis/reviewer adapter to declare synthetic_only=True"
        )


def require_test_adapters(*adapters) -> None:
    if any(
        adapter is not None
        and getattr(adapter, "deterministic_test_adapter", False) is not True
        for adapter in adapters
    ):
        raise ValueError(
            "TEST mode accepts only adapters declaring deterministic_test_adapter=True"
        )
