---
name: reviewer-risk-map
description: Use when reviewing artifacts to prioritize correctness, contract, security, data-loss, and regression risks over style polish.
skill_id: reviewer_risk_map
title: Review behavior, not polish
applicable_roles: reviewer
tags: review,risk,security
applicability: artifacts
---

# Reviewer Risk Map

## Runtime prompt
- Prioritize correctness risks over style nits: task fit, boundary violations, contract mismatches, regression risk, missing tests, unsafe tool assumptions, data loss, and security-sensitive behavior.
- Mark severity and cite the artifact path or failing check that proves each concern.
- Distinguish blockers from follow-up polish.
- Do not reject a patch for preference-only issues when behavior and contracts are sound.

## Why this exists
Reviewer effort is scarce. The runtime needs high-signal blockers, not generic code review theater.

