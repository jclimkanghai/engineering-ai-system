# Scope Analysis Mode

## Purpose

Scope Analysis Mode determines what work, deliverables, obligations, interfaces and responsibilities are included within a project or tender scope.

The objective is to establish a defensible answer to:

> **Who is responsible for what, based on the available contractual and technical evidence?**

Scope analysis shall distinguish between:

- explicitly included scope;
- implicitly necessary scope;
- conditional scope;
- excluded scope;
- work by others;
- interface scope;
- optional scope;
- unclear scope; and
- unsupported assumptions.

The analysis must not expand or reduce scope merely because an activity is technically desirable.

---

# 1. Core Principle

> **Scope is determined from evidence, not from what the reviewer thinks should be included.**

The reviewer shall distinguish between:

### Contractual requirement

An obligation explicitly stated in the contract, RFP, specification, scope, drawing, bulletin, clarification or other governing document.

### Necessary execution activity

An activity reasonably necessary to complete an explicitly required work item.

### Engineering responsibility

A responsibility that follows from the allocated design, construction, testing, commissioning or operational obligation.

### Contractor means and methods

The contractor's responsibility for determining how an obligation will be executed.

### Interface responsibility

An obligation requiring coordination or physical/technical integration between two or more parties or scopes.

### Recommendation

A technically desirable activity that is not established as a contractual obligation.

---

# 2. Scope Classification

Every scope item shall be classified as one of:

| Classification | Definition |
|---|---|
| `in_scope` | Explicitly or sufficiently established as the party's responsibility |
| `out_of_scope` | Explicitly excluded or allocated elsewhere |
| `by_others` | Responsibility explicitly assigned to another party |
| `interface` | Shared or coordinated responsibility between parties |
| `optional` | Included only if an option, condition or instruction is exercised |
| `unclear` | Evidence is insufficient to determine responsibility |

Do not use `unclear` merely because the reviewer has not yet searched the relevant documents.

---

# 3. Scope Evidence Hierarchy

Apply the [governing six-tier authority hierarchy](../docs/implementation/2026-10-04-authority-hierarchy.md) first. When determining scope, consider the following sources for evidence and the actual contract hierarchy. This list does not promote project evidence or general practice above a governing requirement:

1. Contract conditions and executed contractual documents
2. Contract amendments / addenda
3. Tender bulletins and formal clarifications
4. Employer's Requirements
5. Scope of Work
6. Technical Specifications
7. Approved schedules / responsibility matrices
8. Drawings
9. BOQ / pricing schedules
10. Referenced standards
11. Design reports and supporting technical documents
12. Correspondence and meeting minutes
13. Industry practice
14. Engineering judgement

The actual contractual precedence clause shall override this generic hierarchy.

---

# 4. Revision Control

Before determining scope:

1. Identify the latest applicable revision.
2. Check tender bulletins.
3. Check formal clarifications.
4. Check addenda.
5. Check revised drawings.
6. Check revised BOQ.
7. Check superseded documents.
8. Identify whether responsibility has changed.

A scope conclusion based on a superseded document shall not be treated as current unless the governing contract explicitly preserves that requirement.

---

# 5. Scope Decomposition

Do not analyse scope only at high-level work-package level.

Break the scope into logical components.

Example:

```text
Jetty Reconstruction
│
├── Design
│   ├── Structural design
│   ├── Geotechnical design
│   ├── Marine engineering
│   ├── Temporary works
│   └── Authority submissions
│
├── Procurement
│   ├── Steel piles
│   ├── Fenders
│   ├── Bollards
│   ├── Structural steel
│   └── Cathodic protection
│
├── Construction
│   ├── Site preparation
│   ├── Marine piling
│   ├── Rock socketing
│   ├── Concrete works
│   ├── Steel erection
│   └── Dredging
│
├── Testing
│   ├── Pile testing
│   ├── Welding inspection
│   ├── Coating inspection
│   └── Functional testing
│
└── Completion
    ├── As-built drawings
    ├── Authority clearance
    ├── Testing records
    └── Handover documentation
