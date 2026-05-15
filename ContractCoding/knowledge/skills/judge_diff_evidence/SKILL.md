---
name: judge-diff-evidence
description: Use when judging produced artifacts and approval must be based on concrete diff, review, and validation evidence.
skill_id: judge_diff_evidence
title: Approve only with diff evidence
applicable_roles: judge
tags: judge,evidence,validation
applicability: artifacts
---

# Judge Diff Evidence

## Runtime prompt
- Approve only when artifacts, reviewer output, and smoke or validation evidence support the task goal.
- A plausible plan, broad benchmark intuition, or generic confidence is not enough.
- Require concrete files, checks, and rationale.
- If evidence is partial, approve only the proven scope or reject with the missing proof named as a blocker.

## Why this exists
The judge is the last guardrail between plausible code and accepted code.

