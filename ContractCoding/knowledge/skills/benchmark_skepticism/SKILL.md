---
name: benchmark-skepticism
description: Use when repair, test, review, or judge tasks involve benchmark contamination, hidden-test guessing, or memorized public patches.
skill_id: judge_benchmark_skepticism
title: Be skeptical of benchmark-shaped success
applicable_roles: reviewer,judge
tags: evaluation,contamination,tests
applicability: repair_or_artifacts
---

# Benchmark Skepticism

## Runtime prompt
- Treat benchmark-style tasks and hidden-test guessing with skepticism.
- Prefer observable requirements, fresh local tests, and regression checks.
- Do not approve a patch that appears tailored to memorized public benchmark details instead of the repository's stated behavior.
- When validation is weak, ask for stronger local evidence rather than inferring success from benchmark familiarity.

## References
Read `references/evaluation_notes.md` when updating evaluation policy or adding new benchmark-risk examples.
