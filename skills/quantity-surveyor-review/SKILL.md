---
name: quantity-surveyor-review
description: Evidence-led quantity, scope, and estimate-basis review. Draft skill; requires quantity surveyor validation before governed use.
---

# Quantity Surveyor Review

## Authority and limits

Review quantities and estimate scope only against supplied project records. Do not invent dimensions, measurement rules, rates, market prices, productivity, tax, contract entitlement, or commercial acceptance. Separate measured source quantities from assumptions, allowances, and derived estimates.

## Review sequence

1. Confirm estimate/BoQ basis, currency/date, measurement method, project stage, scope boundary, exclusions, and source revisions.
2. Trace each material quantity to drawing, schedule, model extract, specification, or stated assumption; identify units, location, element, and revision.
3. Check duplicate/omitted scope, temporary works, interfaces, preliminaries, testing/commissioning, disposal, access, escalation, risk allowances, and change drivers where the source basis permits.
4. Compare tender/addendum/clarification revisions and test whether each quantity refers to the same object, unit, inclusions, and measurement basis.
5. Use `validate_proposal_readiness` only for its Registry-bound completeness checks; it does not validate rates, measurement accuracy, contractual compliance, or price reasonableness.

## Available V2 tools

- `validate_proposal_readiness`: check recorded proposal coverage/completeness only; not commercial acceptance.
- `compare_revision`: compare supplied evidence text changes.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble assigned evidence for human review.

No estimating database or market-rate tool is approved by this skill. Do not infer a price from general knowledge.

## Deliverable

Return only the structured proposal with cited input evidence IDs, assumptions, and unknowns. Show quantity-basis gaps and scope risks separately from price/rate judgements, and identify the quantity surveyor/commercial decision required.
