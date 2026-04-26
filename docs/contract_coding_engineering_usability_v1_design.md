# Contract-Coding Engineering Usability V1 Design

## 1. Background

The original Contract-Coding implementation already follows the paper's core idea: use a shared Collaborative Document as the Language Contract and Single Source of Truth. However, before this V1 change, most orchestration behavior depended on Markdown text and regular expressions. That made the framework useful as a research prototype, but fragile as an engineering system.

The V1 engineering usability work keeps the existing multi-agent architecture and Markdown document workflow, while adding a structured internal contract representation, stronger interface auditing, dependency-aware scheduling, and clearer failure reporting.

## 2. Goals

This change has four goals:

1. Preserve the human-readable Markdown Collaborative Document for LLM agents.
2. Add a machine-readable Contract Kernel for deterministic scheduling and auditing.
3. Validate Python implementations against declared contract interfaces using AST inspection.
4. Produce actionable failure information when the workflow cannot converge.

Non-goals for V1:

- No Web UI or visualization dashboard.
- No full replacement of Markdown with JSON/YAML.
- No changes to existing agent role names.
- No broad rewrite of prompts or the main execution entrypoint.

## 3. Architecture Overview

The updated architecture has two contract layers:

```text
Collaborative Document (Markdown)
        |
        v
Contract Kernel (structured Python dataclasses)
        |
        +--> Dependency-aware scheduler
        |
        +--> AST interface auditor
        |
        +--> Failure report generator
```

The Markdown document remains the source that agents read and update. The new Contract Kernel is derived from that Markdown and used internally by the system. This keeps compatibility with the existing agent workflow while reducing ambiguity for the framework itself.

## 4. Contract Kernel

### 4.1 New Data Model

A new module was added:

- `ContractCoding/memory/contract.py`

It defines the following core dataclasses:

```python
ContractKernel
ContractFile
ContractClass
ContractFunction
ContractParam
ContractAttribute
ContractParseIssue
ContractParseError
```

The kernel stores:

- file path
- owner agent
- status: `TODO`, `IN_PROGRESS`, `DONE`, `ERROR`, `VERIFIED`
- version
- classes
- functions/methods
- function parameters and return types
- dependency relationships between files

### 4.2 Markdown Compatibility

The parser reads the existing `### 2.4 Symbolic API Specifications` section and extracts file blocks like:

```markdown
**File:** `models.py`
* **Class:** `Player`
* **Owner:** Backend_Engineer
* **Version:** 1
* **Status:** TODO
```

The parser also reads `### 2.3 Dependency Relationships(MUST):` and extracts dependency lines such as:

```markdown
- `service.py` depends on `models.py`
```

### 4.3 DocumentManager Integration

`DocumentManager` now exposes:

```python
DocumentManager.get_kernel() -> ContractKernel
```

This method parses the current Markdown document and returns the structured kernel. If parsing fails, it raises `ContractParseError` with file-specific, field-specific issues.

## 5. AST Interface Auditing

The audit layer was expanded in:

- `ContractCoding/memory/audit.py`

### 5.1 New Audit API

```python
audit_contract_interfaces(kernel: ContractKernel, workspace_path: str) -> list[AuditIssue]
```

`AuditIssue` contains:

```python
path: str
severity: "error" | "warning"
kind: str
message: str
expected: str | None
actual: str | None
```

### 5.2 What It Checks

For Python files, the AST auditor checks:

- whether each contract-declared file exists
- whether declared classes exist
- whether declared methods/functions exist
- whether parameter names match
- whether available parameter type annotations match
- whether available return type annotations match
- whether concrete code contains `pass`
- whether source text contains `TODO` or `placeholder`

For non-Python files, V1 keeps the existing existence/version style checks and does not attempt AST-level validation.

### 5.3 Severity Rules

- Missing file, missing class, missing method/function, parameter mismatch, type mismatch, and placeholder code are `error`.
- Missing return annotation is a `warning` when the contract declares a return type but the implementation omits the annotation.

## 6. Dependency-Aware Scheduling

The scheduler was refactored in:

- `ContractCoding/orchestration/traverser.py`

### 6.1 Previous Behavior

Before V1, scheduling primarily parsed Markdown blocks directly and dispatched tasks based on status and owner.

### 6.2 New Behavior

Scheduling now uses `DocumentManager.get_kernel()` as the primary source.

Rules:

- `VERIFIED` files are never scheduled again.
- `DONE` files are sent to `Critic` and `Code_Reviewer`.
- `TODO`, `IN_PROGRESS`, and `ERROR` files are sent to their owner only when dependencies are satisfied.
- A dependency is satisfied only when the dependency file has status `VERIFIED`.
- If all remaining tasks are blocked by unmet or unknown dependencies, the scheduler sends a repair task to `Project_Manager`.
- If the Contract Kernel cannot be parsed, implementation does not proceed; the scheduler asks `Project_Manager` to repair the contract.

### 6.3 Review Task Improvements

When DONE files are sent for review, the scheduler now includes structured interface audit findings in the review prompt. This gives `Critic` and `Code_Reviewer` concrete evidence instead of asking them to rediscover every issue manually.

## 7. Failure Reporting

Failure reporting was added in:

- `ContractCoding/orchestration/traverser.py`
- `ContractCoding/orchestration/engine.py`

When the workflow cannot finish with all files `VERIFIED`, the system can produce a structured report containing:

- unfinished file paths
- current status
- owner
- dependency-blocking reasons
- recent interface audit issues
- fallback reason such as pending/incomplete agent work
- configured `MAX_LAYERS`

`Engine.run()` now returns this failure report when no terminating success state is produced.

## 8. Engine Integration Fixes

`ContractCoding/orchestration/engine.py` was updated to reset runtime state more safely.

The previous implementation recreated `DocumentManager` in `_run_single_step()`, but the already-created `AgentRunner` and `GraphTraverser` still held references to the older document manager. V1 adds `_reset_run_state()` so the same fresh document manager is shared consistently by:

- `Engine`
- `AgentRunner`
- `GraphTraverser`

The engine also runs the new interface audit during final audit logging.

## 9. Tests Added

A new test file was added:

- `tests/test_contract_kernel_and_audit.py`

It covers:

1. Markdown Contract parsing into `ContractKernel`.
2. Missing required fields producing `ContractParseError`.
3. AST audit detecting signature mismatch and placeholder implementation.
4. Dependency-aware scheduling blocking downstream work until dependencies are verified.

The test file stubs `langgraph` and `openai` so the focused tests can run in a lightweight environment without initializing the full LLM stack.

## 10. Files Changed

### Added

- `ContractCoding/memory/contract.py`
- `tests/test_contract_kernel_and_audit.py`
- `docs/contract_coding_engineering_usability_v1_design.md`

### Modified

- `ContractCoding/memory/audit.py`
- `ContractCoding/orchestration/traverser.py`
- `ContractCoding/orchestration/engine.py`

## 11. Verification

The following checks were run against a downloaded copy of the updated repository:

```bash
python -m unittest tests.test_contract_kernel_and_audit
python -m compileall ContractCoding tests
```

Results:

- Unit tests passed: 4 tests OK.
- Python compilation passed for `ContractCoding` and `tests`.
- A pre-existing logger `ResourceWarning` appeared during tests, but it did not fail the test run.

## 12. Summary of What Changed

In short, V1 changed the system from:

```text
Markdown contract -> regex scheduling -> basic file/version audit
```

to:

```text
Markdown contract -> structured Contract Kernel -> dependency-aware scheduling -> AST interface audit -> structured failure report
```

This makes the framework more deterministic, easier to debug, and closer to an engineering-ready version of the Contract-Coding idea.
