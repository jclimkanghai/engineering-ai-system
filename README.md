# Engineering AI System

A supervised platform for engineering document review, evidence-grounded findings, bounded specialist tasks, and controlled project records.

## System boundaries

The system supports professional review. It does not certify engineering calculations, grant professional approval, or replace the responsible human engineer. Project documents, candidate outputs, and Registry data belong in approved private project storage.

## Architecture

- **Document AI** structures requests, retrieves controlled evidence, prepares assessments, and integrates bounded work.
- **Engineering V2** performs bounded tasks through configured tools and discipline skills.
- **Independent AI Reviewer** checks client and project alignment at the required workflow gates.
- **Engineering Registry** retains project evidence, decisions, and separately governed organisational knowledge.

See [engineering_ai_system/README.md](engineering_ai_system/README.md) and the project workflow, authority hierarchy, knowledge lifecycle, and specialist skill catalog under `docs/implementation/`.

## Local setup

Use Python 3.11 or later. Run `scripts/setup_platform.sh` to create an isolated environment and install the platform packages. The platform does not select an AI provider automatically; configure a provider through the host environment when a live model run is authorized.

Run checks with `pytest`, `ruff check .`, and `python scripts/verify_platform_packages.py`.
