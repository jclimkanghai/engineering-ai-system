# P8 Discipline Specialist Skill Catalog

**State:** Draft skills authored; discipline validation is outstanding.

These packs give the bounded specialist proposal step discipline-specific review questions and allowed V2 tools. They do not make the model a licensed or competent engineer, validate a calculation method, or grant authority to accept a design. Governed ProjectRuns reject specialist assignments unless the exact skill manifest is discipline-matched, tool-matched, and marked `validated` with reviewer and validation-reference fields. The skill and manifest digest are frozen into the plan before Gate 1 and human task authorization.

## Role, skill, and V2 tool mapping

| Specialist discipline | Skill ID | Allowed V2 tools | Intended deliverable |
|---|---|---|---|
| Structural | [`structural-review`](../../skills/structural-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Evidence-linked structural review observations, exact component/load-case basis, and required structural calculation/check. |
| Geotechnical | [`geotechnical-review`](../../skills/geotechnical-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Location/profile-specific evidence review, unknown ground inputs, and required investigation or geotechnical calculation. |
| Marine and coastal | [`marine-coastal-review`](../../skills/marine-coastal-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Location/datum/scenario-linked review of marine/coastal basis and required model or calculation. |
| Hydraulic and environmental | [`hydraulic-environmental-review`](../../skills/hydraulic-environmental-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Separate hydraulic and environmental evidence, applicability gaps, and required assessment/authority review. |
| Civil | [`civil-review`](../../skills/civil-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Location/interface-aware civil review, survey/coordination gaps, and required verification. |
| Mechanical | [`mechanical-review`](../../skills/mechanical-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Equipment/tag/duty-specific review, missing vendor/design evidence, and required mechanical check. |
| Electrical | [`electrical-review`](../../skills/electrical-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Circuit/equipment-specific review, missing study basis, and required electrical verification. |
| Quantity surveyor | [`quantity-surveyor-review`](../../skills/quantity-surveyor-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack`, `validate_proposal_readiness` | Traceable quantity/scope basis, omissions/assumptions, and commercial review actions; no rate or price certification. |
| Risk specialist | [`risk-specialist-review`](../../skills/risk-specialist-review/SKILL.md) | `compare_revision`, `validate_traceability`, `generate_review_pack` | Evidence-backed cause–event–consequence statement, control evidence gaps, owner/action, and human risk decision. |

The first three tools are deterministic document-evidence helpers: text comparison, traceability checks, and review-pack assembly. `validate_proposal_readiness` checks recorded proposal completeness only. None establishes engineering adequacy. No `external_solver` is allowed in these draft manifests: the current catalogue has no validated production solver pack.

## Shared specialist contract

Every specialist receives the frozen source snapshot, task, dependency binding, exact V2 tool/version, and exact skill text. It must return the existing proposal schema (`summary`, `recommendation`, `evidence_ids`, `assumptions`, `unknowns`), and every cited evidence ID must belong to the snapshot. It must check revision/object/location applicability, chronology and supersession, strongest compliant interpretation, and counter-evidence before raising a concern. Missing evidence is `UNKNOWN / INSUFFICIENT INFORMATION`; it is not an inferred failure or an exclusion. Recommendations identify calculations, studies, or decisions still needed and remain proposals for Lead assessment, the independent Reviewer, and human engineering decision.

The existing four-distinct-specialist threshold still requires a plan-digest-bound human plan decision before task authorization. Every specialist task also needs its ordinary class-specific authorization; Critical retains human plan and outcome decisions. Human approval of a plan or proposal does not substitute for discipline-owner skill validation or technical verification.

## Validation needed before governed use

For each skill, the relevant discipline owner must review the skill against current project governance and representative test cases. The review should cover correct governing-basis use, same-object/revision checks, counter-evidence and supersession, missing-input escalation, unsupported conclusion avoidance, tool boundaries, and expected deliverables. Record the accountable reviewer and validation reference in `specialist.json` through the repository's reviewed change process. Keep a frozen regression set for updates. A changed skill or manifest changes its digest and requires a fresh plan/review/authorization.

All nine manifests currently say `draft`, with no reviewer or validation reference. Therefore governed ProjectRuns containing these specialist assignments fail closed at plan proposal. P8 dependency orchestration and bounded execution are implemented; operational specialist readiness for these disciplines remains open until the domain-owner decisions and validation evidence are recorded.

The structural package now includes a discipline-owner review suite and decision-record template in [`skills/structural-review/VALIDATION.md`](../../skills/structural-review/VALIDATION.md). It remains draft; its scenarios are validation cases, not passed results. No structural solver has been added or authorized.
