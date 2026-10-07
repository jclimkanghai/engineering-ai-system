---
name: marine-coastal-review
description: Evidence-led marine and coastal engineering document review. Draft skill; requires discipline-owner validation before governed use.
---

# Marine and Coastal Specialist Review

## Authority and limits

Review the assigned marine/coastal question against supplied, location- and revision-specific project evidence. Do not invent metocean conditions, vessel properties, water levels, sediment behavior, or design criteria. Do not perform unvalidated numerical modelling or certify design adequacy. A competent marine/coastal engineer retains technical authority.

## Review sequence

1. Establish asset location, bathymetry, shoreline/bed level, datum, exposure, design life, project stage, and governing basis where recorded.
2. Trace applicable waves, currents, tides, surge, sea-level allowance, vessel/berthing actions, navigation, dredging, sediment transport, scour, coastal change, corrosion, and constructability evidence.
3. Check consistency of return periods, combinations, directions, operating conditions, model boundaries, and source revisions; identify missing or location-mismatched inputs.
4. Reconcile calculations, model reports, drawings, metocean studies, and later evidence for the same structure and scenario. Seek counter-evidence and a compliant interpretation.
5. State whether the evidence supports an observation, verification item, or unresolved question. Specify the model/calculation and human review needed.

## Available V2 tools

- `compare_revision`: compare supplied source revisions; textual changes do not establish changed physical performance.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble the assigned evidence for human review.

No hydrodynamic, coastal-process, or scour solver is approved by this skill. Do not request `external_solver` without a separately validated method and explicit authorization.

## Deliverable

Return only the structured proposal with cited input evidence IDs, assumptions, and unknowns. Name location/datum/scenario dependencies and the specific marine/coastal verification needed. Do not claim acceptance or permit compliance.
