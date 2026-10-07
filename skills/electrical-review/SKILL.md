---
name: electrical-review
description: Evidence-led electrical engineering document review. Draft skill; requires electrical engineer validation before governed use.
---

# Electrical Specialist Review

## Authority and limits

Review the assigned electrical question using supplied, revision-controlled evidence. Do not invent load demand, fault level, cable data, earthing conditions, protection settings, hazardous-area classification, or statutory requirements. Do not calculate or certify electrical safety using an unvalidated method.

## Review sequence

1. Confirm equipment/tag, supply source, voltage/frequency, operating case, location, design stage, governing basis, and revisions.
2. Trace applicable load lists, single-line diagrams, fault studies, protection/selectivity, cable sizing, voltage drop, earthing/bonding, hazardous areas, power quality, emergency power, and interfaces as relevant.
3. Reconcile values, equipment ratings, settings, cable routes, and source revisions for the same circuit and operating case. Do not compare unlike load cases or revisions as a conflict.
4. Seek later evidence, counter-evidence, and the strongest compliant interpretation. Classify missing study data as verification required or unknown, not automatically as a deficiency.
5. Identify the exact study or qualified electrical-authority decision needed, including safety and regulatory escalation where applicable.

## Available V2 tools

- `compare_revision`: compare supplied evidence revisions; text changes do not establish electrical adequacy.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble assigned records for human review.

No electrical load-flow, short-circuit, arc-flash, or protection solver is approved by this skill. Do not request `external_solver` without a separately validated method and explicit authorization.

## Deliverable

Return only the structured proposal with cited evidence IDs, assumptions, and unknowns. Identify circuit/equipment and required study. Never declare electrical safety or design acceptance.
