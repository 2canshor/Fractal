# Fractal Architecture

Fractal helps a person complete one Project well and lets the system learn from that completed Project for the next one. These are two different review loops:

1. **Project Review** runs while work is active. A milestone or exception opens the review, and the review checks the whole Project before adjusting its Plan.
2. **System Review** starts after the primary user declares Project Completion. It studies the completed Project, considers how Fractal itself could improve, and returns a result for the primary user's decision.

The human-facing Project path is:

```text
Brief -> Project Direction -> Background Research <-> Clarification
      -> Goal -> Success Criteria + Priorities -> Project Plan
      -> Project Work <-> Project Review -> Project Completion -> System Review
```

Fatigue, Curiosity, Greed, Work Signatures, registries, Hooks, Skills, MCP servers, Plugins, Tools, agent roles, and adapters support that path. They supply evidence or perform bounded work under it.

Fractal also separates reusable system behaviour from the state of any person or organisation.

## Layers

1. **Canonical state** — versioned JSON records validated by JSON Schema.
2. **Event journal** — append-only JSON Lines records describing attempted and accepted changes.
3. **Derived indexes** — rebuildable SQLite FTS5 databases for fast local retrieval.
4. **Deterministic programs** — validation, conflict handling, context assembly, review, and lifecycle transitions.
5. **Adapters** — thin platform-specific entrypoints that translate platform events into Fractal commands.

Human-readable Markdown is a view of canonical state, not a second source of truth.

## Core Philosophy

Continuous Improvement is the only Core Philosophy. During a Project it protects the Project trajectory through Project Review. After Project Completion it improves future Projects through System Review. Methods and Supporting Philosophies must have observable behaviour, tests, and evidence.

## State Change Path

```text
observe -> propose -> validate -> authorise -> apply -> verify -> record
```

A proposal is not active state. A claim is not evidence. Installation is not capability proof.

Consequential local version actions use exact, single-use primary-user receipts bound to the Project revision, action, candidate manifest, and expected prior state. System Review cannot become ready until every discovered Pattern has a disposition, required Curiosity and Two-Sided Review have finished, and the public handoff is complete. Candidate build also requires evidence Claim Gate and architecture-lineage receipts.

## Component Governance

Everything an agent can discover, select, or run is a registered component. This includes Skills, Hooks, agent roles, MCP servers, Plugins, Apps, Tools, adapters, platform runtimes, and later component types. Each record keeps its owner and source, version or content hash, dependency links, permissions and secret boundary, trigger, user-surface audience, automatic and explicit invocation state, active and execution status, platform projection, evidence, overlap decision, and removal or restore route. A user job has one verb, outcome, completion contract, and authority boundary; providers and prerequisites stay internal. An active component cannot depend on an absent or inactive component.

The supported activation path is:

```text
request -> source and overlap check -> Naming System -> permission and evaluation check
        -> registration -> candidate version -> generated adapter -> live verification
```

An adapter carries the approved active set and a Human Control view. A pre-Tool guard blocks direct writes to managed component surfaces outside the candidate installer. Runtime reconciliation separates registration, loading, callability, and successful execution evidence. Drift checks report an active component missing from the registry, a registered component missing from a platform, a changed source hash, or a watched platform change. Uncertain material is routed to review or quarantined outside active discovery and remains recoverable.

## Public and Private Boundary

The public system may define contracts and generic examples. A private workspace supplies profile state, policies, projects, memory records, selected imports, and private adapter overlays. Runtime databases and logs remain local unless an explicit export is authorised.
