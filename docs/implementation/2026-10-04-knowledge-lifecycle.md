# Engineering AI knowledge lifecycle

**Governing principle, 4 October 2026.** Validated organisational knowledge is the Project Lead's preferred source of **prior engineering experience**. It is never a substitute for the current project's [authority hierarchy](2026-10-04-authority-hierarchy.md) or a current governing requirement.

```text
PROJECT EXPERIENCE
    ↓
PROJECT LESSON
    ↓
CANDIDATE KNOWLEDGE
    ↓
AI GENERALISATION (proposed pattern, applicability, limits, failure modes)
    ↓
HUMAN VALIDATION
    ↓
VALIDATED ORGANISATIONAL KNOWLEDGE
    ↓
PREFERRED RETRIEVAL BY PROJECT LEAD
    ↓
PROJECT APPLICABILITY CHECK
    ↓
REQUIREMENT COMPATIBILITY CHECK
    ├─ COMPATIBLE → USE / ADAPT within stated limits
    └─ CONFLICT → HIGHLIGHT → LEAD + INDEPENDENT REVIEWER ASSESSMENT
                                 ↓
                         HUMAN/CLIENT DECISION where required
    ↓
RECORD PROJECT OUTCOME
    ↓
NEW PROJECT LESSON
    ↺
```

Organisational knowledge serves two functions: **solution accelerator** and **early-warning system**. A conflict may mean the lesson is inapplicable, the project deliberately differs, or experience points to a possible concern in the requirement. Record and assess that information. Do not silently prefer the lesson or declare it wrong. The current requirement governs until formally changed. A separate human/client deviation decision is needed only if a deviation is proposed; ordinary human technical review and the two AI Reviewer gates still apply.

## Retained controls

- The project outcome and lesson keep their project ID, governing source/revision, accepted decision, V2 result, Lead assessment, reviewer comment, and human actor. A candidate generalisation cites the source project outcomes and states observation, proposed reusable rule, applicability, limitations, contrary evidence and known failure modes.
- AI generalisation is a **proposal**, never organisational knowledge by itself. A granted human validates its evidence, interpretation, cross-project relevance, limits and expiry/review date before promotion into the separate organisational scope.
- The Lead retrieves active, validated and explicitly imported organisational knowledge before other prior lessons. It reviews every lesson for relevance, project applicability and compatibility with current requirements; `partial` use states limitations. A project-specific conflict is retained as a distinct item, with an early-warning flag when the requirement itself may need checking.
- The independent Reviewer checks the Lead's knowledge use against project requirements, client objectives, accepted decisions, regulations, design basis and constraints at Gate 1 and Gate 2. A mismatch produces a retained **REVIEW COMMENT** with cited project evidence. Reviewer alignment does not certify technical correctness.
- After the project decision, the actual outcome feeds a new project lesson. Organisational promotion remains a separate human-controlled action; no automatic model training or cross-project transfer occurs.

## Current implementation boundary

| Stage | Status |
| --- | --- |
| Human-approved project lesson and validated organisational promotion/import | Implemented with project/organisation scoping and provenance. |
| Distinct candidate-knowledge record and AI generalisation before validation | Implemented as separate retained `candidate` and `proposed` states, linked to an accepted run and human-approved project lesson. |
| Preferred retrieval and per-lesson relevance/applicability/requirements check | Implemented for bounded Document AI tasks; the semantic judgement remains AI-proposed and requires review. |
| Independent Reviewer knowledge-applicability criterion and review comment | Implemented when a task plan contains lesson context; live reviewer quality is unverified. |
| Project-wide multi-V2 integration and new lesson generated from that integrated outcome | Implemented in `ProjectRunService`; human run acceptance and lesson promotion are explicit. |
