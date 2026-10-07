from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .context import ReviewContext
from .models import ReviewRequest, ReviewResult, StageStatus
from .stages import PipelineStage


class ReviewPipeline:
    def __init__(self, stages: list[PipelineStage]) -> None:
        self.stages = list(stages)
        self.stage_ids = [stage.stage_id for stage in self.stages]

    def run(self, request: ReviewRequest) -> ReviewResult:
        context = ReviewContext(request)
        results = []
        manifest_path = self._manifest_path(request)
        manifest = self._initial_manifest(request)
        self._write_manifest(manifest_path, manifest)
        for stage in self.stages:
            # Revision comparison is an optional branch of /allin. Keep the
            # stage in the declared graph for compatibility, but do not spend
            # work on it when the request has no comparison set.
            if stage.stage_id == "revision_change" and not request.compare_document_ids:
                from .models import StageResult

                result = StageResult(
                    stage.stage_id,
                    StageStatus.SKIPPED,
                    data={"changes": [], "change_status": "NOT_REQUESTED"},
                )
            else:
                result = stage.run(context)
            results.append(result)
            context.stage_results.append(result)
            manifest["stages"].append(
                {
                    "stage_id": result.stage_id,
                    "status": result.status.value,
                    "errors": list(result.errors),
                    "warnings": list(result.warnings),
                    "artifact_keys": sorted(result.data),
                    "completed_at": _timestamp(),
                }
            )
            manifest["next_stage"] = (
                self.stage_ids[len(results)]
                if len(results) < len(self.stage_ids)
                else None
            )
            manifest["updated_at"] = _timestamp()
            if result.status not in {StageStatus.COMPLETE, StageStatus.SKIPPED}:
                manifest["status"] = result.status.value
                self._write_manifest(manifest_path, manifest)
                return ReviewResult(
                    request, results, self._findings(results), result.status
                )
            if result.status in {StageStatus.COMPLETE, StageStatus.SKIPPED}:
                context.artifacts.update(result.data)
                self._write_manifest(manifest_path, manifest)
                continue

        status = StageStatus.COMPLETE
        if request.require_human_gate and not context.get("human_review_ready", False):
            status = StageStatus.BLOCKED
        manifest["status"] = status.value
        manifest["next_stage"] = None
        manifest["updated_at"] = _timestamp()
        self._write_manifest(manifest_path, manifest)
        return ReviewResult(request, results, self._findings(results), status)

    @staticmethod
    def _manifest_path(request: ReviewRequest) -> Path | None:
        value = request.metadata.get("run_manifest_path")
        return Path(str(value)).expanduser() if value else None

    @staticmethod
    def _initial_manifest(request: ReviewRequest) -> dict:
        run_id = request.metadata.get("run_id")
        if not run_id:
            canonical = json.dumps(
                {
                    "document_ids": request.document_ids,
                    "compare_document_ids": request.compare_document_ids,
                    "review_mode": request.review_mode,
                    "requested_scope": request.requested_scope,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            run_id = "run_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
        now = _timestamp()
        return {
            "schema_version": 1,
            "run_id": str(run_id),
            "status": "RUNNING",
            "review_mode": request.review_mode,
            "document_ids": list(request.document_ids),
            "compare_document_ids": list(request.compare_document_ids),
            "requested_scope": request.requested_scope,
            "stages": [],
            "next_stage": None,
            "started_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _write_manifest(path: Path | None, manifest: dict) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            # A diagnostic manifest must never turn a source review into a
            # failed review when its destination is unavailable.
            return

    @staticmethod
    def _findings(results):
        # Later stages may replace the working finding set (for example, the
        # deterministic consolidation stage). Return the final artifact once;
        # aggregating every stage would double-count findings in ReviewResult.
        for result in reversed(results):
            if isinstance(result.data.get("findings"), list):
                return list(result.data["findings"])
        return []


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
