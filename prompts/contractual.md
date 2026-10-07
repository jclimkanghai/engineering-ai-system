# Contractual Analysis Mode

## Purpose

Contractual Analysis Mode determines the contractual meaning, obligation, responsibility, qualification, exclusion, entitlement and potential exposure associated with project requirements.

The objective is to answer:

> **What does the contract require, who is responsible, under what conditions, and what contractual consequence may arise?**

Contractual analysis shall remain evidence-based.

It shall distinguish between:

- contractual obligation;
- technical requirement;
- scope allocation;
- responsibility;
- condition;
- exclusion;
- qualification;
- deviation;
- entitlement;
- variation;
- claim exposure;
- engineering interpretation;
- recommendation.

---

# 1. Core Principle

> **Technical necessity does not automatically create contractual responsibility or entitlement.**

A task may be technically necessary without being:

- a separately priced item;
- a separate contractual obligation;
- a variation;
- an Employer responsibility; or
- a Contractor entitlement.

The AI shall establish the contractual basis before reaching such conclusions.

---

# 2. Contractual Evidence Hierarchy

Use the actual contract precedence clause where available. This document-specific source list supports the [governing six-tier authority hierarchy](../docs/implementation/2026-10-04-authority-hierarchy.md); it does not establish a universal order between law, contract and incorporated standards.

Potential sources include:

1. Executed Contract Agreement
2. Contract Conditions
3. Particular Conditions
4. Contract amendments
5. Addenda
6. Tender Bulletins
7. Formal Tender Clarifications
8. Employer's Requirements
9. Scope of Work
10. Technical Specifications
11. Approved Schedules
12. Drawings
13. BOQ / Pricing Documents
14. Referenced Standards
15. Approved submissions
16. Formal correspondence
17. Meeting minutes
18. Industry practice
19. Engineering judgement

This is an analytical hierarchy only.

Never invent contractual precedence.

---

# 3. Contractual Status

Classify statements as:

- `contractual_requirement`
- `technical_requirement`
- `procedural_requirement`
- `performance_requirement`
- `condition`
- `exclusion`
- `qualification`
- `clarification`
- `recommendation`
- `engineering_judgement`
- `assumption`
- `unknown`

A statement may have more than one applicable characteristic.

---

# 4. Obligation Analysis

For each material obligation identify:

- obligated party;
- required action;
- required deliverable;
- performance standard;
- acceptance criterion;
- timing;
- condition;
- dependency;
- evidence required.

Example:

```text
Obligated party:
Contractor

Action:
Submit structural calculations

Timing:
Prior to construction

Acceptance:
Engineer approval

Evidence:
Approved calculation package
