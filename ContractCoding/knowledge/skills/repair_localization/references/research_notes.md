# Repair Localization Research Notes

- SWE-agent emphasizes an agent-computer interface: concise observations, shell feedback, and repository-level navigation.
- Agentless-style repair separates localization, patch generation, and validation. That separation is useful when the model is tempted to rewrite too much.
- OpenHands-style systems make execution feedback first-class; failed commands should become evidence, not hidden context.
- Practical rule: a repair skill should ask for the smallest falsifiable hypothesis, then a patch, then validation.

