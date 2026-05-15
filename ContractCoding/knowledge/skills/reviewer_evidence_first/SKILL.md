---
name: reviewer-evidence-first
description: Use when reviewing produced artifacts and every concern must include concrete evidence such as file paths, line spans, or failed checks.
skill_id: reviewer_evidence_first
title: Cite evidence for every concern
applicable_roles: reviewer
tags: review,evidence
applicability: artifacts
---

# Reviewer Evidence First

## Runtime prompt
- Every concern must cite at least one evidence string: artifact path with line span, failed validation command, or failed executable example.
- Concerns without evidence are invalid output and should be discarded by the Judge.
- Quote behavior precisely enough that an implementer can reproduce or inspect it.
- If evidence is missing, ask for the missing artifact or check instead of inventing a concern.

## Authoring notes
This skill should stay strict. Friendly review prose belongs outside the runtime card.

