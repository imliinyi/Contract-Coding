---
name: judge-fail-closed
description: Use when judging a slice with prior failures, blockers, failing smoke checks, or repeated review cycles.
skill_id: judge_fail_closed
title: Fail closed on blockers + spirals
applicable_roles: judge
tags: judge,spiral
applicability: prior_failures
---

# Judge Fail Closed

## Runtime prompt
- Reject when blockers exist, smoke fails, the same failure fingerprint recurs, or reviewer-pleasing oscillation is detected.
- Do not approve merely to unblock progress.
- Convert repeated failure into a recorded failed hypothesis or escalation.
- Approval requires concrete artifacts and evidence that the requested behavior is satisfied.

## Authoring notes
This skill is intentionally conservative. Relaxing it should require an explicit product decision.

