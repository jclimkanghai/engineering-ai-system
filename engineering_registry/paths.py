"""Canonical physical Registry paths for project and organisation data."""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _checked(identifier: str) -> str:
    if not isinstance(identifier, str) or not _SAFE_ID.fullmatch(identifier):
        raise ValueError("Registry scope ID contains unsafe path characters")
    return identifier


def project_registry_path(data_root: str | Path, project_id: str) -> Path:
    return (
        Path(data_root).expanduser().resolve()
        / "projects"
        / _checked(project_id)
        / "registry.sqlite3"
    )


def organization_registry_path(data_root: str | Path, organization_id: str) -> Path:
    return (
        Path(data_root).expanduser().resolve()
        / "organizations"
        / _checked(organization_id)
        / "registry.sqlite3"
    )
