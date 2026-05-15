# Superpowers Patterns Adopted

Source: `https://github.com/obra/superpowers`

Useful patterns for this repository:

- Skill descriptions should say when to load the skill, not summarize the workflow.
- Frequently loaded skill content should stay short.
- Heavy references and reusable tools should live beside the skill, not inside the prompt fragment.
- Skill changes should be validated against failure modes, not judged by readability alone.
- Discipline skills need red flags and explicit anti-rationalization language.

ContractCoding-specific adaptation:

- Runtime skills add flat frontmatter fields consumed by `ContractCoding.memory.skills`.
- `runtime: false` allows meta skills to live in the same folder tree without entering worker packets.
- `## Runtime prompt` is the only section converted into `SkillCard.prompt_fragment`.

