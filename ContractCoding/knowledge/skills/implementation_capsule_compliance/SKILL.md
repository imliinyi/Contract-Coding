---
name: implementation-capsule-compliance
description: Use when implementing code that calls consumed interface capsules and must follow declared capsule contracts exactly.
skill_id: implementer_capsule_compliance
title: Honour consumed capsule contracts
applicable_roles: implementer
tags: capsule,contract
applicability: capsule_deps
---

# Implementation Capsule Compliance

## Runtime prompt
- When invoking a consumed capsule, follow its declared `interface_def` and executable examples exactly.
- Do not adapt by inventing fields, enum values, constructor arguments, or return shapes.
- Record any requested deviation as a decision with rationale.
- The steward evolves capsules; the implementer consumes the published contract.

## Authoring notes
Keep this skill focused on contract consumption. Interface authoring belongs in a separate skill.

