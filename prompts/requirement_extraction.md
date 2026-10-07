# Requirement Extraction Mode

## Purpose

Requirement Extraction Mode converts project documents into structured, traceable and revision-controlled requirements.

The objective is to answer:

> **What does the document actually require, who is responsible, under what conditions, and what evidence would demonstrate fulfilment?**

Requirement extraction is an evidence-preservation process.

It shall occur before higher-level interpretation such as:

- scope analysis;
- compliance analysis;
- gap analysis;
- risk analysis;
- tender evaluation;
- change comparison.

---

# 1. Core Principle

> **Extract first. Interpret second.**

The extraction engine shall preserve the original meaning of the source document before applying engineering interpretation.

Do not silently:

- rewrite contractual wording;
- remove qualifiers;
- combine unrelated requirements;
- convert recommendations into obligations;
- infer responsibility;
- invent acceptance criteria;
- infer missing parameters.

---

# 2. Requirement Sources

Requirements may be extracted from:

- RFP;
- RFQ;
- Employer's Requirements;
- Scope of Work;
- Technical Specification;
- Contract;
- Contract amendment;
- Tender Bulletin;
- Tender Clarification;
- Addendum;
- Drawing;
- Drawing Notes;
- BOQ;
- Design Basis;
- Design Criteria;
- Regulatory document;
- Authority approval;
- Code;
- Standard;
- Method Statement;
- Inspection and Test Plan;
- Quality requirements;
- HSE requirements;
- Programme requirements;
- Correspondence;
- Meeting minutes.

The source type shall always be recorded.

---

# 3. Requirement Detection

Search for requirement-bearing language including:

- shall;
- must;
- required;
- required to;
- shall provide;
- shall design;
- shall construct;
- shall submit;
- shall obtain;
- shall test;
- shall inspect;
- shall comply;
- shall ensure;
- shall maintain;
- shall not;
- minimum;
- maximum;
- not less than;
- not greater than;
- unless otherwise stated;
- where applicable;
- where required;
- subject to;
- prior to;
- before;
- after;
- upon approval;
- as instructed.

However:

> The presence of "shall" does not by itself establish who bears the obligation.

The surrounding context must be examined.

---

# 4. Requirement Atomicity

Extract requirements at the smallest useful logical unit.

Example:

> Contractor shall design, supply, install and test the fender system in accordance with the specification.

This may contain four obligations:

1. Design fender system.
2. Supply fender system.
3. Install fender system.
4. Test fender system.

Where splitting improves traceability, create separate requirement records while preserving their common source.

Do not split a requirement where doing so would destroy its contractual meaning.

---

# 5. Requirement Identity

Each requirement shall receive a unique identifier.

Recommended format:

```text
REQ-[DISCIPLINE]-[SEQUENCE]
