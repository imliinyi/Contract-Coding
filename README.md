# ContractCoding

ContractCoding is a contract-driven multi-agent coding runtime. It keeps the
control plane typed: a global project contract defines the public architecture,
teams publish contract work items and capsules, and a deterministic reducer
decides which LLM-proposed operations may enter the single source of truth.

## Current Model

- `ProjectContract` freezes user intent, bounded contexts, allowed consumers,
  cross-team invariants, and the public dependency graph.
- `TeamContract` stores each team's contract work graph, public APIs,
  dependencies, decisions, and open obligations.
- `ContractOperation` is the only cross-team exchange primitive. LLMs may
  propose typed operations; `ContractReducer` and `ContractAuditor` accept or
  reject them.
- `ContractKernel` is the executable view used by `TeamScheduler` to produce
  conflict-safe parallel waves.
- `ChangeSet` and `FileChange` record Git-like file deltas with before/after
  hashes. Workspace writes use compare-and-swap so parallel lost updates become
  explicit conflicts instead of silent overwrites.
- `ValidationEvidence` records fresh validation claims as typed, auditable
  evidence refs; failed evidence cannot satisfy `submit_evidence`.
- `InterfaceCapsuleV2` is the public contract between teams. Tasks may depend
  on declared capsules; missing dependencies become typed obligations before
  implementation.
- `TaskLedger`, `ProgressLedger`, `FailureLedger`, and `ReviewerMemory` store
  private team state under `ledgers/<team>/`.
- `WorkerPipeline` runs Inspector, Planner, Implementer, Reviewer, optional
  validation, and Judge.
- `SkillCard`s are loaded from visible skill folders under
  `ContractCoding/knowledge/skills/*/SKILL.md`.
- `RegistryTool` is the only worker-facing I/O surface. It enforces ACL and
  stamps writes with margin provenance.

## Runtime Flow

1. The service writes a frozen legacy `PlanSpec` and mirrored
   `ProjectContract`.
2. Teams are activated with a working paper, task ledger, and `TeamContract`.
3. `ProjectCoordinator.run_once()` reduces pending typed operations, audits the
   contract kernel, derives obligations, and asks `TeamScheduler` for waves.
4. The first ready wave runs through bounded parallel `WorkerPipeline`s.
5. Worker results become typed operations such as `submit_evidence` or
   `report_blocker`; reducer/auditor decide whether they update contract state.
6. Worker passes still run Inspector, Planner, Implementer, Reviewer,
   validation, and Judge for each scheduled work item.

## Registry Layout

The configured workspace root contains:

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

## Current Modules

- `ContractCoding/app/`: CLI-facing service facade.
- `ContractCoding/agents/`: coordinator, reducer, auditor, scheduler, reviewer,
  steward, and escalation actors.
- `ContractCoding/contract/`: project/team contract, work graph, typed
  operations, kernel, capsule, and lifecycle data models.
- `ContractCoding/registry/`: filesystem backend, path ACL, and agent-facing
  tool facade.
- `ContractCoding/memory/`: prompt library, skill loader, ledgers, interaction
  log, and reviewer memory.
- `ContractCoding/worker/`: packet schema, LLM protocol, worker passes, and
  pipeline glue.
- `ContractCoding/knowledge/skills/`: progressive-disclosure skill folders.
- `ContractCoding/llm/`: OpenAI-compatible LLM port.

## Development

```bash
python3 -m unittest discover -s tests
python3 ContractCoding/knowledge/skills/skill_authoring/scripts/validate_skills.py
python3 -m compileall ContractCoding main.py tests
```

Use `OFFLINE_LLM=True` or `--offline` paths for deterministic local tests.
