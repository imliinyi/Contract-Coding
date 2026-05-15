# Current Architecture

This document describes the code currently present in the repository. It does
not describe removed Runtime V4/V5 modules or planned quality-transaction
systems that are not implemented in this tree.

## Layers

- `contract/`
  Defines the durable public model: `ProjectContract`, `TeamContract`,
  `ContractOperation`, `ContractObligation`, `ContractKernel`,
  `TeamWorkItem`, `TeamWave`, `ChangeSet`, `ValidationEvidence`, and
  `InterfaceCapsuleV2`.
- `registry/`
  Provides the filesystem registry, path-scoped ACL, and `RegistryTool`.
  Worker code should use `RegistryTool`, not the backend directly.
- `memory/`
  Stores role prompts, skill cards, ledgers, interaction logs, failure memory,
  and reviewer memory.
- `knowledge/skills/`
  Contains human-visible skill folders. Runtime skills expose a short
  `## Runtime prompt`; heavier notes and helper programs live in
  `references/` and `scripts/`.
- `worker/`
  Contains `ContextPacket`, the LLM port, pass implementations, and
  `WorkerPipeline`.
- `agents/`
  Contains the coordinator, reducer, auditor, scheduler, reviewer, steward,
  and escalation logic.
- `app/`
  Wraps the coordinator and per-team pipelines for CLI/service use.

## Contract Runtime

The runtime is contract-driven rather than message-subscription-driven:

1. `ProjectContract` is the global SSOT, mirrored from the legacy `PlanSpec`
   for CLI compatibility.
2. `TeamContract` contains each team's schedulable `TeamWorkItem`s, public
   APIs, dependencies, decisions, and obligation references.
3. Agents may only propose typed `ContractOperation`s. Free text may explain a
   rationale, but it is not used for scheduling.
4. `ContractAuditor` validates claims against registry state: files, capsule
   existence, symbols, and validation evidence.
5. `ContractReducer` accepts or rejects operations and is the only component
   that mutates contract state.
6. `TeamScheduler` derives `TeamWave`s from `ContractKernel`, blocking missing
   dependencies and packing only non-conflicting work into the same wave.
7. `ProjectCoordinator.run_once()` executes the first ready wave with bounded
   parallelism, then turns worker verdicts into typed operations.
8. Worker writes produce `ChangeSet` records. Compare-and-swap workspace writes
   reject lost updates when another worker changes the same file after the base
   read.

This keeps cross-team coordination in typed contract state instead of natural
language message streams.

## Worker Pipeline

The worker pipeline runs these stages:

1. Inspector
   Pulls declared capsule dependencies, cheap L1 neighboring capsule tags,
   prior failures, and role-specific skills.
2. Planner
   Produces a bounded slice plan.
3. Implementer
   Writes artifacts to `workspace/<team>/` through `RegistryTool`.
4. Reviewer
   Independently reviews artifacts and updates reviewer memory.
5. Validation
   Optional smoke runner can attach fresh pass/fail evidence.
6. Judge
   Aggregates blockers, reviewer concerns, smoke result, and reviewer-memory
   signals into an approve/reject verdict.

Each pass should use a role-bound `RegistryTool` so progress entries and
events carry accurate margin provenance.

The pipeline executes a scheduled `TeamWorkItem`. Legacy `TaskItem` inputs are
projected into work items during team activation so existing CLI JSON remains
compatible.

If a work item declares `writes`, the implementer may only write those files or
paths below those declared prefixes. Out-of-bound artifacts become blockers and
are not written.

## Skill Loading

`ContractCoding.memory.skills` loads cards from
`ContractCoding/knowledge/skills/*/SKILL.md`.

Runtime frontmatter fields:

```yaml
name: planner-repo-survey
description: Use when planning coding work that needs repository orientation.
skill_id: planner_repo_survey
title: Start from a repo map
applicable_roles: planner
tags: planning,context
applicability: always
```

`runtime: false` keeps meta skills, authoring guidance, and reference-only
skills out of worker packets.

Inspector stores pulled fragments by role in `ContextPacket.skill_fragments_by_role`.
Prompts should read only the current role's fragments.

## Blocking Policy

- Missing capsule dependencies become `ContractObligation`s and are blocked by
  the scheduler before the worker runs.
- Worker-local blockers stop the pipeline before Planner/Implementer.
- Rejected tasks are marked `BLOCKED`, not left `ACTIVE`.
- If artifacts are produced and validation is required, missing validation
  evidence is a blocker.

This keeps the runtime from converting incomplete context or unverified output
into accepted work.

## Registry Layout

```text
plan.json
contract/project.json
contract/teams/<team>.json
contract/operations.jsonl
contract/obligations.jsonl
contract/schedule.jsonl
contract/evidence.jsonl
events.log
capsules/<team>/<capability>.json
ledgers/<team>/working_paper.json
ledgers/<team>/task_ledger.json
ledgers/<team>/progress_ledger.jsonl
ledgers/<team>/failure_ledger.jsonl
ledgers/<team>/reviewer_memory.json
workspace/<team>/
escalations/<id>.json
```

## Known Gaps

- Workspace write policy currently ensures writes stay under the team
  workspace; `TeamWorkItem.writes` and conflict keys are now available for
  stricter task-level enforcement.
- Validation evidence is typed and persisted, but real command execution
  provenance can still become richer once external test runners are wired in.
- Capsule reads still materialize full capsule objects internally. A stricter
  layer-read API should eventually preserve L1/L2/L3 boundaries end to end.
