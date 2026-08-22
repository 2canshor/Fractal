# Fractal

Fractal is an experimental local-first system for continuous improvement. It keeps canonical state explicit, records evidence separately from claims, and requires verification before a proposed change becomes active.

The public repository contains only reusable architecture, schemas, deterministic programs, tests, and documentation. Personal state, private policies, credentials, conversations, and runtime evidence belong outside this repository.

## Current Status

Version `0.1.0-alpha.1` is an initial development release. The repository does not yet make a stability or compatibility promise.

## Development

Requirements:

- Python 3.12 or newer
- `uv`

```sh
uv sync --locked --all-extras --dev
uv run fractal version
uv run pytest
uv run ruff check .
```

## Repository Boundary

Before a public push, the verification suite checks for common secret forms, absolute home-directory paths, and private application markers. A passing scan is a necessary boundary check, not proof that a file is safe to publish.

## Licence

No licence has been selected yet. Public visibility alone does not grant permission to copy, modify, or redistribute the code.
