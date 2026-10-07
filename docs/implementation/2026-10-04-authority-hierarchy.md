# Engineering AI authority hierarchy

**Governing framework order, agreed 4 October 2026.** Use this order when the Project Lead plans work, V2 receives a bounded task, the AI Reviewer checks alignment, and a human decides. It ranks the authority of a claim; it does not establish that any particular document is current, applicable, authentic or contractually governing.

| Priority | Authority tier | Examples | Control |
| --- | --- | --- | --- |
| 1 | **Mandatory / governing requirements** | Regulations; applicable statutory requirements; contractual/client requirements; governing codes and standards | Establish applicability, issue status and the actual statutory/contractual order. A lower tier cannot waive these requirements. |
| 2 | **Current project requirements** | Design basis; specifications; accepted criteria; scope and constraints | Use the current, controlled project version. Resolve inconsistency with tier 1 through the authorised project process. |
| 3 | **Accepted project decisions** | Human/client decisions; approved deviations; agreed assumptions | Require attributed acceptance and a current record. A decision changes a higher-tier requirement only when the competent authority formally changes or permits it. |
| 4 | **Current project evidence** | Drawings; calculations; studies; correspondence; site information | Use to establish facts, status and compliance. Evidence of an inconsistency triggers review; it does not itself amend a requirement or accepted decision. |
| 5 | **Validated organisational knowledge** | Lessons learned; proven solutions; reusable approaches; known failure modes | Use only if human-validated, active, explicitly imported and applicable to this project. A conflict with tiers 1–4 must be surfaced, never silently resolved in the lesson's favour. |
| 6 | **External / general engineering knowledge** | Published practice; general engineering methods; external references | Use as context or a proposed approach, subject to verification and the higher tiers. It cannot establish a project obligation by itself. |

## Applying the hierarchy

1. Identify the claim and cite its exact source, revision/status, location and project/organisation scope. Classify it as requirement, accepted decision, observed evidence, validated lesson, or external context.
2. Check whether the source is current, applicable and authorised. Within tier 1, use the actual law, contract and incorporated-code relationships; this table does **not** invent a universal precedence among regulations, contract clauses and standards. An incorporated standard can be a binding tier-1 requirement.
3. Compare claims across tiers and record the conflict, effect and controlling source if supported. An approved deviation or new project decision cannot be assumed to override a statutory, contractual or other governing requirement. Escalate where authority or applicability is uncertain.
4. Keep `UNKNOWN / INSUFFICIENT INFORMATION` when the controlling revision, contractual order, client acceptance or technical applicability cannot be established. The AI may recommend a resolution; only the authorised human/client/authority can approve it.

The detailed source-quality lists in `prompts/truth.md`, `prompts/scope.md`, `prompts/contractual.md` and `prompts/compliance.md` help evaluate evidence **within a task**. They do not supersede this six-tier framework or the actual project-specific precedence terms.

## Applicability gate for reused knowledge

Organisational knowledge has two functions: **solution accelerator** and **early-warning system**. A conflict does not establish that the knowledge is wrong. It may mean the lesson is inapplicable, the project intentionally differs, or the lesson points to a possible problem in the current requirement. The current requirement governs unless the competent authority formally changes it.

The [full knowledge lifecycle](2026-10-04-knowledge-lifecycle.md) also requires an independent AI Reviewer check of the Lead's use of organisational knowledge against project requirements, client objectives, accepted decisions, regulations, design basis and constraints. A mismatch produces a retained review comment; the Reviewer does not inherit the Lead's applicability verdict.

Every project lesson or explicitly imported organisational lesson considered for reuse gets a retained review tied to the current project snapshot:

```text
Validated knowledge → Retrieve → Relevant?
  No → Ignore and record not_relevant.
  Yes → Applicable to this task and source context?
    No → Ignore and record not_applicable.
    Yes → Check CURRENT PROJECT REQUIREMENTS and accepted decisions.
      Complies, with current requirement and evidence citations → Propose as a basis.
      Partial applicability, with stated limitations and a compliant cited scope → Use cautiously within those limits.
      Conflict → Explicitly surface both sides; do not directly adopt the knowledge.
        Project deliberately differs → Follow the current requirement.
        Possible concern with the requirement → Raise an EARLY WARNING and check it.
        Lead develops a compliant alternative → Continue within the requirement.
        Lead proposes a deviation → Hold that path for human/client decision.
        No resolution yet → Hold that path and clarify the compliant option.
      Unknown or missing current requirements → Hold reuse and request clarification.
```

Validation in the current implementation checks that each lesson has a review, that reusable `applicable`/`partial` states cite current requirement and evidence IDs, and that `partial` states list limitations. The comparison of free-text engineering meaning remains an AI proposal for reviewer and human judgement; citations do not prove technical compliance. A changed requirement or source snapshot requires a fresh task and review.

For a conflict, the task plan retains a distinct **KNOWLEDGE CONFLICT — Project Requirement** item with the knowledge ID and statement, current requirement ID and text, interpretation, Lead action and deviation-decision flag. A possible concern additionally carries an **EARLY WARNING** statement; it is a question for review, not a finding that the requirement is defective. The review desk displays the item. A compliant alternative keeps the conflict visible without automatically requesting a deviation decision; a proposed deviation remains blocked until the appropriate human/client authority decides it. The AI's claimed compliant resolution still needs the normal Reviewer gates and human technical review.
