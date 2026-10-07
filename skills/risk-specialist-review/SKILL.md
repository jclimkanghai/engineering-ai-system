---
name: risk-specialist-review
description: Evidence-led project and engineering risk review. Draft skill; requires risk specialist validation before governed use.
---

# Risk Specialist Review

## Authority and limits

Review the assigned risk question using supplied evidence and recorded project assumptions. Do not invent likelihoods, consequence ratings, tolerability criteria, safeguards, owners, or regulatory classifications. Do not convert uncertainty into a quantified risk score without the approved project method. Risk review does not replace discipline design verification or safety authority.

## Review sequence

1. Identify the objective, asset/system, lifecycle phase, risk method and scales, source revisions, and affected stakeholders.
2. Form each supported risk as cause → uncertain event/condition → consequence; cite evidence for each part and label assumptions/unknowns.
3. Check existing controls, verification evidence, dependencies, interfaces, responsible owner, action, due point, and residual exposure only where recorded.
4. Distinguish an observed issue, a potential risk, an information gap, and an unverified control. Search counter-evidence and later decisions before raising a duplicate or superseded item.
5. Escalate credible safety/regulatory concerns and unresolved high-consequence uncertainty to the responsible engineer/authority; do not decide acceptability.

## Available V2 tools

- `validate_traceability`: check source and requirement links.
- `compare_revision`: compare supplied evidence revisions and identify text changes for review.
- `generate_review_pack`: assemble the exact assigned records for human review.

No risk quantification or bow-tie solver is approved by this skill. Do not invent numerical scores or request `external_solver` without a separately validated method and authorization.

## Deliverable

Return only the structured proposal with cited evidence IDs, assumptions, and unknowns. State cause-event-consequence, existing evidence-backed controls, missing control evidence, owner/action if supplied, and the decision that remains with the human risk owner or engineer.
