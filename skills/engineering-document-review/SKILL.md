---
name: engineering-document-review
description: Review engineering documents, specifications, drawings, and requirements with registry-backed document control, evidence retrieval, revision awareness, and explicit uncertainty. Use for technical review, requirement analysis, compliance, gap, risk, or engineering comments.
---

# Engineering Document Review

Use the existing project prompts and pipeline as the detailed methodology. This skill is the orchestration layer; it does not replace the repository's registry, retrieval, schemas, or QA stages.

## Workflow

1. Establish document identity through the document registry where available: document ID, number, revision, date, status, issuer, supersession, governing status, source hash, and hierarchy.
2. Retrieve and cite evidence with document ID, revision, page, section, discipline, table, figure, drawing, or clause metadata where available.
3. Extract material requirements and obligations with source locations.
4. Check related documents, referenced documents, drawings, schedules, bulletins, addenda, clarifications, and later revisions before concluding.
5. Separate source facts, interpretations, engineering judgement, assumptions, recommendations, and unknowns.
6. Test the strongest reasonable interpretation before declaring a gap.
7. Reconcile material findings with the project finding registry when the review pipeline exposes it.
8. Apply final engineering QA and a human-review gate to material contractual, regulatory, safety, design, and commercial conclusions.

## Output

Use `prompts/output_template.md` for substantive reviews. Keep simple answers concise. Never invent requirements, revisions, citations, technical values, drawing numbers, clauses, authority positions, or registry entries. If the registry or retrieval layer does not contain enough evidence, say `UNKNOWN / INSUFFICIENT INFORMATION`.

## Project references

- `skills/engineering-document-review/references/workflow-map.md`
- `prompts/master_workflow.md`
- `prompts/master_reviewer.md`
- `prompts/document_controller.md`
- `prompts/truth.md`
- `prompts/steelman.md`
- `prompts/critic.md`
- `prompts/gap.md`
- `prompts/scope.md`
- `prompts/compliance.md`
- `prompts/change_compare.md`
- `prompts/risk.md`
- `prompts/contractual.md`
