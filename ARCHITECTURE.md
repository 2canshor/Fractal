# Fractal Architecture

Fractal separates reusable system behaviour from the state of any person or organisation.

## Layers

1. **Canonical state** — versioned JSON records validated by JSON Schema.
2. **Event journal** — append-only JSON Lines records describing attempted and accepted changes.
3. **Derived indexes** — rebuildable SQLite FTS5 databases for fast local retrieval.
4. **Deterministic programs** — validation, conflict handling, context assembly, review, and lifecycle transitions.
5. **Adapters** — thin platform-specific entrypoints that translate platform events into Fractal commands.

Human-readable Markdown is a view of canonical state, not a second source of truth.

## Core Philosophy

Continuous Improvement is the only Core Philosophy. Methods and Supporting Philosophies must have observable behaviour, tests, and evidence. Their mapping to system nodes is versioned and may be proposed for change when evidence supports it.

## State Change Path

```text
observe -> propose -> validate -> authorise -> apply -> verify -> record
```

A proposal is not active state. A claim is not evidence. Installation is not capability proof.

## Component Governance

Everything an agent can discover, select, or run is a registered component. This includes Skills, Hooks, agent roles, MCP servers, Plugins, Tools, adapters, platform runtimes, and later component types. Each record keeps its owner and source, version or content hash, dependency links, permissions and secret boundary, trigger, active and execution status, platform projection, evidence, overlap decision, and removal or restore route. An active component cannot depend on an absent or inactive component.

The supported activation path is:

```text
request -> source and overlap check -> Naming System -> permission and evaluation check
        -> registration -> candidate version -> generated adapter -> live verification
```

An adapter carries the approved active set and a Human Control view. A pre-Tool guard blocks direct writes to managed component surfaces outside the candidate installer. Drift checks report an active component missing from the registry, a registered component missing from a platform, or a changed source hash. Uncertain material is quarantined outside active discovery and remains recoverable.

## Public and Private Boundary

The public system may define contracts and generic examples. A private workspace supplies profile state, policies, projects, memory records, selected imports, and private adapter overlays. Runtime databases and logs remain local unless an explicit export is authorised.
