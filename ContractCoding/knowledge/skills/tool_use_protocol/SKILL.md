---
name: tool_use_protocol
description: Use filesystem, contract, API-inspection, and validation tools in short auditable loops.
---

## Must
- Use contract_snapshot before complex edits to see current slice, allowed artifacts, team subcontract, dependency capsules, canonical substrate, and public flows.
- Use inspect_module_api or inspect_symbol before consuming a dependency API.
- Use run_public_flow when a declared public behavior probe exists, especially before final submit_result.
- Keep long outputs in files or evidence summaries; stop tool use once changed_files and validation evidence are ready.

## Avoid
- Do not infer hidden global context when a contract or API inspection tool can answer it.
- Do not repeatedly call broad search/read tools after the relevant owner artifact and symbol are known.
