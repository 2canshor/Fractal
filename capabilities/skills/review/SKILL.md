---
name: review
description: Review the object named in the user's request using the matching target-specific method, then return the review outcome and next decision or action.
metadata:
  version: "0.1.0"
  permission-summary: "Read the requested object and produce review findings; mutation or external action needs its own task authority."
---

# Review

Treat the user's object as part of the request: `review this document`, `review this code`, `review the design`, or `review this website`. The user chooses the job and object; provider names and prerequisite tools stay internal.

Read the generated `fractal/internal-workflow-map.json`. Select the narrowest `review-*` workflow for the object, then load only the maintained internal methods needed for that route. Code, document, interface, legal, financial, website, acceptance, and other specialist reviews retain their own evidence and acceptance rules inside this Action. The same inspection, browser, parsing, or evidence dot may also support other Actions.

Classify the route before using it: `exact` when a maintained workflow fits; `partial` when only part of a review method transfers; `missing` when no maintained workflow exists; or `unavailable` when the right workflow exists but a required dependency is not callable. Use an exact route after checking its dependencies. For a partial route, follow the transfer rule below. For a missing route, follow the Curiosity path below. For an unavailable route, report the real constraint and use a safe fallback or stop; never claim the dependency worked.

Do not absorb Fractal lifecycle control into this Action. An active formal Project is handled by the `/match` Command and a primary-user-completed Project by `/complete`, whether Fractal activates them automatically or Carson invokes them directly.

Do not pretend that one review method fits every object. If no target-specific method exists but a materially different method may transfer—for example, only code-review instructions exist for a document—explain the proposed transfer in ordinary language and ask Carson before treating it as the method. If he agrees, obtain and validate a maintained method before claiming the review is complete.

Return findings against the object's real goal, evidence, risks, and completion standard. Keep read-only review separate from an instruction to repair, publish, send, approve, or activate anything.

If no maintained review workflow fits, use Curiosity to compare the object, adjacent review methods, and a different review approach. Explain any consequential method transfer in ordinary language, validate a bounded provisional workflow, and record the missing route for System Review. Do not persist it silently.
