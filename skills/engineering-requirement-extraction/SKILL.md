---
name: engineering-requirement-extraction
description: Extract traceable technical, contractual, regulatory, and project requirements from registry-controlled engineering document sets. Use for requirement registers, obligation extraction, and evidence mapping.
---

# Requirement Extraction

Read `prompts/requirement_extraction.md` and preserve existing requirement schemas as canonical. Use the repository requirement extractor and document registry where available; do not create a parallel requirement model inside the skill.

For each material requirement, capture where available:

- requirement ID and exact or faithful text;
- source document ID, document number, source hash, revision, date, section, page, table, figure, drawing, discipline, or clause;
- requirement type, category, obligation, and mandatory/advisory nature;
- responsible party, deliverable, acceptance or verification method;
- discipline, scope, interfaces, evidence required, compliance status, risk, and confidence.

Do not perform compliance judgement unless requested or required by the selected review workflow. Do not infer missing clauses, revisions, document numbers, parameters, or responsible parties. Use `UNKNOWN / INSUFFICIENT INFORMATION` when the source is inadequate.
