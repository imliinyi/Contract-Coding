---
name: implementation-failure-memory
description: Use when a task has prior failures or repeated attempts and the agent must avoid retrying the same failed hypothesis.
skill_id: implementer_failure_memory
title: Do not repeat failed hypotheses
applicable_roles: planner,implementer
tags: repair,memory,anti-loop
applicability: prior_failures_or_attempts
---

# Implementation Failure Memory

## Runtime prompt
- Use `prior_failures` as hard negative evidence.
- If a failure fingerprint recurs, change the hypothesis, narrow the scope, or escalate with missing evidence.
- Do not reapply the same patch shape with different wording.
- Preserve the useful part of the failed attempt as a constraint for the next plan.

## Why this exists
Long-running coding agents need failure memory to avoid reviewer-pleasing loops and repetitive repair attempts.

