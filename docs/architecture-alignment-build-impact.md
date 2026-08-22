# Architecture Alignment: Build Impact

This document records the runtime work created by the Architecture Lineage correction. The hierarchy and agentic-element mapping are now built in the public candidate. The current live candidate still represents the earlier runtime.

## 1. System Review records

Extend the deterministic review record so each modern stage points to one original backbone step and carries the evidence that step requires.

### Project Assessment

Add explicit `positive_delta` and `negative_delta` fields. Each item links to Project evidence and states the comparison baseline.

### Issue Scan

Add:

- collection mode: `quantity-over-quality`;
- whole-Project history manifest and coverage receipt;
- raw observations with uncertainty and source links;
- a boundary that keeps cause, pattern, priority, and proposed change out of the harvest stage.

### Project Patterns

Add typed distinctions for symptom, proximate cause, plausible common cause, isolated incident, recurring local behaviour, counterexample, confidence, and causal uncertainty.

### Cross-Project Patterns and Reversal Check

Add historical links to previous analyses, proposals, hypotheses, System Versions, interventions, corrections, reversals, and later outcomes. Add pattern types for recurrence, contradiction, overcorrection, and pattern-of-patterns. Reversal Check records whether the adjusted dimension itself remains credible.

### Cause Research

Keep causal diagnosis separate from proposal debate. The cause-research warrant blocks Improvement Options until External Research, Internal Review, and Reconciliation have completed or the review records why they are unnecessary.

### Improvement Options

Require evidence that the review considered existing components and each option family:

`delete, shorten, merge, simplify, reconfigure, modify, add, no-change`

Add complexity delta, demonstrated-value history, safeguard purpose, and counterfactual risk.

### Effect and outcome learning

Separate hypothesis status from improvement status. Store the four Local/Global result combinations and support a pending Global Effect that later Projects can resolve. Link later evidence back to the original problem, cause hypothesis, proposal, version, and context.

## 2. Project Review records

Add the original anti-local-optimisation purpose to its lineage metadata and human view. Extend Review Point and Project Review evidence with:

- planned versus actual time and resource allocation;
- neglected areas and opportunity cost;
- threshold reached, continuation decision, and justification;
- explicit whole-Project scope receipt;
- readable Plan before/after comparison.

Milestone and Exception remain the two trigger families. A time threshold creates a Review Point and leaves the continuation decision to Project Review.

## 3. Node and capability lineage

Extend the Node Implementation Map schema. Each major Node records:

- `original_requirement`;
- `original_failure_mode`;
- `later_refinements`;
- `implemented_by`;
- `lineage_class`: original backbone, supporting infrastructure, or separately approved later capability.

The Three Values—Fatigue, Curiosity, and Greed—and Mechanisms such as Hooks, Registry, and Capability Check record whether their output enters Project Review, System Review evidence, Cause Research, or Improvement Options.

## 4. Human Control

Present System Review in two simultaneous views:

- original five-step backbone, showing the question each step answers;
- modern Nodes, showing the operational stage and evidence status.

Show Positive Delta, Negative Delta, raw Issue Scan coverage, historical comparison, the wrong-dimension question, existing-system assessment, Expected/Local/Global Effect, later evaluation status, Final Assessment, Biggest Remaining Concern, and Your Decision.

Project Review shows the local trigger beside the whole-Project assessment and Plan comparison.

## 5. Generated adapters

Generate the updated System Review and Project Review guidance from Fractal. Adapter projections carry the same lineage identifiers and hashes as the registry. The generated entrypoint remains short and points to the canonical active metadata.

## 6. Acceptance tests

The new candidate needs tests proving at least:

1. Step 1 rejects a review that lacks Positive Delta, Negative Delta, whole-history coverage, or `Quantity over Quality` collection mode.
2. Step 1 preserves possible duplicates and uncertain observations until Step 2.
3. Step 2 rejects similarity-only grouping presented as confirmed cause.
4. Step 3 reads previous analysis/intervention/outcome chains and represents recurrence, contradiction, overcorrection, and pattern-of-patterns.
5. Repeated directional reversal creates a problem-dimension challenge.
6. Cause Research and Two-Sided Review remain independent workflows.
7. Step 4 demonstrates existing-component consideration and complete option-family coverage.
8. A low-use, high-value safeguard is retained when severity and counterfactual evidence justify it.
9. Step 5 represents all four Local/Global combinations and a pending later Global Effect.
10. A locally successful change can be classified as globally harmful.
11. A globally helpful outcome can be recorded with a rejected causal hypothesis.
12. A time threshold opens Project Review and does not automatically stop work.
13. A local Project Review trigger still produces a complete whole-Project assessment.
14. Fatigue, Curiosity, Greed, and Repetition evidence route into the existing review backbone.
15. Human Control shows backbone questions, modern Nodes, evidence status, and Carson's decision point.
16. The live adapter set matches the approved candidate hashes and exposes the aligned review guidance.

## 7. Candidate and authority boundary

The remaining runtime work belongs in a new reversible candidate with full test and live-verification evidence. That candidate returns to Carson for review. Project Completion and persistent System Version activation remain Carson decisions.
