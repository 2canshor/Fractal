# Platform Adapters

Fractal generates Codex, Claude, Cowork, and Gemini projections in one direction from a pinned Public commit and selected Private state. An installed projection never overwrites canonical source in reverse.

Each adapter contains a minimal root router, a bounded Project and authority context, the universal component registry, the approved platform active set, selector-facing capability metadata, a Human Control status view, applicable generated Skill folders or packages, limitations, and a hash-pinned manifest. Codex and Claude also receive their locally observed Hook and agent-role formats. The root router is deliberately small and is not a replacement mega-rulebook.

## Current Platform Evidence

Codex locally exposes global `AGENTS.md`, Skills, command Hooks, agent roles, Plugins, Apps, MCP configuration, and Tools. The adapter uses `SessionStart`, a `PreToolUse` managed-surface guard, and a `Stop` work-completed Hook. `Stop` writes one compact Work Signature and deterministically evaluates the Fatigue repetition trigger. Changed command Hooks still require trust for their exact command hash before they run.

`fractal codex inspect` reconciles the registered candidate with the installed Codex version. It uses the local App Server to inspect loaded Skills, trusted Hooks, MCP runtime and authentication state, installed Plugins, installed Apps, and the instruction sources actually loaded by a read-only thread. `plugin/list` is auxiliary evidence only. The status keeps four different claims separate: registered, loaded, callable, and proven by a successful real execution.

The Codex adapter feature-detects the installed release. Support visible in a newer source checkout is not treated as local support. Global and project `AGENTS.md` precedence, `AGENTS.override.md`, and the aggregate instruction byte limit are resolved and compared with `thread/start` instruction sources. The generated global router stays small; project-local instructions are added only when a real project needs them.

Claude receives the corresponding generated root, Skills, verifier and Improvement Researcher roles, and a settings fragment with `SessionStart`, the same managed-surface `PreToolUse` guard, and `Stop`. A Private `adapters/claude/model-route.json` may also register one non-secret model route: the adapter projects the approved alias, model override, gateway endpoint, platform version, and bounded environment settings while preserving the existing platform-owned credential. The installer fails closed when that credential is absent, the selected gateway model is unregistered, or secret material enters the route. Registered local external Skills remain explicit platform references; enabled Plugins and their Skills are recorded individually. Cowork package format is observable on disk, but server-side Settings, connector activation, and execution remain Unknown until a user-visible or real-behaviour check. Gemini has an observed root file, while Skill execution, Hooks, and MCP behaviour remain Unknown unless live evidence says otherwise.

Existing platform MCP servers and connected Apps are registered individually with their permission and secret boundaries. No separate Fractal MCP server is claimed. The deterministic Python runtime and canonical files remain the Fractal control path. Codex configuration is inspected through `config/read` and `configRequirements/read`. An approved change uses one version-checked `config/batchWrite`, read-back verification, and a private restore record; the candidate installer never rewrites `config.toml` directly.

## Human Status Route

Use `fractal components show --registry ~/.codex/fractal/component-registry.json --platform codex` for the registered status, and `fractal codex inspect` for what Codex has loaded now. This is intentionally separate from the slash-command menu. A menu showing “No commands” does not mean the root `AGENTS.md` router failed, and Fractal does not add decorative slash commands.

## Tool Results

Typed result handling preserves every text, image, audio, resource, structured, warning, and error block in original order. A mixed result is `partial-failure`; an unknown block remains `unverified` with its raw reference. The adapter never assumes that the first text block is the complete result.

## Drift and Restore

The component audit detects unregistered live items, registered items missing from an adapter, inactive items that remain discoverable, and changed source or projection hashes. `fs/watch`, `fs/changed`, and `skills/changed` provide event evidence for new drift. A new external item is reported to Legacy Material Review and Naming System; it is never imported or removed automatically. `externalAgentConfig/detect` follows the same review-only route and stores counts and identifiers rather than copied session content.

The Codex and Claude candidate installers preserve previous generated paths, move unmanaged or rejected extras into a recoverable quarantine, switch only the registered active set, and record a restore manifest. The Claude installer merges generated Hooks and an approved model route into the existing settings and disables only enabled Plugins absent from the approved registry; its backup preserves the prior settings, including the earlier model route, exactly. Restore reinstates those exact paths, settings, and quarantined extras; unrelated home contents remain untouched.
