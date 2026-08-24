# Actions, Commands, Workflows, and Dots

The user-facing Skill surface starts from the job a person wants done. It does not mirror the list of installed providers, Tools, file formats, or prerequisite calls.

## Object-Aware Actions

`Object-Aware Actions` is the user-facing feature name for the rule that the object named after an Action selects its specialised workflow. Its stable technical identifier is `object-aware-workflow-routing`.

For example, `review this document`, `review this code`, and `review this animation` all use the same `review` Action. The named object changes the workflow and reusable dots behind it; it does not create another user-facing Skill. When the object is not explicit enough to select a safe workflow, the Action resolves the consequential unknown before acting.

## Actions

An Action is a complete user job. The Codex candidate exposes six:

- `create`: make a new outcome;
- `edit`: change or repair an existing outcome;
- `review`: judge or check an object without changing it;
- `research`: find and verify the evidence needed to answer a question;
- `automate`: make a repeated or triggered job run reliably;
- `publish`: deliver a finished outcome to an explicitly named destination.

The object stays in the request. For example, `review this document`, `create an animation`, and `edit this spreadsheet` use Object-Aware Actions to select specialised workflows inside the Action instead of adding Document Review, Animation Generator, Spreadsheet Repair, Figma Motion, or another provider name to the user surface.

## Commands

A Command controls Fractal itself. There are four:

- `assess`: run Two-Sided Review on one consequential idea;
- `complete`: finish all eight New Blueprint System Review Steps after Project Completion;
- `match`: run Perspective for an active formal Project;
- `version`: apply, verify, record, activate, and publish one exact permitted System Version batch.

A slash or dollar sign is invocation syntax. It does not make an Action a Command. Actions and the first three Commands may also match automatically; `version` requires Carson's exact explicit order.

## Workflows and reusable dots

Each Action or Command selects the narrowest workflow that matches the object and intended result. A dot is an internal maintained method that a workflow may use: document rendering, browser control, Figma prerequisites, web retrieval, image generation, acceptance checking, or another specialist method.

Dots do not own user jobs. The same browser dot can support Research retrieval, Review testing, Automation, and Publish verification. The same document dot can support Create, Edit, Review, Research, and Publish. Provider names remain available in the internal workflow map and source provenance without occupying the user-facing selector.

Before a route is used, the Action distinguishes four real situations: an exact maintained workflow; a partial method transfer that may need explanation; a missing workflow that starts Curiosity; or an unavailable dependency that must be reported rather than pretended to work.

Every active Skill must be classified as either one visible Action or Command, or one hidden reusable dot connected to at least one workflow. A new unclassified Skill fails the candidate build. This default-hidden gate prevents a new Plugin or provider method from silently becoming a new user-facing job.

## Missing workflow

When no maintained workflow fits, the selected Action uses Curiosity to study the current job, adjacent methods, and a genuinely different route. It may test one bounded provisional workflow for the current job. The missing route is recorded for System Review; it does not silently become part of a persistent System Version.

## Codex visibility and recovery

Generated internal Fractal methods are stored outside Codex Skill discovery roots. External and platform-owned Skill sources are retained but disabled in the selector through an exact `skills.config` plan. The prior config is recorded before change, source files are not deleted, and Codex must restart before the new selector surface is claimed as active.

Codex does not currently provide a native default-deny rule for future Skill paths. Fractal therefore treats Plugin installation, removal, and update as governed component changes. A new Plugin version or Skill path invalidates the existing surface proof: the registry and disable plan must be rebuilt, and a fresh exact-path App Server audit must show only the ten registered entries before activation can continue. Duplicate old Action names and newly enabled provider paths both fail this audit.

Building and testing this surface creates only a candidate. Activation and GitHub publication remain part of the explicit `version` Command.
