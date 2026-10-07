from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from pipelines.findings import EvidenceClass, EvidenceRef
from pipelines.llm import AnalysisRequest, LLMResponse, findings_from_response
from pipelines.llm.adapter import MODE_GUIDANCE, SYSTEM_INSTRUCTIONS
from pipelines.llm.schemas import (
    EXECUTION_ASSESSMENT_SCHEMA,
    EXECUTION_PLANNING_SCHEMA,
    FINDING_SCHEMA,
)
from pipelines.retrieval.models import RetrievalQuery


class AnalysisAdapter(Protocol):
    def analyse(self, request: AnalysisRequest) -> LLMResponse: ...


def _analysis_cache_key(request: AnalysisRequest, refs: list[EvidenceRef]) -> str:
    """Return a deterministic key for one bounded analysis batch.

    The source wording and evidence excerpts are included so a cache entry is
    never reused after the indexed source has changed.
    """
    mode_schema = (
        EXECUTION_PLANNING_SCHEMA
        if request.mode == "execution_planning"
        else EXECUTION_ASSESSMENT_SCHEMA
        if request.mode == "execution_assessment"
        else FINDING_SCHEMA
    )
    analysis_contract = {
        "system_instructions": SYSTEM_INSTRUCTIONS,
        "mode_guidance": MODE_GUIDANCE.get(request.mode),
        "response_schema": mode_schema,
    }
    payload = {
        "analysis_contract_sha256": hashlib.sha256(
            json.dumps(
                analysis_contract, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "mode": request.mode,
        "project_id": request.project_id,
        "document_ids": request.document_ids,
        "requirements": request.requirements,
        "evidence": [
            {
                "evidence_id": ref.evidence_id,
                "document_id": ref.document_id,
                "revision": ref.revision,
                "locator": ref.locator,
                "excerpt": ref.excerpt,
            }
            for ref in refs
        ],
        "context": request.context,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_analysis_cache(path_value: Any) -> tuple[Path | None, dict[str, Any]]:
    if not path_value:
        return None, {"version": 1, "entries": {}}
    path = Path(str(path_value)).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("entries", {}), dict):
        data = {}
    return path, {"version": 1, "entries": dict(data.get("entries", {}))}


def _save_analysis_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        # Cache persistence is an optimization and must not block a review.
        return


def _response_from_cache(value: Any) -> LLMResponse | None:
    if not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        return None
    return LLMResponse(
        model=str(value.get("model") or "cached"),
        response_id=value.get("response_id"),
        findings=list(value["findings"]),
        usage=dict(value.get("usage") or {}),
        raw_output=value.get("raw_output"),
        task_decisions=list(value.get("task_decisions") or []),
    )


def _configured_limits() -> dict[str, int]:
    defaults = {
        "analysis_batch_size": 8,
        "retrieval_top_k_per_requirement": 2,
        "analysis_context_max_chars": 24_000,
        "maximum_batch_size": 12,
        "maximum_retrieval_hits_per_requirement": 4,
        "maximum_context_chars": 100_000,
    }
    path = Path(__file__).resolve().parents[2] / "config" / "llm_analysis.yaml"
    if not path.exists():
        return defaults
    try:
        import yaml
    except ImportError:
        return defaults
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        limits = {
            **defaults,
            **config.get("context_budget", {}),
            **config.get("limits", {}),
        }
        limits["maximum_batch_size"] = max(
            1, min(12, int(limits["maximum_batch_size"]))
        )
        limits["maximum_retrieval_hits_per_requirement"] = max(
            1, min(4, int(limits["maximum_retrieval_hits_per_requirement"]))
        )
        limits["maximum_context_chars"] = max(
            1_000, min(100_000, int(limits["maximum_context_chars"]))
        )
        return limits
    except (TypeError, ValueError):
        return defaults


def _stable_finding_id(finding: dict[str, Any]) -> str:
    identity = {
        "title": finding.get("title"),
        "finding": finding.get("finding"),
        "evidence": sorted(finding.get("source_evidence_ids", [])),
        "requirements": sorted(finding.get("requirement_ids", [])),
    }
    digest = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()[:16]
    return f"F-{digest}"


def _evidence_ref(item: dict[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=str(item["evidence_id"]),
        document_id=item.get("document_id"),
        revision=item.get("revision"),
        locator=item.get("locator"),
        excerpt=item.get("text") or item.get("source_text"),
        evidence_class=EvidenceClass.SOURCE_FACT,
    )


def _grouped(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_evidence_analysis(
    context,
    adapter: AnalysisAdapter | None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Run one mode-guided, bounded analysis call per small requirement batch."""
    # The triage stage keeps the full extracted registry in ``requirements``
    # while supplying a smaller, auditable model queue when available.
    requirements = list(
        context.get("analysis_requirements") or context.get("requirements", [])
    )
    requirement_clusters = context.get("requirement_clusters", {})
    cluster_members_by_requirement = {
        cluster.get("representative_requirement_id"): list(
            cluster.get("member_requirement_ids", [])
        )
        for cluster in requirement_clusters.values()
        if isinstance(cluster, dict) and cluster.get("representative_requirement_id")
    }
    prior_evidence = list(context.get("evidence", []))
    if adapter is None:
        return {
            "findings": [],
            "analysis_status": "NOT_CONNECTED",
            "analysis_errors": [],
            "analysis_warnings": [
                "No analysis adapter is configured; no model call was made."
            ],
            "analysis_usage": {},
            "analysis_calls": 0,
            "analysis_coverage": [],
            "analysis_evidence": [],
        }
    if not requirements:
        return {
            "findings": [],
            "analysis_status": "INSUFFICIENT_INPUT",
            "analysis_errors": [],
            "analysis_warnings": [
                "No extracted requirements were available for analysis."
            ],
            "analysis_usage": {},
            "analysis_calls": 0,
            "analysis_coverage": [],
            "analysis_evidence": [],
        }

    metadata = context.request.metadata
    configured = _configured_limits()
    try:
        batch_size = max(
            1,
            min(
                configured["maximum_batch_size"],
                int(
                    metadata.get(
                        "analysis_batch_size", configured["analysis_batch_size"]
                    )
                ),
            ),
        )
        per_requirement_hits = max(
            1,
            min(
                configured["maximum_retrieval_hits_per_requirement"],
                int(
                    metadata.get(
                        "retrieval_top_k_per_requirement",
                        configured["retrieval_top_k_per_requirement"],
                    )
                ),
            ),
        )
        budget_chars = max(
            1_000,
            min(
                configured["maximum_context_chars"],
                int(
                    metadata.get(
                        "analysis_context_max_chars",
                        configured["analysis_context_max_chars"],
                    )
                ),
            ),
        )
    except (TypeError, ValueError):
        batch_size, per_requirement_hits, budget_chars = 4, 2, 24_000

    engine = context.get("evidence_engine")
    document_ids = list(context.request.document_ids)
    all_findings: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    coverage: list[dict[str, Any]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    usage_totals: dict[str, int] = {}
    response_metadata: list[dict[str, Any]] = []
    call_count = 0
    retry_count = 0
    cache_hits = 0
    completed_batches = 0
    seen_finding_ids: set[str] = set()
    cache_path, analysis_cache = _load_analysis_cache(
        metadata.get("analysis_cache_path")
    )

    for requirement in requirements:
        identity = requirement.get("identity", {})
        provenance = requirement.get("provenance", {})
        requirement_id = identity.get("requirement_id")
        text = (
            identity.get("source_text")
            or requirement.get("source_text")
            or requirement.get("requirement_text")
        )
        if not requirement_id or not text:
            warnings.append(
                "Skipped an extracted requirement missing its ID or source wording."
            )
            continue
        direct_source = None
        if engine is not None:
            chunk = engine.index.get(str(requirement_id))
            if chunk is not None:
                direct_source = chunk.to_dict()
        if direct_source is None:
            direct_source = {
                "evidence_id": str(requirement_id),
                "document_id": provenance.get("source_document_id"),
                "source_hash": provenance.get("source_hash"),
                "document_number": provenance.get("source_document_number"),
                "title": provenance.get("source_document_title"),
                "revision": provenance.get("source_revision"),
                "revision_date": provenance.get("source_revision_date"),
                "page": provenance.get("page"),
                "section": provenance.get("section"),
                "locator": provenance.get("source_location"),
                "text": str(text),
                "evidence_class": "requirement",
            }
        evidence_by_id[str(requirement_id)] = direct_source
        supporting: list[dict[str, Any]] = []
        retrieval_warnings: list[str] = []
        if engine is not None:
            bundle = engine.query(
                RetrievalQuery(
                    query=str(text),
                    document_ids=document_ids,
                    evidence_classes=["source_fact", "context"],
                    top_k=per_requirement_hits,
                    min_score=0.0,
                    include_context=True,
                )
            )
            retrieval_warnings.extend(bundle.warnings)
            hit_scores = {item["evidence_id"]: item for item in bundle.hits}
            for item in bundle.evidence:
                hit = hit_scores.get(item.get("evidence_id"), {})
                # Metadata-only fallbacks are useful for browsing but are not
                # evidence of semantic support for an engineering conclusion.
                if not (
                    hit.get("lexical_score", 0) > 0 or hit.get("semantic_score", 0) > 0
                ):
                    continue
                if item.get("evidence_id") == requirement_id:
                    continue
                supporting.append(item)
                evidence_by_id[str(item["evidence_id"])] = item
        coverage.append(
            {
                "requirement_id": str(requirement_id),
                "source_document_id": provenance.get("source_document_id"),
                "supporting_evidence_ids": [
                    str(item["evidence_id"]) for item in supporting
                ],
                "supporting_evidence_count": len(supporting),
                "coverage_status": "context_retrieved"
                if supporting
                else "no_matching_context",
                "warnings": retrieval_warnings,
            }
        )

    eligible = [
        req
        for req in requirements
        if req.get("identity", {}).get("requirement_id")
        and (
            req.get("identity", {}).get("source_text")
            or req.get("source_text")
            or req.get("requirement_text")
        )
    ]
    total_batches = (len(eligible) + batch_size - 1) // batch_size
    for batch_index, batch in enumerate(_grouped(eligible, batch_size), start=1):
        batch_ids = {str(item["identity"]["requirement_id"]) for item in batch}
        batch_evidence: dict[str, dict[str, Any]] = {
            requirement_id: evidence_by_id[requirement_id]
            for requirement_id in sorted(batch_ids)
        }
        for record in coverage:
            if record["requirement_id"] in batch_ids:
                for evidence_id in record["supporting_evidence_ids"]:
                    if evidence_id in evidence_by_id:
                        batch_evidence[evidence_id] = evidence_by_id[evidence_id]

        # Never discard original requirement wording to satisfy the context
        # budget. The budget limits retrieved supporting context; if the
        # requirements themselves exceed it, preserve them and report that.
        direct = [
            batch_evidence[rid] for rid in sorted(batch_ids) if rid in batch_evidence
        ]
        support = [item for eid, item in batch_evidence.items() if eid not in batch_ids]
        accepted = list(direct)
        used_chars = sum(len(str(item.get("text", ""))) for item in direct)
        if used_chars > budget_chars:
            warnings.append(
                f"Batch {batch_index}: requirement source text exceeds the configured context budget; "
                "source wording was retained."
            )
        for item in support:
            item_size = len(str(item.get("text", "")))
            if used_chars + item_size > budget_chars:
                warnings.append(
                    f"Batch {batch_index}: evidence context reached the configured character budget."
                )
                continue
            accepted.append(item)
            used_chars += item_size
        refs = [_evidence_ref(item) for item in accepted]
        request = AnalysisRequest(
            mode=mode or context.request.review_mode,
            project_id=metadata.get("project_id"),
            document_ids=document_ids,
            requirements=batch,
            evidence=accepted,
            context={
                "requested_scope": context.request.requested_scope,
                "changes": context.get("changes", []),
                "coverage": [
                    item for item in coverage if item["requirement_id"] in batch_ids
                ],
                "analysis_plan": context.get("analysis_plan", {}),
                "requirement_clusters": {
                    cluster_id: cluster
                    for cluster_id, cluster in requirement_clusters.items()
                    if cluster.get("representative_requirement_id") in batch_ids
                },
                "retrieval_rule": "A missing lexical match is not proof of omission, non-compliance, or scope exclusion.",
                "batch": {"index": batch_index, "count": total_batches},
            },
        )
        try:
            cache_key = _analysis_cache_key(request, refs)
            response = _response_from_cache(analysis_cache["entries"].get(cache_key))
            cache_hit = response is not None
            if cache_hit:
                cache_hits += 1
            else:
                response = adapter.analyse(request)
                call_count += 1
            accepted_findings, validation_errors, validation_warnings = (
                findings_from_response(
                    response,
                    refs,
                    allowed_requirement_ids=batch_ids,
                )
            )
            # A model occasionally cites an evidence ID that was not supplied
            # in its bounded context. Give only that batch one constrained
            # repair attempt; never broaden the evidence set implicitly.
            repairable = validation_errors and all(
                error.startswith("Unknown evidence_id:") for error in validation_errors
            )
            if repairable:
                repair_context = {
                    **request.context,
                    "citation_repair": {
                        "attempt": 1,
                        "invalid_errors": validation_errors,
                        "allowed_evidence_ids": [ref.evidence_id for ref in refs],
                        "instruction": "Use only allowed_evidence_ids; omit unsupported findings rather than inventing citations.",
                    },
                }
                repair_request = AnalysisRequest(
                    mode=request.mode,
                    project_id=request.project_id,
                    document_ids=request.document_ids,
                    requirements=request.requirements,
                    evidence=request.evidence,
                    context=repair_context,
                )
                repair_response = adapter.analyse(repair_request)
                call_count += 1
                retry_count += 1
                repaired_findings, repaired_errors, repaired_warnings = (
                    findings_from_response(
                        repair_response,
                        refs,
                        allowed_requirement_ids=batch_ids,
                    )
                )
                if not repaired_errors:
                    response = repair_response
                    cache_hit = False
                    accepted_findings = repaired_findings
                    validation_errors = repaired_errors
                    validation_warnings = [
                        *validation_warnings,
                        *repaired_warnings,
                        f"Batch {batch_index}: invalid citations were repaired in a constrained retry.",
                    ]
                else:
                    validation_errors = repaired_errors
                    validation_warnings.extend(repaired_warnings)
            if validation_errors:
                errors.extend(
                    f"Batch {batch_index}: {error}" for error in validation_errors
                )
            warnings.extend(
                f"Batch {batch_index}: {warning}" for warning in validation_warnings
            )
            if not validation_errors:
                analysis_cache["entries"][cache_key] = {
                    "model": response.model,
                    "response_id": response.response_id,
                    "findings": response.findings,
                    "task_decisions": response.task_decisions,
                    "usage": response.usage,
                    "raw_output": response.raw_output,
                }
                _save_analysis_cache(cache_path, analysis_cache)
                completed_batches += 1
            for finding in accepted_findings:
                data = finding.to_dict()
                provider_finding_id = data.get("finding_id")
                data.setdefault("metadata", {})["provider_finding_id"] = (
                    provider_finding_id
                )
                expanded_requirement_ids = list(data.get("requirement_ids", []))
                for requirement_id in list(expanded_requirement_ids):
                    for member_id in cluster_members_by_requirement.get(
                        requirement_id, []
                    ):
                        if member_id not in expanded_requirement_ids:
                            expanded_requirement_ids.append(member_id)
                data["requirement_ids"] = expanded_requirement_ids
                data["finding_id"] = _stable_finding_id(data)
                if data["finding_id"] in seen_finding_ids:
                    warnings.append(
                        f"Batch {batch_index}: duplicate finding {data['finding_id']} was omitted."
                    )
                    continue
                seen_finding_ids.add(data["finding_id"])
                all_findings.append(data)
            response_metadata.append(
                {
                    "batch": batch_index,
                    "model": response.model,
                    "response_id": response.response_id,
                    "cache_hit": cache_hit,
                }
            )
            for key, value in response.usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage_totals[key] = usage_totals.get(key, 0) + int(value)
        except Exception as exc:
            errors.append(
                f"Batch {batch_index}: analysis call failed ({type(exc).__name__})."
            )

    # Ensure source evidence already created during extraction remains in the
    # audit lookup and add all retrieved chunks that findings may cite.
    merged_evidence: dict[str, dict[str, Any]] = {
        str(item["evidence_id"]): dict(item)
        for item in prior_evidence
        if item.get("evidence_id")
    }
    for evidence_id, item in evidence_by_id.items():
        merged_evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "requirement_id": item.get("metadata", {}).get("requirement_id"),
            "document_id": item.get("document_id"),
            "source_hash": item.get("metadata", {}).get("source_hash"),
            "document_number": item.get("document_number"),
            "revision": item.get("revision"),
            "page": item.get("page"),
            "locator": item.get("locator"),
            "section": item.get("section"),
            "source_text": item.get("text") or item.get("source_text"),
            "evidence_type": item.get("evidence_class", "source_fact"),
        }
    for finding in all_findings:
        for evidence_id in finding.get("source_evidence_ids", []):
            if evidence_id not in merged_evidence:
                errors.append(
                    f"Accepted finding refers to evidence absent from the audit map: {evidence_id}"
                )

    status = (
        "COMPLETE" if completed_batches == total_batches and not errors else "PARTIAL"
    )
    return {
        "findings": all_findings,
        "evidence": list(merged_evidence.values()),
        "analysis_status": status,
        "analysis_errors": errors,
        "analysis_warnings": warnings,
        "analysis_usage": usage_totals,
        "analysis_calls": call_count,
        "analysis_retries": retry_count,
        "analysis_cache_hits": cache_hits,
        "analysis_batches": total_batches,
        "analysis_responses": response_metadata,
        "analysis_coverage": coverage,
        "analysis_evidence": list(evidence_by_id.values()),
    }
