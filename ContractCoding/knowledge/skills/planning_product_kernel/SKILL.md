---
name: planning_product_kernel
description: Freeze product semantics, canonical substrate, interface capsules, executable flows, and slice/team boundaries before implementation.
---

## Must
- Declare canonical schemas, substrate owner artifacts, and substrate slice dependencies before workers write code; shared value objects must have exactly one source module.
- Convert user-visible flows into executable probes with fixture construction, public API calls, and expected invariants.
- Split work by feature/team boundaries while preserving explicit producer-consumer dependencies and conflict keys.
- Mark ambiguity as diagnostics or replan material instead of inventing hidden rules.

## Avoid
- Do not let sibling slices redefine shared value objects, enums, identifiers, or serialization shapes.
- Do not accept compile/import checks as sufficient final product evidence for large tasks.
