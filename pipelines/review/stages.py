from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from .context import ReviewContext
from .models import StageResult, StageStatus


class PipelineStage(ABC):
    stage_id: str

    @abstractmethod
    def run(self, context: ReviewContext) -> StageResult:
        raise NotImplementedError


class FunctionStage(PipelineStage):
    def __init__(
        self,
        stage_id: str,
        function: Callable[[ReviewContext], dict[str, Any]],
        prerequisites: tuple[str, ...] = (),
    ) -> None:
        self.stage_id = stage_id
        self.function = function
        self.prerequisites = tuple(prerequisites)

    def run(self, context: ReviewContext) -> StageResult:
        missing = [key for key in self.prerequisites if context.get(key) is None]
        if missing:
            return StageResult(
                self.stage_id,
                StageStatus.BLOCKED,
                errors=[f"Missing prerequisite artifact(s): {', '.join(missing)}"],
            )
        try:
            data = self.function(context) or {}
            status = StageStatus(data.pop("_stage_status", StageStatus.COMPLETE))
            return StageResult(self.stage_id, status, data=data)
        except Exception as exc:
            return StageResult(
                self.stage_id,
                StageStatus.FAILED,
                errors=[f"{type(exc).__name__}: {exc}"],
            )
