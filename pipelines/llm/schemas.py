from __future__ import annotations

from copy import deepcopy

from pipelines.findings.models import (
    FindingQualification,
    ProjectStage,
    RequirementType,
    UnknownReason,
)

QUALIFICATION_BASIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_authority": {
            "type": "string",
            "enum": ["established", "unresolved", "not_applicable"],
        },
        "requirement_type": {
            "type": ["string", "null"],
            "enum": [*(item.value for item in RequirementType), None],
        },
        "same_object": {
            "type": "string",
            "enum": ["matched", "different_objects", "not_applicable", "unknown"],
        },
        "same_object_basis": {"type": ["string", "null"]},
        "chronology": {
            "type": "string",
            "enum": [
                "current",
                "superseded",
                "resolved_by_later_evidence",
                "not_applicable",
                "unknown",
            ],
        },
        "project_stage": {
            "type": "string",
            "enum": [item.value for item in ProjectStage],
        },
        "materiality": {
            "type": "string",
            "enum": ["material", "immaterial", "unknown"],
        },
        "counter_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "unknown_reason": {
            "type": ["string", "null"],
            "enum": [*(item.value for item in UnknownReason), None],
        },
    },
    "required": [
        "source_authority",
        "requirement_type",
        "same_object",
        "same_object_basis",
        "chronology",
        "project_stage",
        "materiality",
        "counter_evidence_ids",
        "unknown_reason",
    ],
}

FINDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finding_id": {"type": "string"},
                    "title": {"type": "string"},
                    "finding": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "confirmed",
                            "potential",
                            "unverified",
                            "disputed",
                            "requires_human_review",
                        ],
                    },
                    "finding_class": {
                        "type": "string",
                        "enum": [
                            "requirement_noncompliance",
                            "technical_inconsistency",
                            "verification_item",
                            "evidence_limitation",
                            "tender_observation",
                            "recommendation",
                        ],
                    },
                    "qualification": {
                        "type": "string",
                        "enum": [item.value for item in FindingQualification],
                    },
                    "qualification_rationale": {"type": "string"},
                    "qualification_basis": QUALIFICATION_BASIS_SCHEMA,
                    "source_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "requirement_ids": {"type": "array", "items": {"type": "string"}},
                    "interpretation": {"type": ["string", "null"]},
                    "steelman": {"type": ["string", "null"]},
                    "critic": {"type": ["string", "null"]},
                    "gap": {"type": ["string", "null"]},
                    "impact": {"type": ["string", "null"]},
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical", "unknown"],
                    },
                    "risk_dimensions": {"type": "array", "items": {"type": "string"}},
                    "recommendation": {"type": ["string", "null"]},
                    "human_review_required": {"type": "boolean"},
                    "human_review_reason": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "uncertainties": {"type": "array", "items": {"type": "string"}},
                    "conflicts": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "finding_id",
                    "title",
                    "finding",
                    "status",
                    "finding_class",
                    "qualification",
                    "qualification_rationale",
                    "qualification_basis",
                    "source_evidence_ids",
                    "requirement_ids",
                    "interpretation",
                    "steelman",
                    "critic",
                    "gap",
                    "impact",
                    "risk_level",
                    "risk_dimensions",
                    "recommendation",
                    "human_review_required",
                    "human_review_reason",
                    "confidence",
                    "assumptions",
                    "uncertainties",
                    "conflicts",
                ],
            },
        }
    },
    "required": ["findings"],
}

TASK_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision_id": {"type": "string"},
        "statement": {"type": "string"},
        "rationale": {"type": "string"},
        "decision_level": {"type": "string", "enum": ["D0", "D1", "D2", "D3", "D4"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "requirement_ids": {"type": "array", "items": {"type": "string"}},
        "related_record_ids": {"type": "array", "items": {"type": "string"}},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "impact": {"type": ["string", "null"]},
    },
    "required": [
        "decision_id",
        "statement",
        "rationale",
        "decision_level",
        "evidence_ids",
        "requirement_ids",
        "related_record_ids",
        "alternatives",
        "assumptions",
        "impact",
    ],
}

EXECUTION_PLANNING_SCHEMA = deepcopy(FINDING_SCHEMA)
EXECUTION_PLANNING_SCHEMA["properties"]["task_decisions"] = {
    "type": "array",
    "items": TASK_DECISION_SCHEMA,
}
EXECUTION_PLANNING_SCHEMA["required"].append("task_decisions")

LESSON_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lesson_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "not_relevant",
                "not_applicable",
                "applicable",
                "partial",
                "conflict",
                "uncertain",
            ],
        },
        "rationale": {"type": "string"},
        "requirement_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "requirements_check": {
            "type": "string",
            "enum": ["complies", "conflict", "unknown", "not_applicable"],
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "resolution": {
            "type": ["string", "null"],
            "enum": ["compliant_alternative", "deviation_proposed", "unresolved", None],
        },
        "lead_action": {"type": ["string", "null"]},
        "conflict_interpretation": {
            "type": ["string", "null"],
            "enum": [
                "project_requirement_controls",
                "possible_requirement_problem",
                None,
            ],
        },
        "requirement_concern": {"type": ["string", "null"]},
    },
    "required": [
        "lesson_id",
        "status",
        "rationale",
        "requirement_ids",
        "evidence_ids",
        "requirements_check",
        "limitations",
        "resolution",
        "lead_action",
        "conflict_interpretation",
        "requirement_concern",
    ],
}

EXECUTION_PLANNING_SCHEMA["properties"]["lesson_reviews"] = {
    "type": "array",
    "items": LESSON_REVIEW_SCHEMA,
}
EXECUTION_PLANNING_SCHEMA["required"].append("lesson_reviews")

EXECUTION_ASSESSMENT_SCHEMA = deepcopy(FINDING_SCHEMA)
EXECUTION_ASSESSMENT_SCHEMA["properties"]["lesson_reviews"] = {
    "type": "array",
    "items": LESSON_REVIEW_SCHEMA,
}
EXECUTION_ASSESSMENT_SCHEMA["required"].append("lesson_reviews")

REVIEWER_DISPROOF_SCHEMA = deepcopy(FINDING_SCHEMA)
REVIEWER_DISPROOF_SCHEMA["properties"]["finding_disproofs"] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidate_finding_id": {"type": "string"},
            "review_disposition": {
                "type": "string",
                "enum": ["upheld", "reclassified", "disproved", "insufficient_information"],
            },
            "rationale": {"type": "string"},
            "proposed_qualification": {
                "type": ["string", "null"],
                "enum": [*(item.value for item in FindingQualification), None],
            },
            "counter_evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["candidate_finding_id", "review_disposition", "rationale", "proposed_qualification", "counter_evidence_ids"],
    },
}
REVIEWER_DISPROOF_SCHEMA["required"].append("finding_disproofs")
