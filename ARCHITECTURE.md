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

## Public and Private Boundary

The public system may define contracts and generic examples. A private workspace supplies profile state, policies, projects, memory records, selected imports, and private adapter overlays. Runtime databases and logs remain local unless an explicit export is authorised.
