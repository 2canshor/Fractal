# System Review and Versioning

A genuine System Review starts only after a typed Project Completion action by the primary user. A Project that is merely finished in an agent's judgement, passing tests, or `awaiting_completion` cannot trigger it.

## Required Stage Order

1. Project Assessment
2. Issue Scan
3. Project Patterns
4. Cross-Project Patterns
5. Reversal Check
6. Cause Research
7. Reconciliation
8. Improvement Options
9. Expected Effect
10. Local Effect
11. Global Effect
12. Two-Sided Review or an explicit not-warranted result
13. Final Assessment by the Main Agent
14. Biggest Remaining Concern
15. Result: Change Proposal, Experiment, Need More Evidence, or No Change
16. Your Decision

Project Assessment records both what went well and what could be better. Issue Scan first gathers a high-recall observation list, then later stages sort and assess it. Cross-Project Patterns records `insufficient` when the comparison history is too small. Cause Research runs independent External Research and Internal Review when a cause is not already supported, then Reconciliation combines them. Each stage records its result and evidence.

The Main Agent writes the Final Assessment and names the Biggest Remaining Concern. The result then waits for the primary user's typed decision. `No Change` is a complete outcome.

## Independence

External Research and Internal Review start from separate context manifests and cannot receive each other's output before reconciliation. When Two-Sided Review is warranted, Case For and Case Against follow the same rule. A deterministic provenance check verifies branch ids, initial context hashes, input artefacts, and output artefacts. Passed candidates are ranked so the lightest capable source or debate agent is selected. Branches provide evidence and arguments; only the Main Agent makes the final suggestion.

## Proposal and Experiment

Improvement options are ordered `delete -> shorten -> merge -> simplify -> modify -> add -> no-change`. A Change Proposal contains baseline, candidate, diff digest, expected/local/global effects, evidence, and restore plan. It remains inactive after approval until an approved System Version includes it.

An experiment runs baseline and candidate on the same representative work in a disposable directory. Missing any safe-trial condition stops execution and requests a primary-user decision. A positive result is still only a candidate for review.

## Feedback Review

When the primary user gives feedback on a System Review result, Feedback Review records the strongest reasons supporting the feedback, the strongest reasons challenging it, an updated Final Assessment, and an updated Biggest Remaining Concern. The result returns to Your Decision.

## System Version

A candidate manifest pins full Public and Private commit ids, component versions and hashes, adapter hashes, migrations, restore point, and five passing gates: clean build, tests, adapters, migrations, and restore. The candidate cannot activate itself.

Only the primary user can activate, reject, or restore. The active pointer is checked against the immutable manifest digest. Restore targets must have been active before and must carry verified restore evidence.

## Node Implementation Map Changes

Add, Remove, Replace, Merge, and Split all follow the same proposal, isolated trial, primary-user decision, future System Version, and restore path. A program, Skill, Hook, Subagent, MCP, Plugin, or Main Agent cannot add itself to the active mapping.
