---
name: capability-development
description: Create or revise a reusable Skill, program, Hook, adapter, or Tool with scoped instructions, realistic evals, provenance, permissions, and restore evidence. Use when capability behaviour itself is the deliverable.
metadata:
  version: "0.1.0"
  permission-summary: "Build and test candidates; no capability may activate or grant itself authority."
---

# Capability Development

Define the capability's exact job, non-trigger, inputs, outputs, authority, permissions, failure path, and observable completion evidence. Search for overlap before adding another selector-visible capability; prefer a narrower mode or merge when it reduces routing ambiguity without losing behaviour.

If the capability is proposed as a user-facing Action or Command, classify it first and invoke Naming System's Blueprint-required `Select User-Surface Symbol` sub-step before registration. A missing or unverified symbol keeps the user-surface candidate incomplete; do not promote the capability while treating its icon as optional presentation work.

Keep discovery metadata concise. Put shared decisions in the entrypoint and conditional detail in on-demand references. Add deterministic scripts only for repeatable mechanics. Do not copy a manual, private policy, or stale legacy corpus into a Skill.

Evaluate realistic positive, negative, boundary, permission, and failure cases. Track Availability, Activation and Authority, and Execution and Evidence separately. Packaging, linking, or installation can prove only the dimension it directly observes. A candidate becomes active only through the approved System Version path.
