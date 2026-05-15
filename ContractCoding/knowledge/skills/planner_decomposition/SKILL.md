---
name: planner-decomposition
description: Use when planning a coding task into bounded subtasks with explicit file ownership, boundaries, and open questions.
skill_id: planner_decomposition
title: Decompose into <=5 well-bounded subtasks
applicable_roles: planner
tags: planning,decomposition
applicability: always
---

# Planner Decomposition

## Runtime prompt
- Break the goal into 1-5 subtasks.
- For each subtask, name owned files or file families, explicit boundaries, and the output expected from that subtask.
- Do not reference another team's internals; depend on public capsules or declared handoff artifacts.
- If a subtask cannot be scoped from available context, put the gap in `open_questions` instead of inventing assumptions.

## Authoring notes
Keep this skill procedural. Do not add product-specific examples here; place domain examples in a separate skill or reference file.

