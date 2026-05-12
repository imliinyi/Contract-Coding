# Current Architecture

ContractCoding on `dev/long-running-agent` is **ContractSpec V8 / Runtime V5**.

The active paradigm is **Product Kernel + Canonical Substrate + Team Subcontract + Interface Capsule + Feature Slice + Quality Transaction + Repair Transaction**. The previous Runtime V4 module-team/gate-runner/failure-router path was removed from the runtime surface. OpenAI remains the primary real backend; the deterministic worker exists for offline evals and tests.

## Components

- `contract/spec.py`
  Defines `ProductKernel`, `CanonicalSubstrate`, `FeatureSlice`, `FeatureTeam`, `TeamSubContract`, `InterfaceCapsule`, `TeamSpec`, `TeamStateRecord`, `WorkItem`, `PromotionRecord`, `QualityTransactionRecord`, `RepairTransaction`, `ReplanRecord`, and backend-neutral `LLMTelemetry`.
- `contract/compiler.py`
  Freezes product semantics and team boundaries, creates feature-slice graph, emits canonical substrate dependencies, declares public behavior probes, emits team subcontracts and `INTENT` interface capsules, adds parallel capsule lock work items, runs plan-quality boundary checks, and materializes implementation/acceptance work items. It does not attempt to predesign every class or private API.
- `runtime/scheduler.py`
  Produces ready feature-team waves from dependency status, phase, and conflict keys. Capsule-lock work forms the first async phase and can run across teams in parallel. Canonical substrate slices land before consumers. A wave may contain multiple ready items for one team when its internal serial edges and conflict keys allow safe parallelism.
- `runtime/team.py`
  Creates isolated team workspaces, runs slice workers, delegates all test/review decisions to `QualityTransactionRunner`, classifies owned/unowned/missing files, promotes verified owner artifacts, and writes promotion metadata.
- `runtime/engine.py`
  Drives resumable runs through scheduler, team runtime, store, and monitor. Final integration decisions are delegated to `quality/finalization.py` so the scheduling loop does not own test/review/repair policy.
- `quality/finalization.py`
  Owns final integration coordination: run final quality transaction, mark completion, or open/block central repair through the recovery coordinator.
- `runtime/recovery.py`
  Owns central final repair transactions. It locks tests, scopes patch artifacts, detects repeated fingerprints/no-progress, opens targeted replans, or marks human-required.
- `quality/gates.py`
  Provides `SliceJudge`, `CapsuleJudge`, `IntegrationJudge`, and `RepairJudge`. Slice gates check existence, syntax/import, placeholder absence, interface contracts, slice smoke, and slice-level canonical ownership. Final integration checks required artifacts, compile/import, declared tests, public paths, declared public behavior probes, semantic lint, canonical type ownership, and unresolved marked mocks. Scale/LOC budgets are emitted as non-blocking quality signals. Repair gates run exact locked validation before promotion. Diagnostics include kernel invariant, acceptance id, artifact, and slice id where available.
- `quality/transaction.py`
  Provides the unified test+review transaction. Capsule, slice, repair, and final integration tests run first; review then decides `APPROVE`, `REQUEST_CHANGES`, `NEED_MORE_TESTS`, or `SEMANTIC_REPLAN` from the test evidence, changed files, allowed artifacts, locked tests, required OpenAI context preflight, and frozen kernel policy. It writes `.contractcoding/quality/<run-id>/<item-id>.json`.
- `knowledge/`
  Provides compact progressive-disclosure skills for product planning, feature slice design, dependency interface consumption, interface authoring, code generation, test authoring, tool use, evidence, repair, and replan. Human-visible skill files live in `knowledge/skills/<skill>/SKILL.md`; `knowledge/manager.py` merges them with fallback built-ins. `knowledge/prompting.py` builds worker packets that make agents responsible for local design and validation while the runtime stays a scheduler/gate/promotion/fallback control plane.
- `tools/`
  Provides governed OpenAI native tools. Filesystem tools handle bounded reads/writes; contract-aware tools expose `contract_snapshot`, `inspect_module_api`, and `run_public_flow` so workers can inspect contracts and verify public flows without relying on hidden context.

## Runtime Flow

1. `ContractCompiler.compile(task)` extracts required artifacts and builds the Product Kernel.
2. The compiler builds the Canonical Substrate from kernel type ownership. Shared value-object owner slices become early substrate work; enum/status owner slices wait on the value-object substrate when needed.
3. The compiler groups artifacts into feature teams and feature slices. It freezes product semantics and ownership, but leaves concrete implementation APIs progressive.
4. Each feature team receives a `TeamSubContract` and an `INTENT` `InterfaceCapsule`: public modules, capabilities, canonical imports, key signatures, examples, fixtures, smoke checks, consumers, compatibility policy, and a version.
5. Plan quality checks the team graph, canonical substrate dependencies, subcontract coverage, and capsule lock work items. A bad boundary plan is rejected before workers edit files.
6. `RunStore` persists the run record and JSONL events.
7. `Scheduler.ready_team_waves()` first exposes non-conflicting `team.capsule` waves, so multiple teams can lock interface capsules concurrently.
8. Once a team's capsule item is verified, `TeamRuntime` marks its capsule `LOCKED`; downstream work can depend on that stable contract rather than a guessed API.
9. Canonical substrate waves run before consumer implementation waves.
10. Implementation waves then run by dependency/phase/conflict keys. Runtime executes different feature-team waves concurrently. Inside one team, multiple ready slices may run in parallel only when they have no internal dependency edge and no shared conflict key.
11. `TeamRuntime` copies the base workspace into `.contractcoding/team_workspaces/<run-id>/<slice-id>/`.
12. The worker edits only `allowed_artifacts`; OpenAI workers use native tool calls through governed file/code/contract/search/math tools.
13. Agent packets include only the team subcontract, current slice, direct dependency capsules, canonical substrate, required preflight tools, and compact skills.
14. `QualityTransactionRunner` runs capsule/slice tests, then review checks whether the evidence is sufficient and whether worker claims stayed inside the contract. LOC/scale targets are quality signals, not promotion blockers.
15. `TeamRuntime` promotes only owner artifacts and writes `.contractcoding/promotions/<run-id>/<slice-id>.json`.
16. Final integration also runs as a `QualityTransaction`: `IntegrationJudge` checks required artifacts, compile/import, declared tests, kernel public paths, declared public behavior flows, canonical type ownership, semantic lint, and unresolved marked temporary mocks; review approves or routes the diagnostics.
17. Final failures go to `RecoveryCoordinator`, not back to ordinary slice teams.

## Repair And Replan

A repair transaction records:

- failure fingerprint
- root kernel invariant
- allowed artifacts
- locked tests
- validation commands
- pre-patch artifact hashes
- patch plan
- expected behavior delta
- last validation result
- attempts and no-progress count
- evidence

Repair runs in a team workspace copied from the main workspace. If exact validation fails, the patch is not promoted, so the main workspace remains unchanged.

If the same fingerprint repeats or repair produces no owned-file patch, the coordinator opens a targeted `ReplanRecord` for affected slices. If replan budget is exhausted or no legal owner can be found, the transaction becomes `HUMAN_REQUIRED`.

## Observability

`monitor --json` includes:

- run phase and status
- ready wave
- ready feature-team waves with internal parallel/serial mode
- kernel acceptance/invariants/semantic invariants
- slices and items
- teams and team workspaces
- team states, including locked capsules, ready items, active items, mailbox requests, and waiting capsules
- team subcontracts and interface capsule versions/lock status
- promotions
- quality transactions with test evidence, review evidence, verdict, and diagnostics
- repair transactions
- replans
- backend-neutral LLM telemetry
- quality signals, including requested LOC target vs current observed LOC, without treating LOC as a hard gate
- latest final diagnostics

The monitor intentionally does not render `API_KEY`, `BASE_URL`, or `API_VERSION` values.
