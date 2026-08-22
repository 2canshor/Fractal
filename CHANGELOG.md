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

### Changed

- Adapter capability metadata now describes the active-on-install projection instead of calling live-discoverable Skills inactive.
- Component status separates registration, loading, callability, and successful execution evidence.
- The candidate development version is `0.1.0-alpha.2`; persistent activation remains under primary-user authority.
- The product lifecycle now centres Project Review during active work and System Review after primary-user Project Completion; Fatigue, Curiosity, Greed, and other components feed those reviews.

## [0.1.0-alpha.1] - 2026-08-22

### Added

- Experimental Fractal system baseline.

[Unreleased]: https://github.com/2canshor/fractal/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/2canshor/fractal/releases/tag/v0.1.0-alpha.1
