---
name: implementation-validation-loop
description: Use when code changes need validation evidence before any completion or correctness claim.
skill_id: implementer_validation_loop
title: Close the edit-test loop
applicable_roles: implementer
tags: validation,tests,feedback
applicability: always
---

# Implementation Validation Loop

## Runtime prompt
- After changing artifacts, name the narrowest meaningful validation: unit, smoke, lint, import, CLI, or contract check.
- Prefer a targeted command first, then broader tests when the change touches shared behavior.
- If validation cannot run in this packet, include the reason and the exact command a runner should execute.
- Do not claim success from unexecuted checks.

## Scripts
Use `scripts/suggest_validation.py` as a lightweight helper when you only have changed paths and need a first-pass command suggestion.
