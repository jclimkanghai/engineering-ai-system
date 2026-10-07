# Requirement Extraction Agent

## Purpose

The Requirement Extraction Agent converts engineering and project documents
into structured, traceable requirement candidates.

The agent is designed for documents such as:

- Requests for Proposal (RFP)
- Requests for Quotation (RFQ)
- Tender Bulletins
- Tender Clarifications
- Employer's Requirements
- Technical Specifications
- Scope of Work
- Contracts
- Design Basis documents
- Regulatory requirements
- Engineering reports
- Drawings and schedules

The objective is to identify requirements while preserving their original
source evidence.

---

## Design Principle

> Extract evidence first. Interpret second.

The extraction layer must not invent technical, contractual, regulatory or
commercial information that is not supported by the source documents.

For example:

```text
Source:
"The Contractor shall provide steel tubular piles."
```

## Structured extraction

Use the package API to run deterministic extraction and adapt its candidates
to the requirement record model:

```python
from agents.requirement_agent import (
    RequirementRegistry,
    SourceLocation,
    extract_structured_requirements,
)

source = SourceLocation(
    document_id="RFP-001",
    document_number="RFP-001",
    revision="A",
    page=12,
    section="4.2 Piling",
)

records = extract_structured_requirements(
    "The Contractor shall provide steel tubular piles.",
    source,
)

schema_shaped_record = records[0].to_dict()

register = RequirementRegistry()
register.register_many(records)
requirements_for_document = register.for_document("RFP-001")
```

The record keeps extracted source text and provenance, leaves unsupported
fields unset, and uses the grouped field names and controlled values defined
in `schemas/requirement.schema.yaml`. Candidates that are not requirements
produce an empty list.

`RequirementRegistry` is an in-memory, process-local register. It accepts
repeat registrations of an unchanged requirement and rejects conflicting
records with the same ID. Persistence and cross-revision reconciliation are
not implemented yet.
