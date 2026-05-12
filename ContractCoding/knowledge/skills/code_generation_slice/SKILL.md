---
name: code_generation_slice
description: Implement one bounded feature slice against frozen kernel semantics, canonical substrate, and dependency interface capsules.
---

## Must
- Start by calling contract_snapshot and reading the current work item, dependency interface capsules, canonical substrate, and allowed artifacts.
- Import canonical value objects and enums from their owner module; add local adapters only at external JSON boundaries.
- Implement public constructors, from_dict/to_dict helpers, and stable functions that downstream consumers can call.
- Use tools to inspect dependency APIs before calling constructors, enum values, attributes, or helper functions.
- Submit exact changed_files plus compile/import/smoke/public-flow evidence.

## Avoid
- Do not copy-paste GridPoint, StableIdentifier, status enums, or other kernel-owned types into consumer modules.
- Do not edit downstream artifacts or tests to make a local slice appear complete.
