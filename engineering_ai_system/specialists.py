"""Verified skill loading and output checks for discipline specialist proposals."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from engineering_execution.tools import TOOLS
from engineering_registry.service import digest, text

_SKILL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PROPOSAL_FIELDS = {
    "summary",
    "recommendation",
    "evidence_ids",
    "assumptions",
    "unknowns",
}


class SpecialistSkillCatalog:
    """Loads exact local ``<skill-id>/SKILL.md`` files without path traversal."""

    def __init__(self, root: str | Path | None = None) -> None:
        if root is not None:
            self.root = Path(root)
            return
        checkout_root = Path(__file__).resolve().parent.parent / "skills"
        installed_root = Path(sys.prefix) / "share" / "engineering-ai-system" / "skills"
        self.root = checkout_root if checkout_root.is_dir() else installed_root

    def load(self, skill_id: str) -> dict[str, Any]:
        if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
            raise ValueError("Unknown specialist skill")
        root = self.root.resolve()
        path = (root / skill_id / "SKILL.md").resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("Unknown specialist skill")
        content = path.read_text(encoding="utf-8")
        if not content.strip() or len(content.encode("utf-8")) > 100_000:
            raise ValueError("Invalid specialist skill content")
        manifest_path = (root / skill_id / "specialist.json").resolve()
        manifest = None
        if manifest_path.is_file() and root in manifest_path.parents:
            try:
                manifest_text = manifest_path.read_text(encoding="utf-8")
                if len(manifest_text.encode("utf-8")) > 20_000:
                    raise ValueError("Specialist skill manifest exceeds size limit")
                manifest = json.loads(manifest_text)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid specialist skill manifest") from exc
            self._validate_manifest(skill_id, manifest)
        skill_digest = (
            digest({"content": content, "manifest": manifest})
            if manifest is not None
            else digest(content)
        )
        return {
            "skill_id": skill_id,
            "content": content,
            "skill_digest": skill_digest,
            "manifest": manifest,
            "validation_status": (
                manifest["validation_status"]
                if manifest is not None
                else "unclassified"
            ),
        }

    def load_for_assignment(
        self, skill_id: str, discipline: str, tool: str
    ) -> dict[str, Any]:
        """Require an explicit matching discipline/tool scope for a specialist."""
        skill = self.load(skill_id)
        manifest = skill.get("manifest")
        if manifest is None:
            raise ValueError("Specialist skill has no discipline validation manifest")
        if manifest["discipline"].casefold() != discipline.casefold():
            raise ValueError("Specialist skill discipline does not match assignment")
        if tool not in manifest["allowed_tools"]:
            raise ValueError("Specialist skill tool is not allowed")
        return skill

    @staticmethod
    def _validate_manifest(skill_id: str, manifest: Any) -> None:
        fields = {
            "schema_version",
            "skill_id",
            "discipline",
            "validation_status",
            "validated_by",
            "validation_reference",
            "allowed_tools",
        }
        if not isinstance(manifest, dict) or set(manifest) != fields:
            raise ValueError("Invalid specialist skill manifest")
        if (
            type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1
            or manifest["skill_id"] != skill_id
            or not isinstance(manifest["discipline"], str)
            or not manifest["discipline"].strip()
            or len(manifest["discipline"]) > 120
            or not isinstance(manifest["validation_status"], str)
            or manifest["validation_status"] not in {"draft", "validated"}
        ):
            raise ValueError("Invalid specialist skill manifest")
        tools = manifest["allowed_tools"]
        if (
            not isinstance(tools, list)
            or not tools
            or any(not isinstance(tool, str) or tool not in TOOLS for tool in tools)
            or len(tools) != len(set(tools))
        ):
            raise ValueError("Invalid specialist skill manifest tool scope")
        for field in ("validated_by", "validation_reference"):
            value = manifest[field]
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 512
            ):
                raise ValueError("Invalid specialist skill validation record")
        if manifest["validation_status"] == "validated" and not all(
            manifest[field] for field in ("validated_by", "validation_reference")
        ):
            raise ValueError("Validated specialist skills require reviewer provenance")


def validate_specialist_proposal(
    proposal: dict[str, Any], allowed_source_ids: set[str]
) -> dict[str, Any]:
    """Validate a specialist's advisory response and reject uncited source IDs."""
    if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
        raise ValueError("Invalid specialist proposal schema")
    try:
        checked = {
            "summary": text(proposal["summary"], "summary"),
            "recommendation": text(proposal["recommendation"], "recommendation"),
        }
        if any(len(checked[field]) > 3000 for field in ("summary", "recommendation")):
            raise ValueError
        for field in ("evidence_ids", "assumptions", "unknowns"):
            values = proposal[field]
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() or len(value) > 1000
                for value in values
            ):
                raise ValueError
            if len(values) > (20 if field == "evidence_ids" else 10):
                raise ValueError
            values = [value.strip() for value in values]
            if len(values) != len(set(values)):
                raise ValueError
            checked[field] = values
        if not set(checked["evidence_ids"]).issubset(allowed_source_ids):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid specialist proposal or source references") from exc
    return checked
