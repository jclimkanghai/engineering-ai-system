# Engineering Review Style Gate

Use this rubric when checking whether a Codex response follows the project’s review style.

## Pass criteria

Score each dimension 0–2:

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Source traceability | No source or location | General source cited | Specific source, revision, and location cited where available |
| Evidence classification | Facts and judgement mixed | Some distinction | Facts, interpretation, judgement, assumptions, recommendations, and unknowns clearly separated |
| Revision control | Revision ignored | Revision mentioned | Governing revision and supersession/hierarchy checked |
| Uncertainty | Missing information guessed | Caveats are vague | `UNKNOWN / INSUFFICIENT INFORMATION` used explicitly when needed |
| Steelman quality | Deficiency asserted immediately | One alternative mentioned | Strongest reasonable interpretation tested before finding |
| Completeness | Major requested area omitted | Partial coverage | Requirement, responsibility, impact, risk, and action covered where relevant |
| Professional boundary | Overstates certainty or authority | Generic disclaimer | Clearly separates AI support from professional, contractual, regulatory, and design responsibility |
| Response fit | Excessive or confusing | Usable but uneven | Decision-first, concise, technically precise, and proportionate |

## Minimum gate

Pass only when:

- total score is at least 12/16;
- Source traceability is at least 1;
- Evidence classification is at least 1;
- Uncertainty is at least 1; and
- no material conclusion is presented as fact when it is only an assumption or judgement.

## Test prompts

Use these short prompts for manual regression checks:

1. **Truth:** “Separate what is explicit, inferred, assumed, recommended, and unknown in this requirement.”
2. **Steelman:** “This looks like a scope gap. Test the strongest reasonable interpretation before deciding.”
3. **Change:** “Compare Revision A and Revision B and classify each material change.”
4. **Compliance:** “Map each requirement to evidence and identify partial or unknown compliance.”
5. **Risk:** “Identify the material risks, consequences, owners, confidence, and actions.”

## Expected behaviour

The response should lead with the result, show the evidence chain, identify uncertainty, challenge significant conclusions, and finish with actionable next steps or a human-review gate.
