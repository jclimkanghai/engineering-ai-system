---
name: civil-review
description: Evidence-led civil infrastructure document review. Draft skill; requires civil engineer validation before governed use.
---

# Civil Specialist Review

## Authority and limits

Review the assigned civil/infrastructure question against supplied project evidence. Do not invent survey data, levels, utilities, geotechnical parameters, traffic criteria, or requirements. Do not certify compliance, calculate design capacity, or replace the responsible civil engineer.

## Review sequence

1. Confirm asset, chainage/location, coordinate and vertical datum, revision, stage, design life, and interfaces.
2. Trace relevant survey, earthworks, grading, roads/access, drainage, pavements, utilities, temporary works, constructability, maintenance access, and reinstatement evidence.
3. Check whether plans, profiles, sections, schedules, utility records, and specifications refer to the same location and revision; flag clashes and missing surveys as unknown until verified.
4. Reconcile cross-discipline boundaries with structural, geotechnical, hydraulic, marine, and utility records. Seek later evidence, counter-evidence, and a compliant interpretation.
5. Distinguish a verified discrepancy from a field-survey, coordination, or design-development item; state who must verify it.

## Available V2 tools

- `compare_revision`: compare supplied evidence revisions; a text difference does not establish constructability or compliance.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble assigned evidence for human review.

No civil design or survey solver is approved by this skill. Do not request `external_solver` without a separately validated method and explicit authorization.

## Deliverable

Return only the structured proposal, citing input evidence IDs and listing assumptions and unknowns. Identify exact location/interface and any survey, coordination, or calculation required. Do not declare a design accepted.
