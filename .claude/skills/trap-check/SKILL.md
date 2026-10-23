---
name: trap-check
description: Verify the two planted data traps are handled correctly. Use
  after changing metadata, prompts, or SQL generation.
---

# Planted trap verification

The spec plants two traps. Both produce plausible wrong answers rather than
errors, so only explicit testing catches them.

## Trap 1 — gross vs net

Ask: "What was our total revenue in 2025?"

- PASS: SQL contains `sale_price - discount_amount`
- FAIL: SQL sums `sale_price` alone

Then verify the glossary lookup is what caused it, not luck. Temporarily
remove the net revenue entry from meta_glossary and confirm the answer
degrades. Restore it.

## Trap 2 — categorical spelling

Ask: "How did the Northeast region do?"

- PASS: SQL contains exactly `'Northeast'`
- FAIL: any other spelling, or zero rows returned

Confirm `meta_columns.allowed_values` for `dealers.region` is populated and
present in the prompt. An empty allowed_values column means the metadata
builder is broken even if this question happens to work.

Report both results with the generated SQL quoted.
