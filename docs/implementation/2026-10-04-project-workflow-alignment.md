# Engineering AI project workflow alignment

**Updated 4 October 2026.** This is the governing sequence for the integrated Engineering AI System. The retained project-run service classifies each run before creating V2 tasks. A host must supply the Reviewer, Lead integrator and generaliser adapters; no AI provider is selected automatically.

The [six-tier authority hierarchy](2026-10-04-authority-hierarchy.md) governs the context retrieved by the Lead and every subsequent plan, review, result assessment and human decision. The [knowledge lifecycle](2026-10-04-knowledge-lifecycle.md) defines learning, validation, reuse and independent Reviewer challenge. Organisational lessons are tier 5 context; they do not outrank current project requirements, accepted decisions or evidence.

```text
CLIENT REQUEST
    ↓
PROJECT LEAD (Engineering Document AI)
    ↓ retrieves governed context
PROJECT REGISTRY + VALIDATED ORGANISATIONAL KNOWLEDGE
    ↓
PLAN (objective, scope, evidence, requirements, V2 tasks, acceptance criteria, unknowns)
    ↓
REVIEW GATE 1 — ENGINEERING AND CRITICAL (client/project alignment)
    ↓
HUMAN PLAN APPROVAL — CRITICAL
    ↓
MULTI-V2 EXECUTION (bounded specialist tasks; Routine requires active policy)
    ↓
LEAD INTEGRATION (reconcile outputs, conflicts, gaps and source provenance)
    ↓
REVIEW GATE 2 — ENGINEERING AND CRITICAL (integrated result versus client/project objectives)
    ↓
HUMAN OUTCOME DECISION — CRITICAL, OR WHEN AUTHORITY THRESHOLD REQUIRES IT
    ↓
PROJECT REGISTRY (retain plan, reviews, outputs, integration, decisions and rationale)
    ↓
PROJECT LESSON (experience and accepted outcome)
    ↓
CANDIDATE KNOWLEDGE (project-scoped proposal with source and outcome links)
    ↓
AI GENERALISATION (proposed reusable rule, applicability and limits)
    ↓
HUMAN VALIDATION
    ↓
ORGANISATIONAL KNOWLEDGE (separate governed scope; explicit future-project import)
    ↺ to the Project Lead's next relevant task
```

## Control rules

1. The client request becomes a human-controlled project brief and issue mandate. The Lead retrieves current project records and only active, human-validated organisational lessons explicitly imported into that project. Every reused lesson passes the [relevance, applicability and current-project-requirements check](2026-10-04-authority-hierarchy.md#applicability-gate-for-reused-knowledge). Irrelevant or inapplicable lessons are ignored; partial applicability carries explicit limits; unknown compliance or a reported conflict goes to human review.
2. **Workflow class is bound before V2 execution.** Routine is limited to low-risk, low-importance, reversible, allow-listed work under a current human-approved project policy; it skips both AI Reviewer gates and per-task human authorisation. Engineering uses both Reviewer gates. Critical uses both gates plus mandatory human approval of the complete plan and a human final outcome decision. Unknown or potentially material consequence raises the class or holds execution. A reviewer-unavailable, uncertain or misaligned outcome blocks the relevant Engineering/Critical path.
3. Gate 1 checks the complete plan against the original mandate and project brief before any V2 task is authorised. It needs a current aligned verdict with no unknowns. A revised plan or changed source snapshot requires a fresh review. The Reviewer tests client/project alignment; it does not certify technical correctness.
4. Each V2 task has a bounded tool/method, input snapshot, output contract and source links. Routine task execution is bound to a current human-approved policy, rechecked at execution. Engineering and Critical tasks retain separate human execution authorisation. Independent tasks may run concurrently only where their inputs and side effects are isolated. Task failure or stale inputs cannot be hidden in Lead integration.
5. The Lead integrates all V2 results against the shared objective, reconciles contradictions, retains missing inputs and limitations, and records a distinct integrated assessment. Gate 2 reviews that integrated result and the exact Lead assessment against the original client/project intent.
6. A human decides whenever the authority threshold, materiality, consequence or unresolved uncertainty requires it. Critical always requires a human final decision. A human-approved delegation policy may permit bounded low-risk Engineering result dispositions after required checks; it does not grant design, contractual, regulatory, issue-closure or lesson-promotion authority. Routine process completion is not engineering acceptance.
7. The Registry retains the class, reasons, policy/approval state, applicable reviews, every V2 result, Lead integration, attributed disposition and evidence. Material Lead choices enter the decision graph; all-explicit-no-impact choices stay in the execution log. Project experience becomes a project lesson, then candidate knowledge. AI may propose a generalisation, but it remains project-scoped until a human validates its observation, outcome, interpretation, applicability and limits. Cross-project organisational knowledge is a separate human-controlled promotion and import step, never automatic model training.

## Current implementation against the target

| Stage | Current evidence | Status / gap |
| --- | --- | --- |
| Project brief, mandate and Registry context | Human-controlled brief/mandate and project-scoped task context; explicit organisational promotion/import | Implemented for a defined issue/task. |
| Classification, plan and Gate 1 | Document AI retains each bounded task under a class-bound run plan | Routine requires an active human-approved policy; Engineering/Critical require Reviewer Gate 1. Critical also requires exact-plan human approval. |
| Multi-V2 execution | V2 has bounded tools and host-approved solver adapters | Routine rechecks policy at execution; Engineering/Critical retain separately authorised tasks. ProjectRun validates and freezes dependency graphs, exposes ready batches, binds dependent results to exact predecessor result/assessment digests, and provides bounded dispatch using isolated Registry connections. Discipline specialists use an exact local skill with a discipline/tool manifest and configured analysis adapter; the skill digest and validation state bind into the plan, and governed execution fails closed until discipline-owner validation. Specialist outputs remain proposals for Lead integration and independent review. Parallel dispatch requires explicit concurrent-call capability on configured analysis, specialist-analysis and Reviewer adapters; standard provider adapters use per-thread clients, and workflow Registry lookups use worker-local connections. |
| Lead integration and Gate 2 | Lead adapter reconciles all task outcomes into one retained integration | Engineering/Critical require Gate 2 over the exact integration. Routine records process completion only after passing checks. |
| Human authority | Human result verification/decision and policy-controlled low-risk delegation | Critical requires human plan and outcome gates. Engineering may use accepted delegated low-risk result decisions; material or unresolved authority remains human. |
| Registry and learning | Run outcome, human-approved project lesson, candidate, AI generalisation and separate human organisational validation/promotion | Implemented through `ProjectRunService`. Cross-project import remains a separate human action. |

## Acceptance criteria for full alignment

- A project run retains one client mandate, complete multi-task plan and stable source/lesson snapshot.
- No Engineering/Critical V2 task in a governed run can be authorised without an aligned Gate 1 over that complete plan; Routine requires a current human-approved low-risk policy instead.
- Two or more independently scoped V2 tasks can complete under the plan, and the Lead produces one retained integration that identifies every result, failure, conflict and unknown.
- Dependency edges are validated before task creation; each dependent task waits for assessed, accepted prerequisite evidence, and the Lead/Gate 2 inputs retain the exact binding. Independent tasks are exposed in the same ready batch; actual concurrent dispatch remains a host responsibility.
- A plan assigning more than three distinct discipline specialist agents requires a human decision before any specialist task can be authorised, for Routine, Engineering, and Critical runs. The approval view lists each specialist's discipline, task scope, skill, actual allowlisted tool/version, source access, dependency, and deliverable. Critical runs use their existing human plan approval, bound to the specialist roster; other run classes use the dedicated human decision route. Non-specialist workflow roles do not count toward the threshold. Batch dispatch is capped by the host (default four, maximum sixteen); an unavailable skill or specialist adapter fails the task closed.
- Engineering/Critical Gate 2 binds to the exact integration and Lead assessment; stale or missing reviews cannot support acceptance.
- The result disposition follows the trusted authority threshold. Any delegated low-risk decision is attributed to its human-approved policy; other decisions require a human.
- Candidate knowledge links to the accepted source/result/decision chain; an AI generalisation is retained as a proposal and cannot enter organisational knowledge without separate human validation and promotion.
- Offline tests cover class-specific gates, Routine policy revocation, Critical human approvals, stale inputs, partial V2 failure, contradictory V2 outputs, delegated and human decisions, and blocked lesson promotion. Real engineering quality remains subject to reviewed case evaluation.

The implementation uses the existing Registry SQLite database and project isolation. The host calls `core.project_runs.propose` with the requested class and, for Routine, an active policy ID. Engineering/Critical call `review(..., "plan")`; Critical then obtains exact-plan human approval. Each task is authorised through its class-specific route and executed. The Lead calls `integrate`; Engineering/Critical call `review(..., "outcome")`. Routine may call `complete_routine` for process completion, Engineering may use delegated result decisions where permitted, and Critical calls the human `decide` route. Candidate knowledge remains subject to separate human validation. A held or reworked run needs fresh run and task IDs. Offline tests establish the control path, not engineering design quality.
