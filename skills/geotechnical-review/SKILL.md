---
name: geotechnical-review
description: Evidence-led geotechnical and foundation document review. Draft skill; requires geotechnical engineer validation before governed use.
---

# Geotechnical Specialist Review

## Authority and limits

Assess the assigned geotechnical question using only the supplied site-specific evidence. Do not infer soil/rock parameters, groundwater, design resistance, or applicability from a nearby borehole or general practice. Do not certify foundation adequacy, perform unvalidated calculations, or replace a geotechnical engineer.

## Review sequence

1. Identify the asset location, investigation points, strata, groundwater observations, test dates, interpreted ground model, design stage, and source revisions.
2. Check that soil profile and parameters are tied to the relevant location and design situation. Keep measured data, interpreted parameters, and adopted design values distinct.
3. Trace settlement, bearing, lateral resistance, pile capacity, negative skin friction, liquefaction, slope/global stability, scour, seismic, construction, and durability checks only where relevant and evidenced.
4. Compare calculations, logs, test reports, pile schedules, drawings, and later revisions for the same location, element, load case, and design basis. Seek superseding and counter-evidence.
5. Classify unsupported adequacy as verification required or unknown; specify the calculation/investigation needed and the geotechnical engineer decision required.

## Available V2 tools

- `compare_revision`: compare supplied evidence revisions; differences do not establish geotechnical consequence.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble the exact assigned records for human review.

No geotechnical calculation or ground-model solver is approved by this skill. Do not request `external_solver` without a separately validated solver and explicit authorization.

## Deliverable

Return only the structured proposal with concise summary, recommendation, input evidence IDs, assumptions, and unknowns. Identify location/profile applicability and the specific independent calculation or investigation still required. Do not state that a foundation is safe or acceptable.
