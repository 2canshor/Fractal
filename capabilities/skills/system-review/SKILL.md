---
name: system-review
description: Analyse a primary-user-completed Project for evidence-supported system improvement, including No Change, a reversible experiment, or a Change Proposal. It cannot start from agent judgement alone.
metadata:
  version: "0.1.0"
  permission-summary: "Read review evidence and propose; primary-user authority is required for activation, rejection, or restore."
---

# System Review

Use the deterministic System Review runtime for the completed Project snapshot and stage order. Begin from the Project Completion event Carson recorded. Keep the original five Steps visible throughout the review.

1. **Find Problems.** Project Assessment compares the completed Project with its baseline and records Positive Delta and Negative Delta. Issue Scan uses `Quantity over Quality`: inspect the full Project history and record raw observations before grouping, explaining, prioritising, or proposing a change.
2. **Find Local Patterns.** Project Patterns separates symptom, proximate cause, plausible common cause, isolated incident, recurring behaviour, counterexample, and uncertainty. A confirmed cause needs supporting evidence.
3. **Compare History.** Cross-Project Patterns reads previous Projects, System Reviews, proposals, hypotheses, System Versions, interventions, and outcomes. Reversal Check asks whether Fractal is solving the wrong problem. When Cause Research is warranted, keep External Research and Internal Review independent until Reconciliation.
4. **Choose the System Response.** Evaluate existing components and preserve this order: delete, shorten, merge, simplify, reconfigure, modify, add, no-change. Run Capability Check and compare complexity, value history, safeguard purpose, risk, migration, and restore cost.
5. **Reality Check.** Expected Effect states the hypothesis and horizon. Local Effect and Global Effect compare before and after evidence. Final Assessment distinguishes genuine improvement, harmful local optimisation, a helpful result from the wrong causal model, failed intervention, and an outcome that still needs later evidence.

Work through Project Assessment, Issue Scan, Project Patterns, Cross-Project Patterns, Reversal Check, Cause Research, Reconciliation, Improvement Options, Expected Effect, Local Effect, Global Effect, Two-Sided Review, Final Assessment, Biggest Remaining Concern, Result, and Your Decision. Link each conclusion to observed evidence. Result is Change Proposal, Experiment, Need More Evidence, or No Change. Your Decision is Carson's typed action.

Apply `Deterministic Over Probabilistic` to each bounded part: schemas, stage order, history coverage, hashes, authority, option coverage, and effect states use exact programs. The Main Agent handles interpretation, causal reasoning, trade-offs, and the final synthesis. When a consequential warrant exists, keep Case For and Case Against independent. Select the lightest evaluated agent that has the required capabilities; branches supply evidence, not the final decision.

Fatigue, Curiosity, and Greed contribute evidence through the same five-step backbone. Fatigue enters Issue Scan or Improvement Options, Curiosity enters Cause Research or Improvement Options, and Greed enters Expected Effect or Improvement Options. `No Change` is a valid result. Only the Main Agent makes the final suggestion and states the Biggest Remaining Concern.

When Carson gives feedback, evaluate reasons that support it and reasons that challenge it, then issue an updated Final Assessment and Biggest Remaining Concern before returning to Your Decision. A proposal remains inactive after approval until an approved System Version passes build, adapter, migration, and restore gates.
