---
name: repair-localization
description: Use when bug fixes, failing tests, tracebacks, regressions, lint failures, or smoke failures appear.
skill_id: planner_repair_localization
title: Localize before repairing
applicable_roles: planner,implementer
tags: repair,localization,agentless
applicability: repair
---

# Repair Localization

## Runtime prompt
- First localize the smallest suspect surface: failing behavior, likely module, and one reproduction or targeted assertion.
- Cluster diagnostics into a root-cause hypothesis before editing.
- Patch the canonical owner or adapter closest to the fault; avoid spreading compatibility hacks across consumers.
- Avoid broad rewrites until localization evidence points to a cross-cutting cause.

## References
Read `references/research_notes.md` when revising this skill or comparing localization approaches across agentic repair papers.
