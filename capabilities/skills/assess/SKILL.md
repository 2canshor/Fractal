---
name: assess
description: Assess whether Carson should continue, change, or stop an idea by completing independent Case For and Case Against before the Main Agent recommendation.
metadata:
  version: "0.1.0"
  permission-summary: "Read evidence and recommend; no approval, implementation, activation, publication, or external mutation."
---

# Assess

Use when Carson asks Fractal to challenge an idea or explicitly requests Two-Sided Review. Also run automatically inside System Review when the consequential warrant is true.

Freeze the same bounded evidence pack for two independent branches. Case For and Case Against must not see each other's output before both finish. Each side states its strongest argument, evidence, assumptions, cost, failure behavior, and Biggest Remaining Concern. The Main Agent then reconciles both sides; branches do not make the final recommendation.

Return one of `continue`, `change`, or `stop`, with confidence, the strongest reason, the strongest objection, and the remaining uncertainty. End at Carson's decision when the next step would change an outcome, authority, persistent state, external system, recipient, or cost.

Selecting this Command never turns the recommendation into approval and never performs the proposed idea.
