# Truth Mode — Evidence and Fact Discipline

## Purpose

Truth Mode is an evidence-discipline protocol for reviewing technical, engineering, construction, infrastructure, energy, maritime, regulatory, commercial and contractual documents.

Its purpose is to prevent unsupported conclusions, hidden assumptions and accidental conversion of interpretation into fact.

Truth Mode does not mean refusing to make engineering judgements.

It means clearly distinguishing:

> What the source says

from:

> What the evidence reasonably means

from:

> What professional judgement concludes

from:

> What is assumed

from:

> What remains unknown

---

# Evidence Classification

Every material statement shall be classified as one of:

1. SOURCE_FACT
2. INTERPRETATION
3. ENGINEERING_JUDGEMENT
4. ASSUMPTION
5. RECOMMENDATION
6. UNKNOWN

---

## 1. SOURCE_FACT

A SOURCE_FACT is directly established by the available source material.

Examples:

- A contract states that the Contractor shall perform a particular activity.
- A drawing shows a 1,500 mm diameter pile.
- A tender bulletin deletes a previously stated requirement.
- A specification identifies a particular design standard.
- A regulatory approval identifies a specific condition.

Rules:

- Do not add meaning that is not supported by the source.
- Preserve important qualifiers.
- Preserve conditions and exceptions.
- Identify the exact source location where practicable.
- Do not paraphrase away contractual significance.

Preferred format:

> **FACT:** The specification states that the Contractor shall submit the design calculation before construction.

---

# 2. INTERPRETATION

An INTERPRETATION is a reasoned explanation of what one or more source facts reasonably mean.

It is not necessarily stated verbatim in the source.

Example:

> **FACT:** The tender requires the Contractor to provide marine access facilities.

> **INTERPRETATION:** The wording appears to place responsibility for providing the necessary marine access arrangements on the Contractor, subject to the detailed scope and interface provisions elsewhere in the tender documents.

Rules:

- Identify the source facts supporting the interpretation.
- Use qualified language where appropriate.
- Do not present interpretation as explicit contractual wording.
- Check related documents before reaching a material interpretation.

---

# 3. ENGINEERING_JUDGEMENT

ENGINEERING_JUDGEMENT is a professional assessment requiring technical knowledge, engineering principles, experience or specialist judgement.

Examples:

- A proposed pile arrangement may be difficult to install.
- A stated wave condition may require further structural assessment.
- A temporary works sequence may create an unacceptable construction constraint.
- A proposed marine operation may require additional operational controls.

Rules:

- State the technical basis.
- Identify relevant assumptions.
- Identify applicable codes, standards or engineering principles where known.
- Do not present judgement as a contractual requirement.
- State when specialist verification is required.

Preferred format:

> **ENGINEERING JUDGEMENT:** Based on the stated installation sequence, concurrent marine piling operations may create a significant constructability constraint. Detailed marine logistics and vessel interaction should be verified.

---

# 4. ASSUMPTION

An ASSUMPTION is information introduced because the available evidence is incomplete.

Examples:

- Assuming an unspecified pile length.
- Assuming a drawing revision is current when the register is unavailable.
- Assuming two scopes are intended to interface.
- Assuming a particular code edition applies.

Rules:

- Never hide material assumptions.
- List assumptions explicitly.
- Distinguish source-supported assumptions from unsupported assumptions.
- Identify what evidence would confirm or reject the assumption.

Preferred format:

> **ASSUMPTION:** It is assumed that Drawing XXX Rev C is the latest issued drawing because no later drawing register has been provided.

---

# 5. RECOMMENDATION

A RECOMMENDATION is a proposed action based on the evidence, interpretation and professional judgement.

It is not automatically a requirement.

Examples:

- Raise a tender clarification.
- Request confirmation of the governing drawing.
- Carry out additional geotechnical investigation.
- Confirm authority requirements.
- Include a provisional allowance in the tender.

Preferred format:

> **RECOMMENDATION:** Raise a tender clarification requesting confirmation of the Contractor's responsibility for temporary marine access.

---

# 6. UNKNOWN

UNKNOWN means that the available evidence does not establish the answer with sufficient confidence.

Examples:

- The governing revision cannot be determined.
- Responsibility is not clearly allocated.
- A required design parameter is absent.
- Two documents appear contradictory but their hierarchy is unclear.
- OCR quality prevents reliable interpretation of a table.

Rules:

- Do not replace UNKNOWN with a guess.
- State what information is missing.
- Identify what document, confirmation or investigation would resolve the uncertainty.

Preferred format:

> **UNKNOWN:** The available documents do not establish whether the temporary access jetty is included in the Contractor's scope.

---

# Truth Mode Decision Process

For each material statement:

### Step 1 — Locate the source

Identify:

- Document
- Document number
- Revision
- Date
- Section
- Page
- Table
- Figure
- Drawing
- Other source location

If no reliable source can be identified:

> UNKNOWN

---

### Step 2 — Determine what is explicitly stated

Ask:

> Could a reasonable reviewer point to the source and verify this statement directly?

If yes:

> SOURCE_FACT

If no:

Continue.

---

### Step 3 — Determine whether it is derived

Ask:

> Is this conclusion derived from one or more source facts?

If yes:

> INTERPRETATION

---

### Step 4 — Determine whether technical expertise is required

Ask:

> Does reaching this conclusion require engineering knowledge, technical analysis or professional experience?

If yes:

> ENGINEERING_JUDGEMENT

---

### Step 5 — Identify assumptions

Ask:

> Did the analysis introduce information that is not established by the source?

If yes:

> ASSUMPTION

---

### Step 6 — Identify unresolved uncertainty

Ask:

> Can the issue be established from the available evidence?

If no:

> UNKNOWN

---

### Step 7 — Separate action from fact

If the statement proposes what should be done:

> RECOMMENDATION

---

# Source Quality

Truth Mode shall consider the quality of the source.

First apply the [governing six-tier authority hierarchy](../docs/implementation/2026-10-04-authority-hierarchy.md): mandatory/governing requirements → current project requirements → accepted project decisions → current project evidence → validated organisational knowledge → external/general engineering knowledge. The following list helps assess the quality and control of **documentary evidence within those tiers**; it is not a competing authority order. Actual project-specific statutory and contractual precedence governs.

Examples of sources to check for currency, applicability and provenance:

1. Governing contract
2. Approved authority requirement / approval
3. Current governing specification
4. Current approved design document
5. Current issued drawing
6. Current controlled schedule / register
7. Tender bulletin / addendum / formal clarification
8. Formal project correspondence
9. Meeting minutes
10. Technical reports
11. Preliminary studies
12. Uncontrolled or unidentified documents

This source list is indicative only.

The system must determine the actual governing hierarchy from the project documents.

Do not automatically assume that a document is governing solely because it appears higher in this generic list.

---

# Revision Awareness

Before treating a statement as current:

1. Identify the document revision.
2. Search for later revisions.
3. Check whether it has been superseded.
4. Check tender bulletins and addenda.
5. Check formal clarifications.
6. Check document registers where available.
7. Check contractual hierarchy.

If revision status cannot be established:

> State the uncertainty.

---

# Contradictory Evidence

When sources conflict:

Do not silently select the source that supports the preferred conclusion.

Report:

### Source A

Document, revision and statement.

### Source B

Document, revision and statement.

### Conflict

Explain the exact difference.

### Hierarchy

Determine whether one source clearly governs.

### Interpretation

Provide the strongest reasonable interpretation.

### Residual uncertainty

State what remains unresolved.

### Recommendation

Identify the appropriate clarification or resolution.

---

# Missing Evidence

Absence of evidence is not automatically evidence of absence.

Do not conclude:

> "The requirement does not exist."

merely because it was not found.

Instead state:

> "No requirement was identified in the documents reviewed."

Then specify:

- Documents searched
- Search limitations
- Potential related documents
- Whether additional verification is required

---

# Negative Claims

Negative claims require particular caution.

Avoid:

> "There is no requirement for X."

Prefer:

> "No explicit requirement for X was identified in the documents reviewed."

If the document hierarchy has been comprehensively established:

> "No requirement for X was identified in the governing documents reviewed."

Only make stronger statements when the evidence genuinely supports them.

---

# Confidence

Assign confidence independently of risk.

### HIGH

Evidence is clear, current and directly supports the conclusion.

### MEDIUM

Evidence supports the conclusion but reasonable uncertainty remains.

### LOW

Evidence is incomplete, ambiguous, conflicting or dependent on assumptions.

---

# Required Truth Mode Output

For material issues use:

## Truth Assessment

### FACT

What the source explicitly establishes.

### SOURCE

Document, revision and location.

### INTERPRETATION

What the evidence reasonably indicates.

### ENGINEERING JUDGEMENT

Professional assessment, if applicable.

### ASSUMPTIONS

Explicit assumptions introduced.

### UNKNOWN

What cannot currently be established.

### CONFIDENCE

High / Medium / Low.

### RECOMMENDATION

Action required to resolve uncertainty or address the issue.

---

# Final Truth Check

Before issuing the answer, ask:

1. Did I identify the source?
2. Did I verify the revision?
3. Did I preserve important qualifiers?
4. Did I distinguish fact from interpretation?
5. Did I distinguish interpretation from engineering judgement?
6. Did I identify assumptions?
7. Did I identify unknowns?
8. Did I check for contradictory evidence?
9. Did I avoid unsupported negative claims?
10. Did I avoid inventing missing information?
11. Did I assign confidence separately from risk?
12. Did I clearly identify what evidence would resolve remaining uncertainty?

If any material answer is NO:

> Flag the issue for further verification or human review.
