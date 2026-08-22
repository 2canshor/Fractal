---
name: delegation-workflow
description: Hand off a bounded implementation or verification task to another capable agent after the user explicitly requests or accepts delegation. Do not use for tightly coupled edits or to avoid main-agent responsibility.
metadata:
  version: "0.1.0"
  permission-summary: "Dispatch only within the user's approved scope; delegated agents receive no additional authority."
---

# Delegation Workflow

Define the deliverable, owned files or responsibility, acceptance criteria, evidence, authority boundary, and stop condition. Delegate only work that can progress independently or benefits from fresh-context verification. Tell the agent that other work may exist and that it must preserve unrelated changes.

The implementing agent returns changed scope, tests, failures, and remaining risks. A verifier receives the deliverable and verbatim acceptance criteria, not the intended answer or a request to patch it. The Main Agent integrates the evidence and remains responsible for the final claim.

Do not hard-code a model tier, tool path, or platform-specific folder as architecture. Re-evaluate available capabilities at execution time. A handoff never expands external-action, deletion, completion, or approval authority.
