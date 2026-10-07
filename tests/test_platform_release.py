"""Release layout checks run independently of editable source imports."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHEEL_PROJECTS = [
    "engineering-ai-reviewer",
    "engineering-ai-system",
    "engineering-document-ai-brain",
    "engineering-execution",
    "engineering-registry",
]
EXPECTED_INDEPENDENT_PACKAGES = [
    "engineering_ai_reviewer",
    "engineering_document_ai_brain",
    "engineering_execution",
    "engineering_registry",
]


def test_release_check_keeps_platform_packages_independent():
    completed = subprocess.run(
        [sys.executable, "scripts/verify_platform_packages.py", "--layout"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["root_omits_independent_packages"] is True
    assert result["independent_packages"] == EXPECTED_INDEPENDENT_PACKAGES
    assert result["wheel_projects"] == EXPECTED_WHEEL_PROJECTS
    assert result["host_packages"] == ["agents", "engineering_ai_system", "pipelines"]
    assert result["standalone_facade_manifest_absent"] is True
