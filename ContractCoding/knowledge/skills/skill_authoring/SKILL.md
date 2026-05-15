---
name: skill-authoring
description: Use when creating, editing, reviewing, or validating ContractCoding skill folders and SKILL.md files.
runtime: false
---

# Skill Authoring

## Overview

Skill writing is process design. Treat it like test-driven development for agent behavior: identify the failure mode, write the smallest skill that prevents it, then verify the agent or loader behaves differently.

This skill adapts patterns from the Superpowers `writing-skills` skill for ContractCoding's runtime-loader model.

## When to Use

Use when:
- creating a new `ContractCoding/knowledge/skills/<skill>/SKILL.md`
- editing frontmatter, trigger text, runtime prompts, references, or scripts
- adding a new `applicability` predicate in `memory/skills.py`
- reviewing whether a skill is too vague, too broad, or likely to be skipped

Do not use for ordinary product planning or implementation. Those should use the runtime skills.

## Core Pattern

1. RED: name the bad behavior the skill must prevent.
2. GREEN: write the smallest skill body that blocks that failure.
3. REFACTOR: remove filler, split heavy details into `references/`, and add validation.

## Frontmatter Rules

- `name`: lowercase letters, numbers, and hyphens only.
- `description`: starts with `Use when`.
- `description`: describes triggering conditions only, not the workflow.
- `description`: stays under 500 characters.
- Runtime skills include `skill_id`, `title`, `applicable_roles`, `tags`, and `applicability`.
- Non-runtime authoring/reference skills set `runtime: false`.

Why the description rule matters: agents may shortcut from metadata. If the description summarizes the workflow, the body can be skipped.

## Body Rules

- Keep the loaded body short and procedural.
- Put the runtime fragment under `## Runtime prompt` for skills loaded into `SkillCard`.
- Add `## When to Use`, `## Red Flags`, or `## Common Mistakes` when the decision is easy to rationalize away.
- Move heavy research notes, long examples, and API details into `references/`.
- Put reusable deterministic helpers into `scripts/`.

## Validation

Run:

```bash
python3 ContractCoding/knowledge/skills/skill_authoring/scripts/validate_skills.py
python3 -m unittest discover -s tests
```

Fix every reported metadata or structure issue before considering the skill change complete.

## Red Flags

- "This is obvious, no validation needed."
- "The description can explain the process."
- "One giant skill is simpler."
- "This belongs in prompt text because writing a loader is too much."
- "The script is optional, humans can remember the rule."

These usually mean the skill will rot or be skipped.

