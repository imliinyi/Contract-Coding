# Current Architecture

ContractCoding currently operates as a contract-driven multi-agent orchestration system with three core layers:

1. `ContractState` as the structured source of truth.
2. `GraphTraverser` as the module-team scheduler.
3. `TaskHarness` as the execution guardrail around every agent run.

The important shift from the older version is that orchestration is no longer purely file-flat and no longer relies on raw Markdown patching as the only state model. The system now compiles the collaborative document into structured task blocks and schedules work by module team and dependency wave.

## System Overview

```mermaid
flowchart LR
    U["User Task"] --> CLI["CLI: main.py"]
    CLI --> E["Engine"]
    E --> GT["GraphTraverser"]
    E --> DM["DocumentManager"]
    E --> MP["MemoryProcessor"]

    DM --> CS["ContractState"]
    CS --> CP["ModuleTeamPlan Compiler"]

    GT --> AR["AgentRunner"]
    AR --> H["TaskHarness"]
    H --> AG["LLMAgent"]

    AG --> TOOLS["File / Code / Search / Math Tools"]
    AG --> DM

    CP --> GT
    DM --> MD["Rendered document.md"]
```

## Scheduling Model

Each file block in `Symbolic API Specifications` now includes:

- `File`
- `Module`
- `Depends On`
- `Owner`
- `Version`
- `Status`

These fields are parsed into `TaskBlock` and compiled into `ModuleTeamPlan`. A module team is the unit of parallelism. Inside each module, dependencies are used to compute the current ready wave.

```mermaid
flowchart TD
    PM["Project_Manager"] --> DOC["Contract with File / Module / Depends On / Owner / Status"]
    DOC --> ARC["Architect contract review"]
    ARC --> PLAN["Compile ModuleTeamPlan"]

    PLAN --> M1["Module Team A"]
    PLAN --> M2["Module Team B"]
    PLAN --> M3["Module Team C"]

    M1 --> W1["Ready Wave"]
    M2 --> W2["Ready Wave"]
    M3 --> B["Blocked Wave"]

    W1 --> P1["Owner Packet"]
    W2 --> P2["Owner Packet"]

    P1 --> D1["DONE"]
    P2 --> D2["DONE"]

    D1 --> R1["Critic + Code_Reviewer"]
    D2 --> R2["Critic + Code_Reviewer"]

    R1 --> V1["VERIFIED / ERROR"]
    R2 --> V2["VERIFIED / ERROR"]
```

## Harness Model

The harness now wraps implementation agents with:

- target file detection
- module-aware ownership scope
- placeholder rejection
- contract status advancement checks
- isolated execution plane support

This means the scheduler decides what should run, while the harness decides whether the execution respected the contract.

## Current Execution Planes

The current codebase supports three execution modes:

- `workspace`
  Direct execution in the base workspace. This is the current safe default.
- `sandbox`
  A disposable copied workspace for implementation tasks. Validated changes are promoted back.
- `worktree`
  A git worktree-backed isolated execution plane when the target workspace is inside a git repository. If worktree creation fails, the runtime can fall back to `sandbox`.

## Why This Matters

This architecture gives the project four properties that the original version did not reliably provide:

1. Parallelism with structure.
2. Shared-state safety.
3. Execution validation.
4. Clear boundaries for future isolation and promotion workflows.
