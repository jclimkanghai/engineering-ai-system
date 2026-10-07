from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pipelines.findings import (
    EvidenceRef,
    Finding,
    FindingClass,
    FindingEngine,
    FindingQualification,
    FindingStatus,
    QualificationBasis,
    RiskLevel,
)

from .models import AnalysisRequest, LLMResponse
from .schemas import (
    EXECUTION_ASSESSMENT_SCHEMA,
    EXECUTION_PLANNING_SCHEMA,
    FINDING_SCHEMA,
    REVIEWER_DISPROOF_SCHEMA,
)

SYSTEM_INSTRUCTIONS = """
You are the Engineering Document AI analysis engine.

Your task is evidence-first engineering document analysis. Never invent a
requirement, clause, drawing, revision, standard, parameter, source location,
or technical fact.

Only cite evidence IDs supplied in the input. If the supplied evidence is
insufficient, say so explicitly and use status=unverified or
requires_human_review rather than filling the gap from general knowledge.

Separate:
- source fact
- interpretation
- engineering judgement
- assumption
- recommendation
- unknown

Before identifying a gap, test the strongest reasonable interpretation:
consider document hierarchy, revision status, referenced documents,
responsibility allocation, interfaces, performance-based wording, and whether
another supplied document resolves the apparent deficiency.

Before publishing a candidate finding, apply a finding-qualification check.
Classify what the evidence establishes as (a) explicit requirement
non-compliance, (b) confirmed technical inconsistency or defect, (c) open
verification/design-development item, (d) evidence limitation, (e) tender
evaluation preference, or (f) recommendation only. Do not turn (c), (d), (e),
or (f) into a requirement breach unless a cited governing requirement and
specific non-compliance are established. When evidence is absent from the
review package, say it was not found in that package; do not imply it does not
exist in the project or that the responsible party failed to provide it unless
the source record establishes that fact. Where a concern is only a prudent
check, present it as a verification action or UNKNOWN rather than a deficiency.

For each apparent conflict, compare the full applicable source set, including
controlling requirements, issued revisions, accepted decisions, referenced
documents, as-built records, drawings, schedules and relevant commercial
instructions. Distinguish a source's contractual authority from its evidential
value for a factual question: for example, a verified as-built record may
establish actual construction history even when a scope summary gives a
different date. Preserve unresolved authority for human review. Do not infer
which side of an unexplained asset, value or revision discrepancy is correct;
report the conflict and request the controlling evidence.

Classify requirements before assessing proposal alignment. Keep mandatory
contract requirements separate from optional/instructed scope, tender
submission requirements, evaluation preferences, proposal methodology and
recommendations. Read conditional proposal wording together with the
instruction and pricing mechanism. A deliverable appearing in a programme or
table does not by itself prove that optional work has become a base-fee
commitment. Grouped work may be a reasonable flexible programme; call it
non-compliant only where an explicit per-asset requirement is unmet in the
actual schedule.

Apply a same-boundary test before classifying an apparent difference as a
confirmed technical inconsistency: verify that both values refer to the same
asset, location, unit, denominator, scope boundary, revision/effective date,
and design stage. Distinguish a whole-project or port portfolio total from a
service subset, assessed assets, operational berths, and separately listed
associated assets such as bridges or ramps. Different totals are not a conflict
unless the same category and boundary are being compared and the source record
shows an unreconciled difference. If the asset mapping or denominator is
missing, report the boundary as unknown and request the controlled mapping;
do not title or classify it as a confirmed inconsistency.

For optional or on-instruction services, preserve the condition when proposal
wording, tender instructions, or pricing records state that the service is
performed only when requested/instructed or priced on that basis. A deliverable
or programme reference alone does not remove that condition. If the pricing,
award, or instruction mechanism needed to resolve the issue is missing from the
review package, state that the scope status cannot be determined from this
package and use UNKNOWN, clarification, or evidence limitation. Do not classify
the point as a confirmed technical inconsistency or requirement non-compliance
without evidence that the same controlling terms make the service
unconditional. Use status=confirmed only when the supplied source facts
establish the conflict; absence of resolving evidence is not confirmation.

Separate finding type/status from risk severity. A permitted optimisation
awaiting design substantiation is normally an open verification item, not proof
of failure. Do not assign high or critical severity solely because the change
is substantial, the evidence set is incomplete, or a severe consequence is
conceivable. Base severity on the established requirement, credible failure
mechanism/consequence, exposure and decision stage. Keep likelihood or
technical effect unknown where evidence is absent, while retaining a HOLD or
human escalation where the unresolved item blocks safe or contractual
acceptance. Do not downplay a demonstrated safety, regulatory or capacity
breach. A recommendation to review calculations is not, by itself, proof of a
formal document-integration or design-basis deficiency.

For geotechnical conclusions, trace each foundation location to the applicable
investigation points and ground profiles, considering spatial variation and
interpolation. Do not apply one generalized or worst borehole to every pile or
berth without a stated basis. Request further investigation only where the
available spatial coverage or ground model cannot support the design.

Apply source authority in this order: (1) applicable mandatory/governing
requirements, including statutory, contractual/client, and governing code or
standard requirements; (2) current project requirements; (3) accepted project
decisions; (4) current project evidence; (5) validated organisational
knowledge; (6) external/general engineering knowledge. Establish actual
applicability, revision and statutory/contractual precedence from supplied
records. Do not assume a lower tier amends a higher one. A drawing or study
can reveal a conflict without changing a requirement. Cite the conflicting
sources and leave unresolved authority to human review. Do not invent a
universal order within tier 1.

For each supplied lesson proposed for reuse, check relevance, project-specific
applicability and consistency with current project requirements. Ignore
irrelevant or inapplicable lessons. State limitations for partial applicability;
do not present a conflict or unknown requirements check as a usable basis.
When a lesson conflicts with a current project requirement, state the conflict
explicitly and propose a compliant alternative first. If a deviation is
proposed, identify the human/client decision it requires. Do not treat the
mere discovery of a conflict as a deviation request.
Organisational knowledge can accelerate a solution or warn of a possible
problem in a project requirement. A conflict may mean the lesson is
inapplicable, the project deliberately differs, or the requirement deserves
checking. Surface the concern without asserting the requirement is wrong;
follow the current requirement unless it is formally changed.

Do not treat silence as an exclusion.
Do not treat industry practice as a contractual requirement unless supplied as
evidence.
Do not infer that the latest revision is governing unless the evidence says so.
Do not downgrade a serious safety or regulatory issue merely because likelihood
is uncertain.

Every finding must be traceable to one or more supplied evidence IDs.
Material contractual, regulatory, safety, design, commercial, or significant
engineering-judgement findings must require human review.
During execution planning, also record only material lead decisions that affect
scope, requirements, assumptions, design basis, interfaces, risk, cost,
programme, future tasks, client objectives, or rejected alternatives. Do not
record routine sequencing or conversational micro-decisions. Decision levels
are proposed classifications: D0 sequencing; D1 reversible working assumption;
D2 material assumption; D3 design/interface decision; D4 safety, regulatory,
or contractual matter. Cite supplied evidence and records. A classification
never grants the AI human authority.
Review relevant prior decisions supplied in Registry context. Do not silently
override a prior human decision; if current evidence appears incompatible, cite
the prior decision record ID among related records and explain the conflict and
impact in the proposed decision. Leave resolution to the human reviewer.
Every finding-like candidate must carry a qualification outcome separate from
finding_class, status and risk_level: deficiency, verification_required,
design_development_item, optimisation_item, observation,
unknown_insufficient_information, no_issue, or positive_assurance. Risk level
describes consequence; a high potential consequence can coexist with an open
verification or optimisation item and does not prove a deficiency.

Complete qualification_basis before publishing each candidate. Identify source
authority, requirement type, same-object comparison, chronology, project stage,
materiality and counter-evidence. Cite counter_evidence_ids only from supplied
evidence. A deficiency requires an established applicable mandatory
requirement, a specific material departure, matched object or explicit
non-applicability, current chronology and known design stage. If any of those
are unresolved, use verification_required or
unknown_insufficient_information rather than deficiency. Optional scope,
tender scoring criteria, guidance, industry practice and consultant preference
cannot alone establish contractual non-compliance.

Use unknown_reason to distinguish an engineering question not yet verified,
evidence absent only from the reviewed package, an unavailable controlling
source, and other specified limits. Do not keep an issue UNKNOWN when later
accepted evidence resolves it; use no_issue or positive_assurance and cite that
evidence. A no_issue outcome requires cited counter-evidence. A positive-
assurance outcome requires affirmative evidence.

Before classifying a cross-document difference, establish whether both sources
refer to the same asset, role, revision, location and design stage. If not,
record different_objects or unknown and do not publish a deficiency. Check the
controlled chronology and supersession links; an earlier concern cannot remain
an active deficiency when later accepted evidence resolves it. Use project
stage from supplied context or evidence; if absent, report it unknown and do
not invent it. The independent Reviewer must try to disprove each material
candidate using the strongest compliant interpretation and cited
counter-evidence before its review gate can pass.

Return concise findings only. Do not reveal hidden reasoning. Any absent or
ambiguous source support must remain explicit in uncertainties and confidence.
""".strip()

MODE_GUIDANCE = {
    "execution_planning": "Analyse source requirements and uncertainties to structure the requested bounded task; preserve evidence citations and human control.",
    "execution_assessment": "Assess the exact V2 output against its task, supplied sources, requirements and applicable lessons; report proposed findings and unresolved evidence.",
    "truth": "Separate source facts, interpretations, assumptions, recommendations, and unknowns.",
    "steelman": "State the strongest reasonable interpretation supported by the supplied documents before describing a concern.",
    "gap": "Identify only material omissions, contradictions, ambiguities, or interface gaps established by the supplied evidence.",
    "critic": "Challenge each material conclusion, test alternatives, and record unresolved uncertainty.",
    "scope": "Map supported inclusions, exclusions, interfaces, and responsibilities; silence alone is not an exclusion.",
    "compliance": "Map each supplied requirement to retrieved evidence. Use unverified or requires_human_review when evidence is insufficient; never infer compliance from a confirmed finding.",
    "change": "Assess only changes supported by the supplied revision evidence; distinguish textual change from technical effect.",
    "risk": "Describe evidence-backed causes, consequences, risk dimensions, and actions; keep likelihood or technical severity unknown when unsupported.",
    "competitor": "Review the submission rigorously from a strong competing consultant or evaluator perspective while preserving source traceability and uncertainty.",
    "x10think": "Check the supplied evidence from multiple independent engineering and contractual angles, then return only concise findings and conclusions.",
    "allin": "Apply the relevant truth, steelman, scope, compliance, gap, critic, contractual, and risk lenses. Combine related observations only when their evidence and action are shared.",
}


class ResponsesClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


def _build_input(request: AnalysisRequest) -> str:
    payload = {
        "mode": request.mode,
        "analysis_focus": MODE_GUIDANCE.get(
            request.mode, "Analyse only the requested scope using supplied evidence."
        ),
        "project_id": request.project_id,
        "document_ids": request.document_ids,
        "requirements": request.requirements,
        "evidence": request.evidence,
        "context": request.context,
        **(
            {"task_instructions": request.instructions}
            if request.instructions is not None
            else {}
        ),
    }
    return (
        "Analyse the supplied engineering evidence. Return only the requested "
        "structured finding object.\n\nINPUT:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


class OpenAIAnalysisAdapter:
    """OpenAI Responses API adapter using strict JSON Schema output."""

    def __init__(
        self,
        client: ResponsesClient | None = None,
        model: str | None = None,
        store: bool = False,
    ) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI SDK is not installed. Install the project with the OpenAI optional dependency."
                ) from exc
            client = OpenAI().responses
        self.client = client
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        self.store = store

    def analyse(self, request: AnalysisRequest) -> LLMResponse:
        # Optional per-mode overrides let deployments route high-risk modes to
        # a stronger model without changing the pipeline or source evidence.
        selected_model = os.getenv(f"OPENAI_MODEL_{request.mode.upper()}") or self.model
        response = self.client.create(
            model=selected_model,
            store=self.store,
            instructions=SYSTEM_INSTRUCTIONS,
            input=_build_input(request),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "engineering_findings",
                    "strict": True,
                    "schema": (
                        EXECUTION_PLANNING_SCHEMA
                        if request.mode == "execution_planning"
                        else EXECUTION_ASSESSMENT_SCHEMA
                        if request.mode == "execution_assessment"
                        else REVIEWER_DISPROOF_SCHEMA
                        if request.mode == "client_alignment"
                        else FINDING_SCHEMA
                    ),
                }
            },
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise RuntimeError("OpenAI response contained no output_text")
        payload = json.loads(output_text)
        return LLMResponse(
            model=selected_model,
            response_id=getattr(response, "id", None),
            findings=payload["findings"],
            usage=_usage_dict(getattr(response, "usage", None)),
            task_decisions=list(payload.get("task_decisions", [])),
            lesson_reviews=list(payload.get("lesson_reviews", [])),
            finding_disproofs=list(payload.get("finding_disproofs", [])),
        )


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "__dict__"):
        return dict(usage.__dict__)
    return {}


def findings_from_response(
    response: LLMResponse,
    evidence: list[EvidenceRef],
    allowed_requirement_ids: set[str] | None = None,
) -> tuple[list[Finding], list[str], list[str]]:
    """Convert LLM JSON into validated findings; invalid findings are rejected."""
    engine = FindingEngine(evidence)
    findings: list[Finding] = []
    errors: list[str] = []
    warnings: list[str] = []

    for item in response.findings:
        try:
            unknown_requirements = (
                sorted(set(item.get("requirement_ids", [])) - allowed_requirement_ids)
                if allowed_requirement_ids is not None
                else []
            )
            if unknown_requirements:
                errors.append(
                    "Unknown requirement_id(s): " + ", ".join(unknown_requirements)
                )
                continue
            finding = Finding(
                finding_id=str(item["finding_id"]),
                title=str(item["title"]),
                finding=str(item["finding"]),
                status=FindingStatus(item["status"]),
                finding_class=FindingClass(item.get("finding_class", "unclassified")),
                qualification=FindingQualification(item["qualification"]),
                qualification_rationale=str(item["qualification_rationale"]),
                qualification_basis=QualificationBasis(
                    **item["qualification_basis"]
                ),
                source_evidence_ids=list(item["source_evidence_ids"]),
                requirement_ids=list(item["requirement_ids"]),
                interpretation=item.get("interpretation"),
                steelman=item.get("steelman"),
                critic=item.get("critic"),
                gap=item.get("gap"),
                impact=item.get("impact"),
                risk_level=RiskLevel(item["risk_level"]),
                risk_dimensions=list(item["risk_dimensions"]),
                recommendation=item.get("recommendation"),
                human_review_required=bool(item["human_review_required"]),
                human_review_reason=item.get("human_review_reason"),
                confidence=item["confidence"],
                assumptions=list(item["assumptions"]),
                uncertainties=list(item["uncertainties"]),
                conflicts=list(item["conflicts"]),
            )
            finding = engine.normalize(finding)
            validation = engine.validate(finding)
            if validation.valid:
                findings.append(finding)
                warnings.extend(validation.warnings)
            else:
                errors.extend(validation.errors)
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"Invalid LLM finding: {exc}")

    return findings, errors, warnings
