---
name: create
description: Create the outcome named in the user's request as one complete job, choosing and coordinating the required methods and tools internally.
metadata:
  version: "0.1.0"
  permission-summary: "Create the requested artifact or bounded state change; unrelated delivery, publication, spending, or external recipients require explicit authority."
---

# Create

Treat the requested object as part of the job: `create an animation`, `create a document`, `create an image`, or `create a Project`. Start from the requested outcome and completion standard, not from a provider catalogue.

Read the generated `fractal/internal-workflow-map.json`. Select the narrowest `create-*` workflow that matches the requested object, then load only the maintained internal methods needed for that route. One method may be reused by several workflows and Actions; never expose it as a separate user job merely because it has its own provider name.

Classify the route before using it: `exact` when a maintained workflow fits; `partial` when only part of a method transfers; `missing` when no maintained workflow exists; or `unavailable` when the right workflow exists but a required dependency is not callable. Use an exact route after checking its dependencies. For a partial route, explain and ask only when the transfer could materially change the result. For a missing route, follow the Curiosity path below. For an unavailable route, report the real constraint and use a safe fallback or stop; never claim the dependency worked.

Mention a provider only when Carson must choose because it materially changes the output, cost, account, rights, privacy, or delivery route. A missing provider is a capability constraint, not a reason to make the user redesign the workflow around tool names.

Keep one primary outcome and one independently testable completion contract. If the request contains separately useful artifacts, different authority boundaries, unrelated recipients, or independent external effects, separate them clearly rather than hiding them inside one oversized action.

Creation does not imply publication, sending, activation, purchase, or an unrequested external side effect. Verify the actual artifact or state transition before saying the job is complete.

If no maintained workflow fits, use Curiosity to study the current job, adjacent methods, and one genuinely different approach. Adapt and test one bounded provisional workflow for the current job, then record the missing route for System Review. Do not silently add it to a persistent System Version.
