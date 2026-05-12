"""Progressive-disclosure skills for Runtime V5.

Skills are intentionally compact. They should steer the agent's own planning,
capsule reasoning, implementation, testing, and repair without moving product
judgment into the runtime control plane.
"""

BUILTIN_SKILLS = [
    {
        "name": "planning_product_kernel",
        "summary": "Freeze product semantics before implementation work starts.",
        "checklist": [
            "Extract canonical schemas, fixtures, public flows, invariants, and acceptance rows from explicit user requirements.",
            "Turn semantic expectations into machine-checkable invariants, fixture consistency rules, and slice smoke contracts.",
            "Prefer user-visible behavior, public paths, and artifact ownership over directory-only decomposition.",
            "Declare slice dependencies and conflict keys before any worker edits files.",
            "If semantics are ambiguous, preserve the ambiguity as diagnostics instead of inventing hidden acceptance rules.",
        ],
        "forbidden": [
            "Do not create final tests whose expected behavior is absent from the frozen kernel.",
            "Do not split a large task only by folder when a runnable feature slice boundary is available.",
        ],
    },
    {
        "name": "feature_slice_design",
        "summary": "Design one bounded feature slice as a small product increment before editing files.",
        "checklist": [
            "Name the slice's public contribution in one sentence.",
            "List producer APIs this slice will expose and consumer APIs it needs from dependencies.",
            "Define the exact smoke check that proves this slice can be imported or executed before final integration.",
            "Choose a minimal data shape that downstream slices can import without side effects.",
            "Keep each owner artifact focused; shared behavior belongs in explicit producer modules, not copy-pasted consumers.",
        ],
        "forbidden": [
            "Do not start by writing every file independently with incompatible local types.",
            "Do not hide cross-slice coupling in private helpers that consumers cannot inspect.",
        ],
    },
    {
        "name": "managed_feature_team_coordination",
        "summary": "Coordinate a feature team as a small managed-agent group with a lead, worker pool, interface steward, and reviewer.",
        "checklist": [
            "Treat feature_team as the team's local contract and feature_slice as the current bounded assignment.",
            "Team lead keeps the local done contract and dependency order visible before edits begin.",
            "Worker pool may implement independent owner artifacts in parallel only when conflict keys and dependency capsules do not overlap.",
            "Interface steward locks exported examples and public modules before downstream consumers rely on them.",
            "Reviewer verifies compile/import/smoke evidence and rejects narrative-only submit_result.",
        ],
        "forbidden": [
            "Do not let parallel workers invent incompatible local schemas.",
            "Do not bypass the interface capsule when communicating between teams.",
        ],
    },
    {
        "name": "interface_contract_authoring",
        "summary": "Author stable producer interfaces for downstream slices.",
        "checklist": [
            "Expose constructors, dataclass fields, enum values, function signatures, and serialization shapes through importable symbols.",
            "Prefer explicit classmethods or helper constructors when raw dataclass kwargs are not the intended public API.",
            "Add lightweight module docstrings or evidence naming the public producer surface.",
            "When changing an existing producer, preserve backwards-compatible call paths or update all allowed consumers in the same repair.",
        ],
        "forbidden": [
            "Do not require consumers to guess private dataclass internals.",
            "Do not export symbols that perform I/O, prompts, network calls, or long-running work at import time.",
        ],
    },
    {
        "name": "interface_capsule_handshake",
        "summary": "Lock the smallest producer capsule that lets other teams proceed asynchronously.",
        "checklist": [
            "Publish capabilities, public modules, examples, fixture shapes, and smoke checks without locking private implementation internals.",
            "Treat the capsule as a versioned contract: consumers may depend on examples and shapes after the capsule lock item passes.",
            "Keep capsule scope narrow enough that implementation teams can still choose local classes and helpers.",
            "If a consumer needs more shape information, report a capsule request instead of guessing hidden requirements.",
        ],
        "forbidden": [
            "Do not freeze full internal APIs before implementation has enough evidence.",
            "Do not let consumers depend on an unlocked capsule.",
        ],
    },
    {
        "name": "dependency_interface_consumption",
        "summary": "Use dependency slices by inspecting their public capsules before coding against them.",
        "checklist": [
            "Before writing consumer code, read contract_snapshot and inspect dependency owner artifacts named in dependency_interface_capsules.",
            "Use dependency constructors and helpers exactly as implemented; do not invent kwargs or attributes.",
            "Normalize external JSON/dict payloads at the boundary before handing them to domain/core producer APIs.",
            "If the dependency interface is missing or contradictory, report a blocker or repair root cause instead of guessing.",
        ],
        "forbidden": [
            "Do not assume dataclass fields that you have not inspected.",
            "Do not patch dependency artifacts unless they are inside allowed_artifacts for the current work item.",
        ],
    },
    {
        "name": "code_generation_slice",
        "summary": "Implement one feature slice against Product Kernel owner artifacts only.",
        "checklist": [
            "Read the Product Kernel, canonical substrate, fixture refs, done contract, allowed_artifacts, locked_artifacts, and dependency_interface_capsules first.",
            "Implement the interface_contract first, then fill behavior behind that stable surface.",
            "Implement only allowed_artifacts; use existing public producer APIs from dependency slices.",
            "If a temporary dependency mock is unavoidable, mark it with CONTRACTCODING_MOCK metadata naming mock_id, real_owner_slice, allowed_until_phase, and contract_tests.",
            "Keep imports side-effect free and avoid prompts, daemon loops, network calls, hidden global state, and generated filler.",
            "Finish with concrete changed_files and evidence that each owner artifact compiles and slice smoke can pass.",
        ],
        "forbidden": [
            "Do not edit downstream consumer artifacts to make local code look complete.",
            "Do not submit narrative-only completion; changed_files and evidence must be concrete.",
        ],
    },
    {
        "name": "code_test_slice",
        "summary": "Create or run tests that compile frozen Product Kernel acceptance for one slice.",
        "checklist": [
            "Use kernel fixtures, public paths, dependency interfaces, and slice done contract as the only semantic source.",
            "Cover import safety, artifact coverage, producer-consumer shape, and declared public behavior.",
            "For numeric behavior, derive expectations from declared formulas or public helpers; exact literals require kernel fixture evidence.",
            "Keep slice tests close to producer-consumer contracts; leave cross-slice scenarios to final integration.",
            "Prefer small smoke tests at slice boundaries; broad scenario tests belong to final integration.",
            "Report exact commands, pass/fail status, and the smallest useful failure excerpt.",
        ],
        "forbidden": [
            "Do not invent new product rules in tests.",
            "Do not weaken locked tests during repair.",
        ],
    },
    {
        "name": "acceptance_test_authoring",
        "summary": "Write final acceptance tests as executable projections of the frozen kernel.",
        "checklist": [
            "Read producer interfaces before asserting behavior across slices.",
            "Assert user-visible flows, persistence round trips, CLI smoke, and deterministic scenarios from the kernel.",
            "Never introduce magic expected numbers, strings, or rankings unless they are present in kernel fixtures or derived in-test from public APIs.",
            "Keep tests black-box where practical: import public modules and run public commands.",
            "When a test reveals missing behavior, leave the test locked and let repair patch production code.",
        ],
        "forbidden": [
            "Do not encode accidental implementation details as final product semantics.",
            "Do not make tests pass by relaxing the user's stated requirement.",
        ],
    },
    {
        "name": "tool_use_protocol",
        "summary": "Use tools in short, inspectable loops with clear stop conditions.",
        "checklist": [
            "Inspect before editing: file_tree, read_file/read_lines, search_text, or inspect_symbol for dependency APIs.",
            "Write bounded patches to allowed_artifacts and avoid large unreviewable rewrites when a small patch works.",
            "Use run_code or compile commands for focused validation when available; if blocked, record that clearly as evidence.",
            "Call submit_result once all owner artifacts and validation evidence are ready.",
        ],
        "forbidden": [
            "Do not keep calling tools after all allowed artifacts and evidence are complete.",
            "Do not dump long command output into chat; summarize and keep long output in files when needed.",
        ],
    },
    {
        "name": "evidence_submission_protocol",
        "summary": "Submit completion evidence that the runtime and later repair can audit.",
        "checklist": [
            "changed_files must be the exact owner files modified by this work item.",
            "Evidence should mention interface decisions, validation commands, compile/import status, and any blocked validation.",
            "For consumer slices, name the producer APIs used from dependency interfaces.",
            "For repair, name the failure fingerprint, root cause, patched files, and locked tests left unchanged.",
        ],
        "forbidden": [
            "Do not claim success without changed_files for implementation or repair work.",
            "Do not omit known residual risks or blocked validation steps.",
        ],
    },
    {
        "name": "judge_contract_verification",
        "summary": "Judge only frozen Product Kernel acceptance and slice contracts.",
        "checklist": [
            "Check existence, syntax, import safety, placeholder absence, and declared producer-consumer shape.",
            "Run the declared slice smoke and reject narrative-only success if executable evidence is missing.",
            "Attach diagnostics to slice_id, artifact, acceptance_id, and kernel_invariant.",
            "Separate local slice gate failures from final integration failures.",
            "Treat model claims as advisory; completion requires deterministic evidence.",
        ],
        "forbidden": [
            "Do not route final integration failures back to ordinary feature teams.",
            "Do not create extra acceptance criteria while judging.",
        ],
    },
    {
        "name": "repair_transaction",
        "summary": "Patch allowed artifacts for one failure fingerprint while locked tests remain read-only.",
        "checklist": [
            "Cluster diagnostics by fingerprint, artifact, and kernel invariant.",
            "State root cause, allowed artifacts, locked tests, expected behavior delta, and validation command.",
            "Run exact validation commands in the isolated repair workspace before submit_result when tools allow it.",
            "Patch the smallest legal surface and produce owned-file changes.",
            "If the same fingerprint repeats without progress, request replan or human-required instead of retrying blindly.",
        ],
        "forbidden": [
            "Do not edit locked tests unless diagnostics explicitly classify the tests as invalid.",
            "Do not claim repair success without a patch and exact validation evidence.",
        ],
    },
    {
        "name": "replan_failure_cluster",
        "summary": "Update only affected kernel or slice graph facts after repeated repair failure.",
        "checklist": [
            "Identify whether the failure is semantic ambiguity, missing owner, bad dependency, fixture conflict, or illegal patch scope.",
            "Reopen only affected slices and preserve verified slice evidence and promotion metadata.",
            "Update kernel or slice graph facts rather than cloning the old contract with metadata.",
            "Escalate to human-required when no legal owner or invariant update can be derived.",
        ],
        "forbidden": [
            "Do not reopen every slice by default.",
            "Do not erase prior promotion evidence while replanning.",
        ],
    },
]
