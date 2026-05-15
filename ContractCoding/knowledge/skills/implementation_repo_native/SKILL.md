---
name: implementation-repo-native
description: Use when ordinary implementation tasks require repository-native changes that match local conventions.
skill_id: implementer_repo_native_change
title: Make repo-native diffs
applicable_roles: implementer
tags: implementation,diff,style
applicability: always
---

# Implementation Repo Native

## Runtime prompt
- Implement the smallest coherent diff that satisfies the task.
- Match existing style, naming, imports, error handling, file layout, and test layout.
- Do not introduce new frameworks, global rewrites, or clever abstractions unless local code already points that way or the task explicitly requires it.
- Keep decisions tied to concrete files and observable behavior.

## Why this exists
Coding agents drift when they optimize for plausible code instead of local fit. This skill biases toward the repository's own grammar.
