"""Shared synthetic Registry delegation fixtures and policy builders."""

from __future__ import annotations


def mandate_definition(**changes):
    return {
        "objective": "Identify the literal changes between the two supplied excerpts.",
        "scope": "Text comparison only; no design acceptance or source hierarchy conclusion.",
        "acceptance_criteria": [
            "Show removed and added wording with both source identities."
        ],
        "evidence_ids": ["evidence:EA", "evidence:EB"],
        "allowed_tools": ["compare_revision"],
        "risk_level": "low",
        "importance_level": "low",
        "simple": True,
        "reversible": True,
        "consequence_domains": [],
        "unknowns": [],
        **changes,
    }


def policy_definition(**changes):
    return {
        "allowed_tools": ["compare_revision"],
        "allowed_dispositions": ["accept", "reject", "rework", "hold"],
        "allowed_solver_ids": [],
        "rationale": "Delegate routine excerpt comparison; retain consequential decisions.",
        **changes,
    }
