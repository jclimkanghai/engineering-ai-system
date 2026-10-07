"""Verify the five-wheel Engineering AI System release without source imports."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHEEL_PROJECTS = {
    "engineering-ai-reviewer",
    "engineering-ai-system",
    "engineering-document-ai-brain",
    "engineering-execution",
    "engineering-registry",
}
HOST_PACKAGES = {"agents", "engineering_ai_system", "pipelines"}
INDEPENDENT = {
    "engineering_registry": ROOT / "engineering_registry",
    "engineering_execution": ROOT / "engineering_execution",
    "engineering_document_ai_brain": ROOT / "engineering_document_ai_brain",
    "engineering_ai_reviewer": ROOT / "engineering_ai_reviewer",
}


def _layout() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        root = tomllib.load(handle)
    included = set(root["tool"]["setuptools"]["packages"]["find"]["include"])
    children = {}
    for package, directory in INDEPENDENT.items():
        with (directory / "pyproject.toml").open("rb") as handle:
            children[package] = tomllib.load(handle)["project"]["name"]
    independent_patterns = {package + "*" for package in INDEPENDENT}
    host_patterns = {"pipelines*", "agents*", "engineering_ai_system*"}
    return {
        "root_omits_independent_packages": not bool(included & independent_patterns),
        "host_packages": sorted(
            pattern.removesuffix("*") for pattern in included & host_patterns
        ),
        "standalone_facade_manifest_absent": not (
            ROOT / "engineering_ai_system" / "pyproject.toml"
        ).exists(),
        "independent_packages": sorted(children),
        "wheel_projects": sorted([root["project"]["name"], *children.values()]),
    }


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _build_wheel(source: Path, destination: Path, workspace: Path) -> Path:
    isolated = workspace / ("host" if source == ROOT else source.name)
    shutil.copytree(
        source,
        isolated,
        ignore=shutil.ignore_patterns(
            ".git",
            ".platform-venv",
            ".venv",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "__pycache__",
            "build",
            "dist",
            "*.egg-info",
            "*.dist-info",
            ".DS_Store",
            ".superpowers",
            "data",
            "*.sqlite",
        ),
    )
    before = set(destination.glob("*.whl"))
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(destination),
            str(isolated),
        ],
        cwd=workspace,
    )
    created = set(destination.glob("*.whl")) - before
    if len(created) != 1:
        raise RuntimeError("Wheel build did not produce exactly one artifact")
    return created.pop()


def _contains(wheel: Path, prefix: str) -> bool:
    with zipfile.ZipFile(wheel) as archive:
        return any(name.startswith(prefix + "/") for name in archive.namelist())


def _project_name(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
    return metadata["Name"]


def verify() -> dict:
    layout = _layout()
    if not layout["root_omits_independent_packages"]:
        raise ValueError(
            "Engineering AI System host wheel must not bundle independent packages"
        )
    if set(layout["wheel_projects"]) != EXPECTED_WHEEL_PROJECTS:
        raise ValueError(
            "Release metadata does not describe exactly the five system wheels"
        )
    if set(layout["host_packages"]) != HOST_PACKAGES:
        raise ValueError("Engineering AI System host package roots are incomplete")
    if not layout["standalone_facade_manifest_absent"]:
        raise ValueError("Engineering AI System facade still defines a separate wheel")
    with tempfile.TemporaryDirectory(prefix="engineering-platform-release-") as temp:
        workspace = Path(temp)
        wheels = workspace / "wheels"
        wheels.mkdir()
        sources = workspace / "sources"
        sources.mkdir()
        root_wheel = _build_wheel(ROOT, wheels, sources)
        platform_wheels = {
            package: _build_wheel(directory, wheels, sources)
            for package, directory in INDEPENDENT.items()
        }
        built_projects = {_project_name(root_wheel)} | {
            _project_name(wheel) for wheel in platform_wheels.values()
        }
        if built_projects != EXPECTED_WHEEL_PROJECTS:
            raise ValueError(
                "Build did not produce exactly the five selected distributions"
            )
        if any(not _contains(root_wheel, package) for package in HOST_PACKAGES):
            raise ValueError("Engineering AI System wheel is missing a host package")
        if any(_contains(root_wheel, package) for package in INDEPENDENT):
            raise ValueError("Host wheel contains an independent platform package")
        with zipfile.ZipFile(root_wheel) as archive:
            if any(
                "/build/" in name or "/dist/" in name for name in archive.namelist()
            ):
                raise ValueError("Host wheel contains cached build or dist files")
        if any(
            not _contains(wheel, package) for package, wheel in platform_wheels.items()
        ):
            raise ValueError("Independent package wheel has missing source files")
        environment = workspace / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
        python = environment / "bin" / "python"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(root_wheel),
                *map(str, platform_wheels.values()),
            ],
            cwd=workspace,
        )
        probe = (
            "from pathlib import Path; "
            "import sys; "
            "import engineering_ai_reviewer; "
            "assert not any(name == 'pipelines' or name.startswith('pipelines.') for name in sys.modules); "
            "from engineering_registry.demo import run_smoke; "
            "from engineering_ai_system import EngineeringAISystem; "
            "from engineering_ai_system.specialists import SpecialistSkillCatalog; "
            "catalog=SpecialistSkillCatalog(); "
            "assert catalog.load('engineering-document-review')['skill_digest']; "
            "skills=[('structural-review','structural','compare_revision'),('geotechnical-review','geotechnical','validate_traceability'),('marine-coastal-review','marine and coastal','generate_review_pack'),('hydraulic-environmental-review','hydraulic and environmental','compare_revision'),('civil-review','civil','validate_traceability'),('mechanical-review','mechanical','compare_revision'),('electrical-review','electrical','generate_review_pack'),('quantity-surveyor-review','quantity surveyor','validate_proposal_readiness'),('risk-specialist-review','risk specialist','validate_traceability')]; "
            "assert all(catalog.load_for_assignment(*item)['validation_status']=='draft' for item in skills); "
            "assert EngineeringAISystem.__name__ == 'EngineeringAISystem'; "
            "result=run_smoke(Path('smoke')); "
            "assert result['issue_status']=='closed' and result['result_verified']; "
            "from engineering_registry.extended_demo import run_extended_smoke; "
            "extended=run_extended_smoke(Path('extended-smoke')); "
            "assert extended['authority']=='ai_delegated' and extended['delegated_disposition']=='accept'; "
            "assert extended['alignment_status']=='aligned' and extended['human_verification_count']==0; "
            "assert extended['stress']=={'value':2000000,'unit':'Pa'}; "
            "print('installed-smoke=passed')"
        )
        _run([str(python), "-I", "-c", probe], cwd=workspace)
        _run(
            [
                str(python),
                "-I",
                str(ROOT / "scripts" / "verify_installed_review_gates.py"),
                str(workspace / "review-gate-smoke"),
            ],
            cwd=workspace,
        )
    return {
        **layout,
        "installed_smoke": True,
        "installed_extended_smoke": True,
        "installed_reviewer_gates": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", action="store_true")
    args = parser.parse_args()
    result = _layout() if args.layout else verify()
    if not result["root_omits_independent_packages"]:
        raise SystemExit("Independent package layout is invalid")
    print(json.dumps(result, default=sorted, sort_keys=True))


if __name__ == "__main__":
    main()
