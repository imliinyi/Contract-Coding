---
name: planner-parallel-boundaries
description: Use when multi-file, integration, migration, API, refactor, or wiring work may need parallel subtasks.
skill_id: planner_parallel_boundaries
title: Split only independent work
applicable_roles: planner
tags: planning,parallelism,boundaries
applicability: multi_file
---

# Planner Parallel Boundaries

## Runtime prompt
- Use parallel or multi-slice decomposition only when ownership is disjoint: files, public interfaces, and validation paths must not overlap.
- For dependent work, name the dependency order and the handoff artifact instead of pretending tasks are independent.
- Make conflict keys explicit when two slices could touch the same module, schema, route, CLI command, or test fixture.
- Keep shared contracts in the owning slice; consumers adapt at public boundaries only.

## Why this exists
This follows the pattern used by asynchronous coding products: parallelism helps only when the coordination cost is lower than the work split.
