# Compliance Analysis Mode

## Purpose

Compliance Analysis Mode determines whether a requirement, obligation, design feature, submission, document or deliverable satisfies the applicable requirement based on traceable evidence.

The objective is to answer:

> **Has the applicable requirement been demonstrably satisfied, and what evidence supports that conclusion?**

Compliance analysis shall never equate:

- requirement identified;
- requirement acknowledged;
- requirement addressed; and
- requirement demonstrated as satisfied.

These are different states.

---

# 1. Core Principle

> **No evidence, no confirmed compliance.**

A requirement shall only be classified as compliant when sufficient evidence demonstrates that the applicable requirement has been satisfied.

Where evidence is incomplete, the system shall not automatically conclude non-compliance.

Instead distinguish between:

- compliant;
- partially compliant;
- non-compliant;
- not demonstrated;
- not applicable;
- unclear;
- pending verification.

---

# 2. Compliance Status

Use the following standard classifications:

| Status | Definition |
|---|---|
| `compliant` | Requirement is satisfied and supported by adequate evidence |
| `partially_compliant` | Requirement is only partly satisfied or evidence covers only part of it |
| `non_compliant` | Available evidence demonstrates that the requirement is not satisfied |
| `not_demonstrated` | Requirement may be satisfied, but sufficient evidence has not been provided |
| `not_applicable` | Requirement has been established as not applicable |
| `unclear` | Applicability, interpretation or requirement cannot presently be determined |
| `pending_verification` | Compliance depends on an outstanding test, approval, document or verification |

Do not use `non_compliant` merely because evidence is missing.

---

# 3. Compliance Evidence Hierarchy

Use the strongest available evidence, after identifying the applicable requirement under the [governing six-tier authority hierarchy](../docs/implementation/2026-10-04-authority-hierarchy.md). The list below concerns evidence of compliance, not authority to change a requirement.

Typical evidence hierarchy:

1. Approved authority approval / permit
2. Contractual requirement and formal acceptance
3. Approved design / construction document
4. Approved calculation / design report
5. Inspection / test / commissioning record
6. Certified material / manufacturer documentation
7. Survey / measurement / monitoring record
8. Formal correspondence / clarification
9. Contractor submission
10. Engineering analysis
11. Engineering judgement
12. Assumption

The applicable hierarchy may differ by project or discipline.

Always identify the governing requirement and evidence source.

---

# 4. Requirement-to-Evidence Mapping

Every compliance assessment shall establish:

```text
Requirement
    ↓
Applicable criterion
    ↓
Evidence required
    ↓
Evidence available
    ↓
Assessment
    ↓
Compliance status
    ↓
Action
