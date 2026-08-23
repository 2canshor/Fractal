---
name: edit
description: Change, repair, transform, or improve an existing object as one complete job. Use when the requested starting point already exists; not for read-only judgement, research, or a Fractal lifecycle Command.
metadata:
  version: "0.1.0"
  permission-summary: "Modify only the object and delivery scope named by the user; publication, sending, activation, purchase, or unrelated external effects need explicit authority."
---

# Edit

Start from what Carson wants changed and what must remain true. The object may be text, code, a document, spreadsheet, presentation, PDF, image, animation, design, website, workflow, configuration, or another existing artifact. Provider and Tool names are internal implementation details.

Read the generated `fractal/internal-workflow-map.json`. Select the narrowest `edit-*` workflow that matches the object and intended result, then load only the maintained internal methods needed for that route. One method may be reused here and in other Actions; never treat a provider method as owned by this Action.

Classify the route before using it: `exact` when a maintained workflow fits; `partial` when only part of a method transfers; `missing` when no maintained workflow exists; or `unavailable` when the right workflow exists but a required dependency is not callable. Use an exact route after checking its dependencies. For a partial route, explain and ask only when the transfer could materially change the result. For a missing route, follow the Curiosity path below. For an unavailable route, report the real constraint and use a safe fallback or stop; never claim the dependency worked.

Preserve the original, declared invariants, unrelated user changes, and a restore path in proportion to risk. Verify the edited result in its real form, including rendering or execution where relevant. Editing does not imply approval, publication, sending, activation, purchase, or an unrelated state change.

If no maintained workflow fits, use Curiosity to study the current job, adjacent methods, and one genuinely different approach. Adapt a bounded provisional workflow, test it on this job, and record the missing route for System Review. Do not silently turn the provisional route into a persistent System Version.
