---
name: automate
description: Turn a repeated, scheduled, triggered, or multi-step job into a reliable workflow. Use when the user wants the job to run again or without manual repetition; not for a one-off task or merely using a browser or Tool once.
metadata:
  version: "0.1.0"
  permission-summary: "Design and test the repeatable workflow; scheduling, credentials, recipients, purchases, publication, or external writes remain separately scoped."
---

# Automate

Define the repeated job in ordinary terms: trigger, inputs, expected result, safe retry rule, stop condition, notification, and recovery. A one-off browser or computer action is not automatically an automation.

Read the generated `fractal/internal-workflow-map.json`. Select the narrowest `automate-*` workflow, then load only the maintained internal methods needed for this route. Browser control, scheduling, monitoring, extraction, notifications, and verification are reusable dots; the same dot may also support Research, Review, Create, Edit, or Publish.

Classify the route before using it: `exact` when a maintained workflow fits; `partial` when only part of an automation method transfers; `missing` when no maintained workflow exists; or `unavailable` when the right workflow exists but a required dependency is not callable. Use an exact route after checking its dependencies. For a partial route, explain and ask only when the transfer changes effects, reliability, or recovery. For a missing route, follow the Curiosity path below. For an unavailable route, report the real constraint and use a safe fallback or stop; never claim the dependency worked.

Build and test the smallest useful end-to-end path before widening frequency or scope. Treat lost acknowledgements as `indeterminate`; inspect the external state before retrying. Never infer authority for a schedule, credential, recipient, purchase, publication, or external mutation merely because the workflow can perform it.

If no maintained workflow fits, run Curiosity across the current job, adjacent methods, and a different implementation approach. Test one bounded provisional workflow and record the missing route for System Review. Do not silently add it to the persistent System Version.
