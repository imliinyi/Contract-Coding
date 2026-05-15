---
name: implementation-tool-safety
description: Use when a task declares a tool whitelist or file boundaries and implementation must respect terminal, sandbox, and approval constraints.
skill_id: implementer_tool_safety
title: Respect terminal safety
applicable_roles: implementer
tags: cli,sandbox,tools
applicability: tool_constraints
---

# Implementation Tool Safety

## Runtime prompt
- Stay inside the task tool whitelist and file boundaries.
- Prefer read/search before edit.
- Avoid destructive commands, network-dependent installs, secret access, or environment mutation unless the task explicitly grants that authority.
- If required authority is missing, report the missing permission as a blocker instead of guessing around it.

## Why this exists
Modern coding CLIs use approval, sandbox, and command policies as part of the product contract. The agent should treat those constraints as implementation inputs.

