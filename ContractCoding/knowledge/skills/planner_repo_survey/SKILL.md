---
name: planner-repo-survey
description: Use when planning coding work that needs repository orientation before selecting files, tests, or implementation strategy.
skill_id: planner_repo_survey
title: Start from a repo map
applicable_roles: planner
tags: planning,context,aci
applicability: always
---

# Planner Repo Survey

## Runtime prompt
- Before choosing files, form a compact repo map from visible context: entrypoints, tests, config, existing conventions, and validation commands.
- Prefer local architecture evidence over generic framework patterns.
- Identify the smallest file set that can satisfy the goal, plus the commands that would prove it.
- If available context is not enough to name likely files, put that gap in `open_questions`.

## Why this exists
Good coding agents win by using the repository as the source of truth. This skill keeps planning anchored in local evidence.

