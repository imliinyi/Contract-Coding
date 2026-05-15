---
name: capsule-pull-boundaries
description: Use when a task consumes another team's capsule dependency and must respect pull-based context boundaries.
skill_id: inspector_pull_discipline
title: Respect capsule pull boundaries
applicable_roles: planner,implementer,reviewer
tags: pull-based,capsule
applicability: capsule_deps
---

# Capsule Pull Boundaries

## Runtime prompt
- Treat L2/L3 capsule detail as valid only when Inspector attached it from `task.capsule_dependencies`.
- For every other team, rely on L1 purpose tags only.
- Never invent a capsule interface, field, enum, or example.
- Missing dependency detail belongs in `open_questions` or a blocker, not in guessed implementation.

## Authoring notes
This is a runtime boundary skill, not a product skill. Keep it short and strict.

