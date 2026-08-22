# Minimum Project Recording Core

The recording core persists one canonical Project record in a private workspace and keeps its append-only event journal in local runtime storage.

## Guarantees

- A Project has a stable validated identity and an explicit System Version pin.
- Every successful write uses an expected record revision.
- Compatible stale appends are merged without dropping either platform's record.
- A conflicting stale change is not guessed away; it creates a pending `Request Decision`.
- Canonical JSON is written atomically and checked against a SHA-256 digest on every read.
- Local JSON Lines events form a hash chain and must reach the canonical record revision.
- Completion, version changes, and decision approval cannot use the ordinary write path.
- The Human Control summary is derived from canonical state.
- SQLite FTS5 indexes are disposable and rebuildable.

## Storage Boundary

```text
private workspace/projects/active/<project-id>/record.json
private workspace/projects/active/<project-id>/record.sha256
local runtime/events/<project-id>.jsonl
local runtime/indexes/projects.sqlite
```

Lock files and temporary files are operational state and are not committed.

## Minimum CLI

```sh
fractal project create \
  --project-root /path/to/private/projects/active \
  --runtime-root /path/to/local/runtime \
  --project-id example-project \
  --title "Example Project" \
  --platform example-adapter

fractal project show \
  --project-root /path/to/private/projects/active \
  --runtime-root /path/to/local/runtime \
  --project-id example-project

fractal project verify \
  --project-root /path/to/private/projects/active \
  --runtime-root /path/to/local/runtime \
  --project-id example-project
```
