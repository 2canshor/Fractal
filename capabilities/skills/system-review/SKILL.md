---
name: system-review
description: Analyse a primary-user-completed Project for evidence-supported system improvement, including No Change, a reversible experiment, or a Change Proposal. It cannot start from agent judgement alone.
metadata:
  version: "0.1.1"
  permission-summary: "Read review evidence and propose; primary-user authority is required for activation, rejection, or restore."
---

# System Review

Use the deterministic System Review runtime for the completed Project snapshot and New Blueprint stage order. Begin from the Project Completion event Carson recorded. Keep all eight Steps visible throughout the review.

1. **Find Problems.** Project Assessment compares the completed Project with its baseline and records Positive Delta and Negative Delta. Issue Scan uses `Quantity over Quality`: inspect the full Project history and record raw observations before grouping, explaining, prioritising, or proposing a change.
2. **Find Local Patterns.** Project Patterns separates symptom, proximate cause, plausible common cause, isolated incident, recurring behaviour, counterexample, and uncertainty. A confirmed cause needs supporting evidence.
3. **Find Global Patterns.** Compare Local Patterns with previous Projects, System Reviews, proposals, hypotheses, System Versions, interventions, reversals, and outcomes.
4. **Find the Reasons for Every Global Pattern.** Make causal uncertainty explicit. Cause Research may use independent External Research and Internal Review before Reconciliation, but it does not own this Step.
5. **Find Solutions for Every Global Pattern.** Evaluate existing components first and preserve Subtraction First: delete, shorten, merge, simplify, reconfigure, modify, add, no-change. Curiosity supplies 60% current method, 20% dated same-field findings, and 20% related-field transfer. `No Finding` and `No Change` are valid.
6. **Map New Implementations into the Blueprint.** Every Candidate must receive one truthful Genre, role, Principle assessment, authority boundary, context effect, evidence and recovery path before debate. A No Change result records that there are no Candidates.
7. **Debate the Solutions for Every Global Pattern.** Two-Sided Review supplies independent Case For and Case Against for every consequential mapped Candidate before Main Agent synthesis.
8. **Present Decisions One-by-One.** Present one fully prepared decision at a time, preserve the remaining set, state the Biggest Remaining Concern, and record Carson's decision.

Work through Project Assessment, Issue Scan, Project Patterns, Cross-Project Patterns, Reversal Check, Cause Research, Reconciliation, Improvement Options, Expected Effect, Local Effect, Global Effect, Blueprint Mapping, Two-Sided Review, Final Assessment, Biggest Remaining Concern, Result, and Your Decision. Link each conclusion to observed evidence. Result is Change Proposal, Experiment, Need More Evidence, or No Change. Your Decision is Carson's typed action.

Before presentation, map every discovered Pattern exactly once into a response unit with an explicit disposition and decision id. No observation may disappear merely because an intermediate result format could not express it. If a required review mechanism is missing, finish it automatically when current authority allows; otherwise return `review_incomplete` and do not claim the recommendation is ready.

Apply `Deterministic Over Probabilistic` to each bounded part: schemas, stage order, history coverage, hashes, authority, option coverage, and effect states use exact programs. The Main Agent handles interpretation, causal reasoning, trade-offs, and the final synthesis. Evaluate the complete Two-Sided warrant automatically. When any warrant is true, keep Case For and Case Against independent and finish both before showing the Pattern. Select the lightest evaluated agent that has the required capabilities; branches supply evidence, not the final decision.

Fatigue, Curiosity, and Greed contribute evidence across the eight Steps without owning them. Reality Check, Experiment, Component Governance, Human Control and other Extras support the workflow without becoming Steps. `No Change` is valid, including a verified zero-observation and zero-pattern path. Only the Main Agent makes the final synthesis and states the Biggest Remaining Concern.

Present only the ordinary-language handoff: what problem occurred, how Fractal proposes to solve it, and what Carson must decide. Internal essays stay evidence. For the first three handoffs after this change, run the newcomer preflight in shadow-advisory mode: record advice but do not block, rewrite automatically, or add another approval turn.

When Carson gives feedback, evaluate reasons that support it and reasons that challenge it, then issue an updated Final Assessment and Biggest Remaining Concern before returning to Your Decision. A proposal remains inactive after approval until an approved System Version passes build, adapter, migration, architecture-lineage, Claim Gate, and restore gates.
