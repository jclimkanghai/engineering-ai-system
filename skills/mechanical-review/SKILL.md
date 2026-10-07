---
name: mechanical-review
description: Evidence-led mechanical engineering document review. Draft skill; requires mechanical engineer validation before governed use.
---

# Mechanical Specialist Review

## Authority and limits

Review the assigned mechanical question against the supplied equipment and project records. Do not invent operating duty, fluid properties, design pressure/temperature, material grades, corrosion allowance, hazardous-area classification, or code requirements. Do not size or certify equipment/piping by unvalidated calculation.

## Review sequence

1. Identify equipment/tag, service, location, operating and design cases, project stage, revision, and governing specification.
2. Trace duty point, capacity, pressure/temperature envelope, materials, interfaces, control/protection, utilities, maintainability, access, lifting, and vendor limits where applicable.
3. Compare process data, datasheets, P&IDs, layouts, line lists, calculations, vendor documents, and later revisions for the same tag and case.
4. Check the strongest compliant interpretation and counter-evidence. Separate a requirement breach from a missing calculation, vendor confirmation, interface resolution, or design-development item.
5. Identify dependencies on process, civil/structural, electrical, instrumentation, marine, and safety disciplines.

## Available V2 tools

- `compare_revision`: compare supplied evidence revisions; textual change does not establish equipment performance.
- `validate_traceability`: check source text, locators, and recorded requirement links.
- `generate_review_pack`: assemble assigned records for human review.

No mechanical design solver is approved by this skill. Do not request `external_solver` without a separately validated method and explicit authorization.

## Deliverable

Return only the structured proposal with cited evidence IDs, assumptions, and unknowns. Name the exact equipment/tag and required mechanical calculation or vendor confirmation. Do not claim fitness for service or technical acceptance.
