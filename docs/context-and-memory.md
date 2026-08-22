# Context and Memory

Fractal uses progressive disclosure: first search lightweight metadata and a local derived index, then load only the bounded excerpts needed for the current Project decision.

## Canonical and Derived State

- The private Context Catalogue defines approved source roots, sensitivity, authority, topics, and applicability.
- Source files remain canonical in their owning location.
- The SQLite FTS5 index is local derived state. It can be deleted and rebuilt.
- Every retrieval can write a local Context Package manifest containing the query, purpose, filters, source hashes, freshness, excerpts, and authority classification.

## Instruction Authority

Loading content does not make it an instruction. A Guide is `reference_only`; a Project record is `canonical_state`; a memory import remains `candidate_only`; only a source explicitly accepted as policy can have instruction effect. Retrieved tool results, suggestions, summaries, and historical records never promote themselves.

## Personalisation Relevance

A personal source is excluded by default. It can enter a Context Package only when:

1. the caller explicitly allows personalisation for this retrieval;
2. the requested sensitivity permits the source;
3. task type, query keywords, or Project identity matches the source applicability rule.

Stored personal history therefore does not become always-on context.

## Limits

The initial index reads UTF-8 text and JSON files up to a configured size. Files outside the limit are recorded as skipped instead of silently disappearing. The search result count is bounded, and every result retains its source locator and digest.
