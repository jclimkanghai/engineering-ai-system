# Workflow Map

Use the repository prompts as the detailed source methodology:

- `prompts/master_workflow.md` — orchestration
- `prompts/master_reviewer.md` — review standard
- `prompts/document_controller.md` — authority and revision
- `prompts/requirement_extraction.md` — requirements
- `prompts/truth.md` — evidence discipline
- `prompts/steelman.md` — strongest interpretation
- `prompts/critic.md` — critical challenge
- `prompts/gap.md` — gaps
- `prompts/scope.md` — scope
- `prompts/compliance.md` — compliance
- `prompts/change_compare.md` — changes
- `prompts/risk.md` — risk
- `prompts/contractual.md` — contractual analysis

Use the repository pipeline components when the task is performed inside this project:

- `pipelines/registry/document_registry.py` — controlled source document identity, revisions, source hashes, status, and supersession.
- `pipelines/retrieval/engine.py` — evidence retrieval with document, revision, locator, section, page, and discipline metadata.
- `pipelines/registry/finding_registry.py` — project-scoped finding reconciliation after review, before approved writes.
- `pipelines/review/production.py` — final QA, human gate, digest-bound approval, and approved registry persistence.

Skills guide the reasoning flow. They do not store project evidence, write registry entries directly, or replace the review pipeline.
