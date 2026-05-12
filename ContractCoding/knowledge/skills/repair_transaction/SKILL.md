---
name: repair_transaction
description: Repair one failure fingerprint with locked tests, bounded production edits, and exact validation evidence.
---

## Must
- Cluster diagnostics into a root cause before editing; name the invariant, failing flow, and affected public API.
- Patch only allowed artifacts and keep locked tests unchanged.
- Prefer fixing canonical owner modules and adapters over spreading compatibility hacks across consumers.
- Run the locked validation commands and any public behavior flow that failed before submit_result.
- Escalate to replan or human-required when no legal owner or invariant update exists.

## Avoid
- Do not route final integration failures back to ordinary feature teams.
- Do not claim repair success without a production patch and exact validation evidence.

