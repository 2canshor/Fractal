# Platform Adapters

Fractal generates Codex, Claude, Cowork, and Gemini projections in one direction from a pinned Public commit and selected Private state. An installed projection never overwrites canonical source in reverse.

Each adapter contains a minimal root router, a bounded Project and authority context, capability metadata, the applicable Skill folders or packages, a limitation record, and a hash-pinned manifest. Codex and Claude also receive their locally observed hook or agent formats. The root router is deliberately small and is not a replacement mega-rulebook.

## Current Platform Evidence

Codex locally exposes global `AGENTS.md`, Skills, command Hooks, agents, plugins, and MCP configuration. The adapter uses `SessionStart` plus a protected-legacy `PreToolUse` guard; changed command Hooks still require Codex trust before they run. Claude has locally observed root instructions, Skills, a verifier agent, and `SessionStart` settings. Cowork package format is observable on disk, but server-side Settings, connector activation, and execution remain Unknown until a user-visible or real-behaviour check. Gemini has an observed root file, while Skill discovery, Hooks, and the empty MCP configuration remain Unknown.

No Fractal MCP server is claimed in the initial adapter. The deterministic Python runtime and canonical files are the fallback until an MCP interface is implemented, reviewed, connected, and exercised.

## Tool Results

Typed result handling preserves every text, image, audio, resource, structured, warning, and error block in original order. A mixed result is `partial-failure`; an unknown block remains `unverified` with its raw reference. The adapter never assumes that the first text block is the complete result.

## Drift and Restore

The audit compares every managed file digest and reports missing, changed, and unexpected paths. Installation backs up only files owned by the adapter manifest. Restore reinstates those exact files and removes only newly installed managed files; unrelated home contents remain untouched.
