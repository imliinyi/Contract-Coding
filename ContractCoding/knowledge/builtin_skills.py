"""Built-in MVP skills for ContractCoding Runtime V4."""

BUILTIN_SKILL_RECORDS = [
    {
        "name": "general.delivery",
        "description": "General WorkItem completion discipline for all tasks.",
        "allowed_work_kinds": ["*"],
        "trigger_keywords": ["contract", "work item", "evidence", "scope"],
        "output_schema": {"output": "concise result", "evidence": "artifact paths, tests, or blocker"},
        "evidence_requirements": ["artifact path or explicit blocker", "acceptance criteria mapping"],
        "risk_policy": "Never expand beyond target artifacts or conflict keys.",
        "priority": 10,
        "prompt": """
Use the compiled contract as the source of truth. Before acting, identify:
- current WorkItem id, kind, scope, target artifacts, conflict keys, dependencies
- acceptance criteria and evidence needed
- whether the work is read-only, mutable, serial, or parallel-safe

Finish with concrete evidence, artifact paths, or a blocker with the missing input.
Do not expand scope just because nearby work is visible.
""".strip(),
    },
    {
        "name": "coding.implementation",
        "description": "Implement scoped code work with tests and no out-of-scope writes.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["implement", "fix", "refactor", "test", "api"],
        "output_schema": {"changed_files": "list[str]", "tests": "commands/results or reason unavailable"},
        "evidence_requirements": ["changed target artifacts", "syntax/import/test evidence when possible"],
        "tool_hints": [
            "file_tree",
            "search_text",
            "read_lines",
            "inspect_symbol",
            "create_file",
            "replace_file",
            "update_file_lines",
            "replace_symbol",
            "report_blocker",
            "submit_result",
        ],
        "risk_policy": "Write only target artifacts unless the contract explicitly allows shared edits.",
        "priority": 20,
        "prompt": """
For coding WorkItems:
- inspect existing project style and interfaces before editing
- write only target artifacts unless the contract says otherwise
- if an allowed target artifact is missing, create it; absence of the package tree is not a blocker for a new artifact
- if an existing target is still a scaffold or needs complete replacement, use replace_file so old trailing placeholder code is truncated
- obey provided_interfaces and required_interfaces exactly
- if dependency_policy=interface, code against the declared signatures without copying dependency implementations
- keep APIs small and importable
- add or run focused tests when risk justifies it
- for test artifacts, import and exercise the actual target package/modules from the contract; do not skip because a guessed API is missing
- avoid placeholders, TODOs as behavior, broad rewrites, and hidden global state
- if parallel teammates exist, assume files outside conflict keys may be changing
- during repair, use the provided diagnostic first; if the needed file is outside target artifacts, return a blocker naming that file instead of editing around it

Evidence should include changed paths and test or import results.
""".strip(),
    },
    {
        "name": "coding.code_generation_workflow",
        "description": "Generates scoped implementation artifacts for coding agents. Use for Backend_Engineer, Frontend_Engineer, Algorithm_Engineer, multi-file package work, public API wiring, and code creation/repair packets.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": [
            "backend_engineer",
            "frontend_engineer",
            "algorithm_engineer",
            "implementation",
            "generate",
            "public api",
            "target artifacts",
            "multi-file",
        ],
        "output_schema": {
            "changed_files": "all target artifacts created or updated",
            "symbol_closure": "public calls/imports verified or blocker",
            "evidence": "compile/import/focused checks or reason unavailable",
        },
        "evidence_requirements": [
            "every target artifact exists",
            "public symbols called by entrypoints are defined/importable",
            "changed paths mapped to acceptance criteria",
        ],
        "tool_hints": [
            "file_tree",
            "search_text",
            "read_lines",
            "inspect_symbol",
            "create_file",
            "replace_file",
            "update_file_lines",
            "replace_symbol",
            "report_blocker",
            "submit_result",
        ],
        "risk_policy": "No undefined public helper calls, no placeholder artifacts, no writes outside allowed artifacts/conflict keys.",
        "priority": 12,
        "prompt": """
This is an executable code-generation workflow, not role prose.

Workflow:
1. Inventory the exact target artifacts and acceptance criteria in the Agent Input Packet.
2. Read the nearest real dependencies, existing package exports, and public entrypoint patterns before editing.
3. Create or replace every target artifact assigned to this packet. Missing directories for listed targets are normal.
4. Keep the implementation vertically executable: public entrypoint -> domain/core behavior -> IO/result where applicable.
5. Run a symbol-closure pass before submitting:
   - every public function/class imported by this artifact exists in the target or an allowed dependency
   - every cross-scope constructor/function call uses the exact producer signature; inspect the real artifact or frozen interface before calling it
   - every helper called by CLI/API/REPL/public module code is defined or imported
   - package __init__ exports match downstream imports
   - no TODO/pass/placeholder behavior remains in target artifacts
6. For adapter/entrypoint code, add a tiny consumer-contract probe in evidence: producer symbol, signature checked, and public path exercised or deferred.
7. If the required fix is outside allowed artifacts, return a structured blocker naming the exact file and symbol.

Pre-submit evidence must list created/updated files and one executable check when available. Do not claim completion if
any target artifact is absent or a public call path contains an undefined name.
""".strip(),
    },
    {
        "name": "coding.test_generation_workflow",
        "description": "Creates and repairs executable test artifacts for Test_Engineer packets, missing tests, final gates, team gates, unittest generation, CLI smoke, and regression/integration coverage.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": [
            "test_engineer",
            "tests/",
            "unittest",
            "test_regeneration",
            "missing_test_artifact",
            "team gate",
            "final gate",
            "regression",
            "integration",
        ],
        "output_schema": {
            "test_files": "all target tests created or updated",
            "coverage_map": "contract/public behavior covered by each test file",
            "blocker": "implementation bug or ambiguous public API if tests cannot honestly pass",
        },
        "evidence_requirements": [
            "every target test artifact exists",
            "tests import the real generated package",
            "implementation failures are reported instead of weakening tests",
        ],
        "tool_hints": [
            "file_tree",
            "search_text",
            "read_lines",
            "inspect_symbol",
            "create_file",
            "replace_file",
            "update_file_lines",
            "report_blocker",
            "submit_result",
        ],
        "risk_policy": "Missing target tests are never optional; failing real behavior must route to implementation repair, not test weakening.",
        "priority": 13,
        "prompt": """
This is an executable test-generation workflow, not a generic quality prompt.

Workflow:
1. Treat the Target files list as mandatory. Create or update every listed tests/*.py artifact before touching optional tests.
2. Inspect real package modules, public entrypoints, and exported symbols. Tests must import the real generated package.
3. Cover public behavior first: package imports, core domain behavior, boundary adapters, save/load, CLI/API smoke, and one realistic scenario when present.
4. Use unittest and deterministic temporary files/fixtures. Mock only external IO, stdin/stdout, clocks, randomness, or subprocess boundaries.
5. Do not create empty, skip-only, mock-only, or private-implementation snapshot tests.
6. If a real public command/import fails because production code has an undefined symbol, wrong export, or bad runtime behavior,
   report an implementation_bug blocker with the suspected artifact and symbol. Do not weaken or delete the test to match the bug.
7. Existing valid tests are locked unless this packet explicitly says invalid_tests or test_repair.

Completion condition: every target test file exists, contains executable unittest cases, imports real package modules, and maps to the
contract or public API. Anything less is a blocker, not success.
""".strip(),
    },
    {
        "name": "coding.review_gate_workflow",
        "description": "Reviews generated code/tests at team and final gates. Use for reviewers, evaluator gates, deterministic evidence, missing artifacts, undefined public calls, false-done detection, and repair routing.",
        "allowed_work_kinds": ["eval"],
        "trigger_keywords": [
            "review",
            "reviewer",
            "gate",
            "deterministic evidence",
            "missing artifact",
            "undefined",
            "false done",
            "blocker",
        ],
        "output_schema": {
            "verdict": "pass|pass_with_risks|fail|blocked",
            "blocking_findings": "artifact/symbol/test evidence",
            "repair_route": "implementation_repair|test_regeneration|replan|infra|human_required",
        },
        "evidence_requirements": [
            "deterministic evidence considered first",
            "missing target artifacts block",
            "undefined public calls block",
            "test weakening blocks",
        ],
        "tool_hints": ["read_file", "search_text", "run_code", "report_blocker", "submit_result"],
        "risk_policy": "Narrative review cannot pass deterministic failures or incomplete target artifact creation.",
        "priority": 14,
        "prompt": """
Gate review workflow:
1. Start from deterministic evidence: compile/import/test output, missing target artifacts, promotion metadata, and diagnostics.
2. Block missing artifacts. A packet that created 2 of 7 required test files is not partial success for a gate.
3. Block undefined public calls, missing exports, import-time crashes, and CLI/API paths that call helpers that do not exist.
4. Block false-done tests: skip-only, mock-only, tests rewritten to match a production bug, or tests that avoid the declared public path.
5. Route repairs by root cause:
   - missing tests -> test_regeneration on the missing tests/*.py files
   - invalid tests -> test_repair on the named tests/*.py files
   - executable public behavior fails -> implementation_repair on the suspected production owner, tests locked
   - contract/interface gap -> replan or human_required
6. Return pass only when deterministic evidence is clean or remaining risks are explicitly nonblocking.
""".strip(),
    },
    {
        "name": "coding.tool_discipline",
        "description": "Use ContractCoding tools progressively so writes stay reviewable and repairable.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["tool", "write_file", "update_file_lines", "replace_symbol", "repair", "rollback"],
        "output_schema": {"tool_flow": "read -> narrow edit -> validation feedback", "blocker": "structured blocker when scope is wrong"},
        "evidence_requirements": ["changed files", "tool validation feedback or deterministic self-check"],
        "tool_hints": ["read_lines", "inspect_symbol", "replace_file", "update_file_lines", "replace_symbol", "create_file", "report_blocker"],
        "risk_policy": "Use whole-file replacement for scaffold replacement; keep localized repairs narrow.",
        "priority": 24,
        "prompt": """
Use tools in this order of preference:
- New missing artifact: create_file, or replace_file when producing the complete initial file.
- Missing target directories are normal for new projects; create_file should create the allowed path instead of reporting a blocker.
- Existing scaffold or mostly generated file: replace_file with the full final file so old placeholder tail code is truncated.
- Existing file small repair: read_lines, then update_file_lines.
- Function/class repair after syntax or indentation feedback: inspect_symbol, then replace_symbol.
- Small insertion: add_code.
- Search first when the symbol/location is unclear: search_text or code_outline.

When a tool result says validation_status=rolled_back, the patch was not kept. Read the suggested range and repair again
inside the same target artifact. Do not repeat the same blind line patch. If the needed file is outside allowed_artifacts,
use report_blocker or return structured blocker JSON.
""".strip(),
    },
    {
        "name": "coding.public_entrypoint_import_safety",
        "description": "Keep CLI/API/REPL modules import-safe across isolated functional teams.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["cli", "api", "repl", "entrypoint", "import-safe", "lazy import"],
        "output_schema": {"entrypoints": "public commands/functions", "lazy_dependencies": "modules imported inside handlers"},
        "evidence_requirements": ["entrypoint module imports before downstream teams promote", "public smoke path after integration"],
        "tool_hints": ["read_file", "update_file_lines", "replace_symbol"],
        "risk_policy": "Public entrypoints must not hard-import downstream team modules at top level.",
        "priority": 25,
        "prompt": """
For CLI/API/REPL artifacts:
- keep top-level imports limited to stdlib and same-file-safe constants
- lazy import domain/core/ai/io modules inside command handlers or helper functions
- tolerate missing optional downstream modules with clear runtime errors, not import-time crashes
- expose public parser/dispatch/main functions without running interactive code at import time
- final smoke must exercise real commands such as scenarios, status, run --turns, run --ai, save/load, and plan
- before constructing domain/core/io objects, inspect their dataclass/class/function signatures and adapt CLI input to that exact shape
- evidence must name the cross-scope producer signatures checked for each state-changing command

Do not prove an entrypoint by testing only internal helpers; the public module itself must import cleanly in an isolated
team workspace before other teams are promoted.
""".strip(),
    },
    {
        "name": "coding.repair",
        "description": "Repair a failed coding gate from structured diagnostics without blind rewrites.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["repair", "diagnostic", "failure", "traceback", "assertion"],
        "output_schema": {"changed_files": "list[str]", "blocker": "out-of-scope or ambiguous diagnostic"},
        "evidence_requirements": ["diagnostic fingerprint or failing test", "changed target artifacts or blocker"],
        "tool_hints": [
            "search_text",
            "read_lines",
            "inspect_symbol",
            "update_file_lines",
            "replace_symbol",
            "report_blocker",
            "submit_result",
        ],
        "risk_policy": "Never repair by changing unrelated artifacts or weakening tests unless assigned a test artifact.",
        "priority": 35,
        "prompt": """
For repair WorkItems:
- start from the latest diagnostic: failing_test, expected_actual, traceback, and suspected implementation artifacts
- treat repair_ticket_id/lane as the active objective; resolve that exact failure before broad cleanup
- make the smallest change in the current target artifacts that can satisfy the diagnostic
- before editing, read the failing function/class or the target line range named by the diagnostic
- when syntax/indentation was rolled back, inspect and replace the enclosing function/class instead of stacking line patches
- if the diagnostic points to a different file, return an out-of-scope blocker naming the exact artifact
- do not edit tests for assertion/runtime/import failures; test artifacts are editable only for invalid_tests/test_repair diagnostics
- if the test appears to assert behavior not declared by the contract/interface, say `suspect_invalid_test` with the reason
- do not weaken production behavior just to satisfy a brittle test

Evidence should map the diagnostic to the edited artifact or explicit blocker.
""".strip(),
    },
    {
        "name": "coding.public_entrypoint_acceptance",
        "description": "Turn contract acceptance into executable public entrypoint smoke checks.",
        "allowed_work_kinds": ["coding", "eval"],
        "trigger_keywords": ["cli", "api", "entrypoint", "smoke", "final gate", "acceptance"],
        "output_schema": {
            "entrypoint_matrix": "commands or API calls with expected exit/status/output",
            "coverage_gaps": "public paths not yet exercised",
        },
        "evidence_requirements": [
            "each declared public command/API is executed at least once",
            "exit code/status and machine-readable output are checked",
            "stateful paths prove before/after state changes",
        ],
        "tool_hints": ["run_code", "read_file"],
        "risk_policy": "Do not treat helper-level tests as proof that a public entrypoint works.",
        "priority": 14,
        "prompt": """
For public entrypoints, compile acceptance into a concrete smoke matrix before declaring success.

Required discipline:
- enumerate every declared CLI subcommand, API route, library entrypoint, or executable script
- for each path, run the real public entrypoint, not only internal helpers
- check exit code/status, parseable output, and at least one semantic field
- for state-changing commands, verify before/after state changes and saved artifacts can be read back
- include option combinations that change behavior, such as --output, --ai, --json, --turns, or scenario/save inputs
- never accept `engine.run_simulation()` as proof that `cli run <scenario>` works unless the CLI path itself is executed

When writing tests, prefer end-to-end public calls first, then add smaller unit tests for failure localization.
Evidence should list the exact commands/API calls and the assertions made on their outputs.
""".strip(),
    },
    {
        "name": "coding.integration_boundary",
        "description": "Catch cross-team semantic mismatches at generated-code boundaries.",
        "allowed_work_kinds": ["coding", "eval"],
        "trigger_keywords": ["integration", "interface", "boundary", "scenario", "save", "planner", "engine"],
        "output_schema": {
            "boundary_paths": "producer -> adapter -> consumer paths",
            "mismatch_risks": "data shapes or semantics that can drift",
        },
        "evidence_requirements": [
            "at least one test crosses each producer/consumer boundary",
            "immutable/serializable/runtime-state objects are not confused",
            "planner output is adapted to engine input where applicable",
        ],
        "tool_hints": ["read_file", "run_code"],
        "risk_policy": "Do not let each team pass locally while the composed product path is untested.",
        "priority": 15,
        "prompt": """
For multi-team coding work, explicitly test the boundaries where independent teams meet.

Common high-risk boundaries:
- scenario description -> runtime state -> engine mutation
- planner recommendation -> engine action input
- save document -> runtime state -> resumed simulation
- domain dataclass/object -> JSON output -> CLI/API response
- package exports -> downstream imports
- CLI/API input -> domain event/dataclass constructor

Before final success, ask: "Did the test use the same object shape and public path that a user will use?"
If a test constructs a convenient dict while the real entrypoint returns a dataclass or save object, add a real-path test.
If a producer returns a plan/report object and the consumer expects action mappings, add an adapter or return a blocker.
If a consumer constructs a producer dataclass/class, verify required fields and keyword names against the producer source.

Evidence should name each boundary and the command/test that exercises it end to end.
""".strip(),
    },
    {
        "name": "coding.large_project_generation",
        "description": "Generate large projects by functional slices while preserving executable acceptance.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["large project", "package", "module", "team", "simulation", "game"],
        "output_schema": {
            "implemented_capabilities": "features mapped to artifacts",
            "self_checks": "compile/import/unit/public smoke results",
        },
        "evidence_requirements": [
            "functional capabilities map to owned artifacts",
            "no padding/placeholders/fake tests",
            "public acceptance paths pass after integration",
        ],
        "tool_hints": ["file_tree", "search_text", "read_lines", "inspect_symbol", "create_file", "update_file_lines", "replace_symbol"],
        "risk_policy": "Prefer meaningful feature depth over line-count padding or file-level fragmentation.",
        "priority": 15,
        "prompt": """
For large generated projects:
- organize work by functional bounded context, not one team per file
- implement cohesive behavior behind stable public interfaces before filling secondary helpers
- keep generated code meaningful: no padding, dead branches, fake tests, skip-only tests, or placeholder text
- make tests exercise real package imports and public behavior, not monkeypatched success paths
- include representative happy paths, error paths, persistence/serialization paths, and one realistic user scenario
- after local unit tests pass, run public smoke paths that mirror how the package is actually used

If a feature is too broad for one step, return a scoped blocker or partial evidence rather than inventing unsupported behavior.
Evidence should connect capabilities to files and executable checks.
""".strip(),
    },
    {
        "name": "coding.phase_contract",
        "description": "Execute only the active PhaseContract and preserve phase handoff boundaries.",
        "allowed_work_kinds": ["coding", "doc"],
        "trigger_keywords": ["phase", "phase_contract", "vertical_slice", "handoff", "milestone"],
        "output_schema": {
            "phase_scope": "current phase id and intended deliverables",
            "blocker": "missing frozen interface or out-of-phase request",
        },
        "evidence_requirements": ["current phase id", "artifact handoff or blocker"],
        "tool_hints": ["read_lines", "create_file", "update_file_lines", "report_blocker", "submit_result"],
        "risk_policy": "Do not implement future phase features unless they are required for the current phase gate.",
        "priority": 18,
        "prompt": """
For phase-contract runs:
- treat the Agent Input Packet phase_id and phase_goal as the active contract
- implement only current-phase deliverables and directly required support code
- do not fill future feature breadth during the vertical slice
- if a future-phase artifact is necessary to make the current phase testable, return a blocker with the artifact and reason
- preserve handoff artifacts such as PRD, interfaces, scaffold manifests, and phase evidence
- keep acceptance evidence tied to the current phase gate

This keeps large projects convergent: a small real path first, then broader feature phases.
""".strip(),
    },
    {
        "name": "coding.vertical_slice",
        "description": "Build a minimal real end-to-end path before expanding a large project.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["vertical_slice", "minimal scenario", "end-to-end", "critical interface"],
        "output_schema": {
            "slice_path": "domain -> core -> io/interface path exercised",
            "evidence": "compile/import/smoke result or blocker",
        },
        "evidence_requirements": ["real public path", "no private API guessing"],
        "tool_hints": ["read_lines", "inspect_symbol", "create_file", "update_file_lines", "submit_result"],
        "risk_policy": "Prefer one executable public scenario over broad incomplete feature coverage.",
        "priority": 19,
        "prompt": """
For vertical slice work:
- create the smallest real flow that crosses domain, core, IO, and interface where those teams are in scope
- use frozen critical interfaces and scaffold manifests as the public contract
- keep behavior modest but executable: package import, construct state, run one turn/action, serialize or render a public result
- defer secondary systems, optional policies, and large helper surfaces to later feature phases
- tests/smoke should use public APIs and real package imports, not mocks of internal modules

Evidence should name the public path and the artifact(s) that make it run.
""".strip(),
    },
    {
        "name": "coding.integration_repair",
        "description": "Repair integration failures through owned bundles; final-gate implementation repair is centralized.",
        "allowed_work_kinds": ["coding"],
        "trigger_keywords": ["integration", "repair", "final gate", "phase_convergence", "diagnostic", "centralized"],
        "output_schema": {"owner_fix": "artifact and symbol repaired", "blocker": "true external owner/interface mismatch"},
        "evidence_requirements": ["diagnostic fingerprint", "owner artifact bundle", "verification feedback"],
        "tool_hints": ["read_lines", "inspect_symbol", "update_file_lines", "replace_symbol", "report_blocker"],
        "risk_policy": "Implementation failures repair implementation; tests change only for invalid_tests diagnostics.",
        "priority": 21,
        "prompt": """
For integration repair:
- start from the structured diagnostic, failing command, failing test, expected/actual, and owner artifacts
- for final-gate centralized repair, treat the entire allowed_artifacts bundle as the owner; repair cross-artifact state/IO/interface mismatches in one coherent sequence
- for ordinary team repair, repair only the owner artifact; do not rewrite integration tests unless the diagnostic says invalid_tests
- if the required API is outside allowed artifacts, return a structured blocker; never blocker on a file already listed in allowed_artifacts
- make the narrowest repair that can pass the current phase/final gate
- keep previous verified behavior intact; do not broad-rewrite a working module to satisfy one assertion

Evidence should connect the diagnostic to the edited symbol or explicit blocker.
""".strip(),
    },
    {
        "name": "coding.final_recovery",
        "description": "Route and execute final-gate recovery through a centralized convergence lane.",
        "allowed_work_kinds": ["coding", "eval"],
        "trigger_keywords": ["final", "recovery", "gate", "diagnostic", "missing_test_artifact", "test_regeneration", "centralized_convergence"],
        "output_schema": {
            "root_cause": "missing_test_artifact|invalid_test|implementation_bug|interface_contract_gap|infra|human_required",
            "repair_lane": "test_regeneration|implementation_repair|interface_replan|infra_retry|needs_human",
            "allowed_artifacts": "files this worker may edit",
            "locked_artifacts": "files this worker must not edit",
        },
        "evidence_requirements": [
            "diagnostic fingerprint",
            "root cause category",
            "owner scope and allowed artifacts",
        ],
        "tool_hints": ["read_file", "search_text", "create_file", "update_file_lines", "report_blocker", "submit_result"],
        "risk_policy": "Final recovery routes the smallest valid lane; implementation failures use one centralized convergence item with tests locked.",
        "priority": 16,
        "prompt": """
Final recovery discipline:
- Classify before editing: missing_test_artifact, invalid_test, implementation_bug, interface_contract_gap, infra, or human_required.
- If tests/*.py is missing, regenerate that test artifact in the test_regeneration lane. Do not reopen implementation workers.
- If a generated test is structurally invalid, repair only the test artifact named by the diagnostic.
- If an executable final test or blackbox check fails against real public behavior, repair it in the centralized final convergence item. That item may edit every implementation file in allowed_artifacts and should not send the failure back to the original domain/core/io/interface team.
- If the failure is an interface/contract mismatch, request replan rather than broad implementation repair.
- If the same final diagnostic repeats, stop blind broadening and return a structured blocker or replan request.

When assigned test artifacts, create executable unittest tests that import real package modules and avoid all-skip/mock-only
success. When assigned centralized implementation artifacts, treat tests as locked, fix the directly failing public behavior
first, then fix dependent state/persistence/planning mismatches in the same allowed bundle.
""".strip(),
    },
    {
        "name": "coding.evaluator_gate",
        "description": "Evaluate phase and final gates conservatively against real behavior.",
        "allowed_work_kinds": ["eval", "coding"],
        "trigger_keywords": ["evaluator", "gate", "phase gate", "final gate", "acceptance"],
        "output_schema": {
            "findings": "structured blocking/nonblocking findings",
            "evidence": "commands, artifacts, assertions",
        },
        "evidence_requirements": ["deterministic command output", "criterion-to-evidence mapping"],
        "tool_hints": ["run_code", "read_file"],
        "risk_policy": "Never pass deterministic failures; LLM review is advisory only.",
        "priority": 22,
        "prompt": """
For evaluator gates:
- judge only the active phase or final acceptance criteria
- prefer real compile/import/unittest/CLI/save-load/scenario checks over visual inspection
- flag fake completion: placeholders, skip-only tests, mock-only internal success, private API guesses, padding
- output blocking findings with owner team/artifact and a repair instruction
- do not pass because code looks plausible when public behavior has not been executed

Deterministic failures always block; narrative review cannot override them.
""".strip(),
    },
    {
        "name": "research.synthesis",
        "description": "Collect source-backed facts without mutating the workspace.",
        "allowed_work_kinds": ["research"],
        "trigger_keywords": ["research", "source", "survey", "evidence"],
        "output_schema": {"claims": "source-backed bullets", "uncertainty": "unknowns and assumptions"},
        "evidence_requirements": ["source identifiers", "claim-to-source mapping"],
        "tool_hints": ["read_file", "search_web"],
        "risk_policy": "Do not fabricate citations; mark unknowns explicitly.",
        "priority": 20,
        "prompt": """
For research WorkItems:
- stay read-only unless the target artifact is an explicit research note
- separate verified facts, assumptions, and open questions
- include source identifiers or URLs when available
- prefer concise synthesis over long pasted excerpts
- never fabricate citations or pretend unavailable sources were checked

Evidence should list sources consulted and the decision each source supports.
""".strip(),
    },
    {
        "name": "math.reasoning",
        "description": "Solve calculations, proofs, and quantitative checks traceably.",
        "allowed_work_kinds": ["doc", "data"],
        "trigger_keywords": ["math", "proof", "derive", "calculate", "optimize"],
        "output_schema": {"assumptions": "definitions/variables", "result": "exact or approximate answer"},
        "evidence_requirements": ["derivation or reproducible computation", "edge-case check"],
        "risk_policy": "Distinguish exact from approximate results.",
        "priority": 30,
        "prompt": """
For mathematical or quantitative WorkItems:
- state definitions, assumptions, and variables before deriving
- show the shortest derivation that makes the result auditable
- check units, bounds, edge cases, and numerical stability
- when using computation, record the expression or command as evidence
- distinguish exact results from approximations

Evidence should include the final result plus enough reasoning to reproduce it.
""".strip(),
    },
    {
        "name": "paper.writing",
        "description": "Draft academic or technical prose with structure and citation discipline.",
        "allowed_work_kinds": ["doc", "research"],
        "trigger_keywords": ["paper", "论文", "abstract", "related work", "method"],
        "output_schema": {"section": "structured prose", "citations": "available source IDs only"},
        "evidence_requirements": ["source material or contract facts used"],
        "risk_policy": "Do not overstate novelty or empirical support.",
        "priority": 30,
        "prompt": """
For paper-writing WorkItems:
- identify audience, claim, contribution, and required section type
- keep prose precise, scoped, and evidence-backed
- use citations only when sources are available in context or research evidence
- separate related work, method, result, limitation, and future-work claims
- do not overstate novelty or empirical support

Evidence should identify source material or contract facts used in the draft.
""".strip(),
    },
    {
        "name": "data.pipeline",
        "description": "Handle data artifacts with provenance and reproducibility.",
        "allowed_work_kinds": ["data"],
        "trigger_keywords": ["data", "dataset", "csv", "schema", "transform"],
        "output_schema": {"inputs": "paths/provenance", "outputs": "paths/validation summary"},
        "evidence_requirements": ["row/schema/null checks", "input and output paths"],
        "risk_policy": "Avoid destructive mutation unless explicitly requested.",
        "priority": 20,
        "prompt": """
For data WorkItems:
- preserve input provenance and transformation steps
- avoid destructive mutation unless explicitly requested
- validate schemas, row counts, nulls, and representative edge cases
- keep derived artifacts named and reproducible

Evidence should include input paths, output paths, and validation summaries.
""".strip(),
    },
    {
        "name": "ops.safety",
        "description": "Treat operational work as serial, scoped, and approval-sensitive.",
        "allowed_work_kinds": ["ops"],
        "trigger_keywords": ["deploy", "shell", "infra", "ops", "permission"],
        "output_schema": {"commands": "intended commands/results", "rollback": "rollback or next-step notes"},
        "evidence_requirements": ["command intent", "command result", "rollback note"],
        "risk_policy": "Mutable or destructive commands require explicit approval.",
        "priority": 10,
        "prompt": """
For ops WorkItems:
- assume mutable infrastructure and destructive commands require explicit approval
- prefer dry runs, status checks, and reversible steps
- record exact commands and outputs as evidence
- do not cross the declared scope, credentials, or environment

Evidence should include command intent, result, and rollback or next-step notes.
""".strip(),
    },
    {
        "name": "eval.bench",
        "description": "Evaluate task completion with concise reproducible metrics.",
        "allowed_work_kinds": ["eval"],
        "trigger_keywords": ["eval", "benchmark", "completion rate", "failure analysis"],
        "output_schema": {
            "score": "pass/fail plus numeric metrics",
            "failure_category": "planner|scheduler|tool|verifier|implementation|unknown",
        },
        "evidence_requirements": [
            "task id",
            "final run status",
            "test result or reason unavailable",
            "security/replan observations",
        ],
        "tool_hints": ["read_file", "run_code"],
        "risk_policy": "Evaluation may execute tests but must not mutate task artifacts.",
        "priority": 15,
        "prompt": """
For eval WorkItems:
- evaluate the finished run or artifact against explicit criteria
- keep the report short: status, evidence, failure category, and one next action
- do not fix implementation during evaluation
- record whether failures are due to planning, scheduling, tool policy, verifier, or generated code

Evidence should be concise and reproducible.
""".strip(),
    },
]
