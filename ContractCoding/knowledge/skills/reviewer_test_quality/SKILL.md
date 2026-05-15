---
name: reviewer-test-quality
description: Use when artifacts include tests or validation evidence and the reviewer or judge must detect overfit, shallow, or disconnected tests.
skill_id: reviewer_test_quality
title: Reject overfit tests
applicable_roles: reviewer,judge
tags: review,tests,benchmark-skepticism
applicability: artifacts
---

# Reviewer Test Quality

## Runtime prompt
- Assess whether tests prove the requested behavior rather than implementation details.
- Flag tests that are too narrow, too broad, happy-path-only, or disconnected from the task's observable contract.
- Prefer tests that exercise public modules, CLI/API behavior, persistence, or integration boundaries when those are the promised surface.
- Treat compile/import checks as useful smoke evidence, not final product proof.

## References
Read `../benchmark_skepticism/references/evaluation_notes.md` when updating test-quality policy for benchmark-like tasks.

