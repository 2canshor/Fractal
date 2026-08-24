# Changelog

All notable changes to Fractal will be recorded here.

The format follows Keep a Changelog, and system versions follow Semantic Versioning.

## [Unreleased]

### Added

- Initial public package structure.
- Public/private repository boundary.
- Technical decision record for the first development release.
- Universal component registry for Skills, Hooks, roles, MCPs, Plugins, Tools, and adapters.
- Generated active-set metadata and a `fractal components show` Human Control route.
- Managed Codex and Claude installation, quarantine, restore, dependency checks, and component drift checks.
- Codex and Claude work-completed Hook projections for Fatigue Work Signature capture.
- Codex live reconciliation for Skills, Hooks, MCPs, Plugins, Apps, and loaded instruction sources.
- Version-checked Codex configuration writes, live drift events, and review-only external detection.
- Final Work Signature enrichment from Codex turn, Token usage, and completed-item events.
- A canonical Project Review capability with milestone and exception triggers, nine-dimension whole-Project assessment, and readable Plan snapshots.
- Required Biggest Remaining Concern, four System Review result types, Your Decision, and two-sided Feedback Review records.
- User-perspective `review`, `create`, `complete`, `assess`, `match`, and `version` Skills with provider and prerequisite details kept internal.
- Exact response-unit coverage, automatic Two-Sided warrants, three-part plain handoff preflight, evidence Claim Gate, and architecture-lineage receipts.
- Single-use primary-user authority receipts for local System Version build, activation, rejection, and restore actions.
- Honest Project Plan resource states: provided, unknown at plan time, or not applicable.
- A governed user surface with `create`, `edit`, `review`, `research`, `automate`, and `publish` Actions plus the four Fractal Commands.
- `Object-Aware Actions` (`object-aware-workflow-routing`), where the object named after an Action selects its specialised workflow without creating another user-facing Skill.
- Many-to-many workflow routing so one hidden provider or Tool method can support several Actions without becoming another slash item.
- Recoverable Codex Skill visibility planning and exhaustive default-hidden validation for every non-surface Skill.

### Changed

- Codex user-surface planning now loads installed Plugins before Skill discovery and waits for the Plugin/Skill catalogue to reach a fixed point. Lazy Google Drive, template, Figma, history, or future Plugin Skills can no longer be omitted from the deny-list audit and then appear in the user menu after a false pass.
- Adapter capability metadata now describes the active-on-install projection instead of calling live-discoverable Skills inactive.
- Component status separates registration, loading, callability, and successful execution evidence.
- Persistent activation remains under primary-user authority; candidate builds cannot activate themselves. An explicit `/version` order now completes only after the exact candidate is activated and verified in a fresh session.
- The product lifecycle now centres Project Review during active work and System Review after primary-user Project Completion; Fatigue, Curiosity, Greed, and other components feed those reviews.
- Component records now separate user-surface audience from automatic and explicit invocation. Adapter build evidence no longer implies installation, active runtime, callability, or user execution.
- The development candidate is `0.1.0-alpha.6`; the active System Version remains unchanged until Carson explicitly invokes `/version`.

## [0.1.0-alpha.1] - 2026-08-22

### Added

- Experimental Fractal system baseline.

[Unreleased]: https://github.com/2canshor/fractal/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/2canshor/fractal/releases/tag/v0.1.0-alpha.1
