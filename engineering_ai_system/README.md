# Engineering AI System

Engineering AI System is the user-facing core that assembles the framework's existing packages. It does not replace their ownership or create a second registry.

The latest framework blueprint is maintained at [`docs/implementation/2026-10-01-latest-framework-blueprint.md`](../docs/implementation/2026-10-01-latest-framework-blueprint.md).

The [governing project workflow](../docs/implementation/2026-10-04-project-workflow-alignment.md) classifies each run as Routine, Engineering or Critical. Routine uses an active human-approved low-risk execution policy and skips both AI Reviewer gates. Engineering uses both gates; Critical also requires human approval of the complete plan and a human outcome decision. `core.project_runs` retains these stages in the project Registry. Host execution mode defaults to `GOVERNED`; `TEST` and `DEMO` must be selected explicitly. Governed use requires a Reviewer for Engineering/Critical runs; the host supplies a run integrator and generaliser when those operations are needed.

The [six-tier authority hierarchy](../docs/implementation/2026-10-04-authority-hierarchy.md) orders mandatory/governing requirements, current project requirements, accepted project decisions, current project evidence, validated organisational knowledge and external/general engineering knowledge. It guides planning and review; Registry source-control records and human judgement still determine actual applicability and precedence.

```text
Engineering Document AI (brain)
    plans the task ─────────────── assesses V2 output
            │                              ▲
            ▼                              │
Engineering Registry (memory + control) ◀─┤ records result, decision and learning
            │                              │
            └── authorized task ──► Engineering AI V2 (execution)
                                           │
Independent alignment review + human verification and decision (standard route)
                                           │
                                           └────────────► Registry
```

The `engineering-ai-system` host wheel contains the user-facing facade plus the Document AI host pipelines. It assembles four independent wheels: `engineering-document-ai-brain` plans tasks and assesses execution results, `engineering-execution` runs bounded V2 tools, `engineering-registry` retains memory and enforces control, and `engineering-ai-reviewer` performs the two automated alignment gates. The Registry remains the single source of truth. The existing loopback review desk and MCP server remain the user/application interfaces.

The [framework role map](../docs/implementation/2026-10-01-latest-framework-blueprint.md#the-framework-in-plain-language) treats Document AI as the Lead for a defined task, V2 as bounded specialist execution, and the Reviewer as a separate **client-alignment** role. This is workflow separation only: the host may supply the same model/provider for Lead and Reviewer, and no technical design correctness is certified by Reviewer alignment. Registry holds project-scoped knowledge and a separately governed organisational layer; human engineers retain technical authority and control material decisions. New ProjectRun MCP plans explicitly classify each task's discipline specialist assignment (or declare `null`). More than three distinct discipline specialists require human approval before task authorization; the approval request is bound to the plan digest and shows discipline, scope, skill, actual tool/version, source access, dependencies and deliverables. Conditional approval stays blocked until a human separately confirms every condition. Human-only decision routes are exposed through the host `EngineeringAISystem.decide_specialist_plan` and `confirm_specialist_plan_conditions` APIs, not AI-callable MCP tools. Critical runs use their existing human plan approval, which also binds the specialist roster.

## Pilot operating boundary

The current product target is a local supervised pilot for an engineering reviewer. The Lead, Reviewer and discipline specialist outputs are proposals; a responsible human engineer must validate technical conclusions. The Reviewer checks project/client alignment and is not a second technical engineer. Specialist skills and allowed-tool manifests constrain requested work, but a prompt/skill is not a qualified discipline agent or validated calculation method. Governed ProjectRuns reject draft specialist packs; human validation of the exact skill digest and independent benchmark evidence are required before use.

This local setup uses host-supplied Registry identity and project grants. The local desk records the configured reviewer ID but does not independently authenticate the person; local session protection is not individual authentication or non-repudiation. The standard MCP is local stdio with launcher-fixed grants, not a remote multi-user service. Before live model analysis, the operator must review which project evidence and context will leave the host for the configured provider. `store=False` is a request setting, not a complete data-governance or egress policy.

The host wheel preserves Python import paths such as `pipelines.*`, `agents.*`, and `engineering_ai_system.*`. Installers that previously named the `engineering-document-ai` distribution should uninstall that distribution before installing `engineering-ai-system`; the old and new distributions otherwise overlap in ownership of `pipelines` files.

## Start a project core

The host application must authenticate the user and supply the trusted Registry `Principal`. A governed facade can open without a reviewer callback for Routine work under an active human-approved policy. Engineering and Critical runs still require a configured Reviewer at both gates: a missing Reviewer blocks Gate 1 and task authorisation, or Gate 2 and acceptance. Standalone facade tasks also require a Reviewer before authorisation or execution; the exception is limited to retained, policy-bound Routine runs. No AI provider or policy is selected automatically.

```python
from pathlib import Path
from engineering_ai_system import EngineeringAISystem
from engineering_ai_reviewer import build_alignment_reviewer
from engineering_registry.service import Principal

# reviewer_adapter is supplied by the authenticated host application.
reviewer_callback = build_alignment_reviewer(reviewer_adapter)
core = EngineeringAISystem.open_project(
    Path.home() / "Engineering AI System Data" / "registry.sqlite3",
    "MY-PROJECT",
    Principal("authenticated-user-id", frozenset({"MY-PROJECT"})),
    create=True,
    alignment_reviewer=reviewer_callback,
)
```

`open_project` requires the exact Registry database path. It fails if the file is missing unless `create=True` is explicit, and binds the database to one project so another project or organisation cannot be written into it. `open_project_from_data_root` resolves `<data-root>/projects/<project-id>/registry.sqlite3`; an optional organisation grant opens `<data-root>/organizations/<organization-id>/registry.sqlite3`. Existing shared Registries are migrated with `engineering-registry-migrate --source ... --data-root ... --dry-run`, then `--apply`; the source is retained. `OrganizationalKnowledgeService` performs explicit human-controlled promotion/import across the separate stores and preserves source snapshots and digests. Use the framework's documented Registry, brain and execution APIs for controlled operations; the facade delegates authority checks to those packages.

For controlled tests, pass `mode="TEST"`. This is an explicit mode and is retained on each new project run. Governed use defaults to `mode="GOVERNED"` and requires the configured alignment reviewer for Engineering/Critical gates. `DEMO` additionally requires every analysis/reviewer adapter to declare `synthetic_only=True`; synthetic results never establish engineering acceptance. Configured reviewers must return valid evidence-bound Gate 1 and Gate 2 reports; unavailable, uncertain or misaligned reviews block the corresponding authorization or acceptance path.

During execution planning, Document AI can extract evidence- and requirement-cited material task decisions. Registry labels them as AI-lead proposals and assigns controls from D0–D4: D0/D1 remain informational, D2 require human review, D3 require review and acceptance, and D4 additionally requires a formal authority reference. Requirement links are validated against the issue and retained as graph links. The local human review desk can accept or reject reviewable proposals against the exact proposal digest; decisions remain separately attributed, and a human review cannot be silently overwritten. A task does not stale its own input snapshot with its newly proposed decisions.

Later task context includes decisions from the same issue and prior decisions explicitly linked to shared evidence or requirements, with the matching source IDs shown for cross-issue context. Document AI is instructed to cite incompatible prior decisions and leave resolution to the human reviewer.

The local review desk also supports deliberate organisational learning: a granted human reviewer can promote project lessons into a separate `ORG:<id>` partition with explicit applicability, limitations, source snapshots and a review date. Another project sees none of that knowledge until a human explicitly imports it. Imports and lifecycle changes are digest-bound, attributed and recorded; suspended, retired, superseded or due knowledge is excluded from future task context. Launch the desk with explicit `--project` and `--organization` grants.

For physically separated stores, construct `OrganizationalKnowledgeService` from a granted project `RegistryService` and its organisation `RegistryService`. It retains the same human-only promotion/import boundaries while copying explicit source snapshots across databases. The facade's `project_runs.validate_candidate` uses its configured organisation Registry, rebinding both services to the deciding human's identity and grants. An isolated project Registry requires that separate store for approval; rejection needs no organisational write. Promotion and candidate updates roll back together on operation errors, but separate SQLite database commits are not crash-atomic.

The bounded project-run MCP tools are exposed through `EngineeringAISystem.create_mcp_server(identity_resolver)`, which binds this system's `ProjectRunService` and requires host-verified identity. The operations include `propose_project_run`, `get_project_run`, `get_project_run_specialist_output`, `request_project_run_gate1`, `execute_project_run_task`, `dispatch_project_run_ready_tasks`, `integrate_project_run`, and `request_project_run_gate2`. Batch dispatch returns task and artifact IDs rather than full results, defaults to four workers, accepts a maximum of sixteen, opens an isolated Registry connection per worker, and executes only already-authorised ready tasks. Dependent tasks are released only after current predecessor results and assessments are retained; a failed task holds its dependents while independent tasks continue. The host's analysis, specialist-analysis and Reviewer adapters must support concurrent calls when more than one worker is enabled.

A discipline task requires `skills/<skill-id>/SKILL.md`, a matching `specialist.json` discipline/tool manifest, and a configured specialist analysis adapter (or the host's existing `brain.analysis` adapter). This adapter may be the same model as the Lead. Governed ProjectRuns reject draft/unvalidated skill manifests before task records are created. The exact skill, manifest digest, validation status/reference, task/source digests, specialist proposal and bound V2 tool/version are retained in the plan and Registry. Changing the skill after planning requires a fresh plan, review, and authorization. The specialist response is proposal-only and its evidence IDs are checked against the task snapshot. ProjectRun status lists output references; `get_project_run_specialist_output` returns one integrity-checked proposal, and Lead/Gate 2 receive it during integration. It does not verify engineering adequacy, replace either Reviewer gate, or constitute human acceptance. The current discipline-specific packs are drafts; see the [P8 specialist skill catalog](../docs/implementation/P8_SPECIALIST_SKILL_CATALOG.md) for tool scope and human validation requirements.

The lower-level `engineering_registry.mcp_server.create_server` accepts an explicitly injected service; it does not advertise run tools when the host has not configured orchestration, and it exposes batch dispatch only when the host supplies a dispatcher callback. The MCP surface does not expose the legacy single-task proposal/execution tools; bounded `ExecutionService` methods remain available to trusted host integrations, with final acceptance and human-only decisions separate. Critical plan approval, final human outcome decisions and knowledge validation are not MCP tools. Gate 2 is bound to the retained P0 integrated-assessment ID and digest.

When the Lead receives approved lessons, imported organisational lessons appear first in its analysis context. Planning and assessment must retain one structured review per reused project or imported organisational lesson, with its ID, relevance/applicability status, current-project-requirements check, rationale, limitations and citations. Missing reviews and uncited claims of compliance or conflict are rejected. A reported conflict or uncertain applicability holds the engineering assessment for human review. The AI still judges the meaning of free-text engineering evidence; these controls do not establish that every real conflict will be detected.

The [knowledge applicability gate](../docs/implementation/2026-10-04-authority-hierarchy.md#applicability-gate-for-reused-knowledge) now requires a review for every retrieved project or imported organisational lesson. It distinguishes irrelevant, inapplicable, applicable, partially applicable, conflicting and uncertain items. Reuse requires a `complies` check with current requirement and evidence citations; partial use retains explicit limitations. Conflict and uncertainty hold reuse for human resolution.

An explicit **KNOWLEDGE CONFLICT — Project Requirement** item is retained in the task plan and shown in the review desk. It cites the lesson and current requirement, states why direct adoption is blocked and records the Lead action. A compliant alternative can continue through the normal review gates; only a proposed deviation triggers a specific human/client deviation decision. Reviewer alignment and human technical responsibility still apply.

Organisational knowledge also serves as an early-warning system. A conflict can mark an inapplicable lesson, a deliberate project difference or a possible concern in the requirement. The last case is visibly flagged for checking; it does not automatically invalidate the project requirement, which remains governing unless formally changed.

The [governing knowledge lifecycle](../docs/implementation/2026-10-04-knowledge-lifecycle.md) retains a project candidate, a separate AI generalisation proposal, human validation and organisational promotion after an accepted multi-task run and a human-approved project lesson. Preferred Lead retrieval and independent Reviewer challenge remain subject to the applicability controls described above.

When the task plan includes imported organisational knowledge, Gate 1 and Gate 2 each require a separate `knowledge_applicability` finding. The Reviewer challenges the Lead's lesson use against current project requirements, client objectives, accepted decisions, regulations, design basis and constraints. Its conflict comments are retained in Registry and surfaced in the review desk. This is a second review layer, not technical certification.

## Current boundaries

- Existing source data is not migrated or copied by this package.
- A human principal authorizes new execution. The standard route requires human result verification and decision; a separately approved policy may delegate bounded low-risk dispositions.
- The unified facade defaults to GOVERNED mode and requires an independent alignment reviewer. TEST and DEMO require explicit mode selection; DEMO requires synthetic-only adapters. Direct Registry and execution APIs retain their own control contracts.
- AI outputs are not human verification or engineering approval.
- MCP and the review desk continue to use their existing framework implementations.
- Decision-level classification and materiality extraction are AI-assisted proposals; the human remains responsible for review. There is no automatic resolution of conflicts between formal authority classes.
- Organisational knowledge is not model training or automatic cross-project learning. It is controlled Registry context, with explicit promotion and per-project import.
- P12 assurance output is advisory. Its `PASS`/`HOLD`/`FAIL` status does not authorize deployment or human engineering acceptance. Snapshot digests and provenance fields are checked for internal consistency, not cryptographically authenticated against an identity or trusted data source.
