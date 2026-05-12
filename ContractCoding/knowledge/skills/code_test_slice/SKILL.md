---
name: code_test_slice
description: Produce and run executable tests from frozen kernel acceptance and public behavior flows.
---

## Must
- Test through public modules and commands, not private implementation details.
- Cover artifact existence, import safety, canonical type ownership, producer-consumer shape, and at least one real public flow when declared.
- For final acceptance, run scenario-style flows such as build fixture -> schedule/dispatch -> simulate -> persist -> CLI smoke.
- Keep failure output small but include the exact command, failing artifact, and invariant.

## Avoid
- Do not invent product semantics absent from kernel fixtures, formulas, public paths, or slice contracts.
- Do not mark a feature passing after only compiling files if a public flow exists.

