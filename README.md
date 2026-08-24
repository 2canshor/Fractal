# Fractal

Fractal is a self-improving agentic environment for doing Projects and learning from them. While a Project is active, Perspective prevents local work from pulling attention, time, or resources away from the Project as a whole. After the primary user declares Project Completion, System Review uses the eight-step New Blueprint workflow to make future Projects better.

```text
Brief -> Direction -> Research and Clarification -> Goal
      -> Success Criteria and Priorities -> Plan
      -> Work and Perspective -> Project Completion -> System Review
```

Fractal keeps the Project record, evidence, capabilities, and review history together so an agent can continue work without rebuilding the story from scattered files.

Its improvement hierarchy is:

```text
Continuous Improvement                         Core Philosophy
└── # System Review                            Sole Protagonist
    ├── * Steps                                eight $ Steps
    ├── * Values                               Fatigue, Curiosity, Greed
    ├── * Principles                           %, including Subtraction First
    ├── * Infrastructure                       ^ supporting Extras
    └── * Methods                              $ Perspective and reusable ¢ Props
```

The [New Blueprint](docs/blueprint.md) is the canonical active workflow architecture. [Blueprint Implementation Gap](docs/blueprint-implementation-gap.md) separates current implementation and execution evidence without creating a second architecture, while the [Donor Inventory](docs/donor-inventory.md) treats Hermes and every other external source as a bounded, non-authoritative donor. [Agentic Element Map](docs/agentic-element-map.md) shows which parts belong to the Main Agent, Skills, Hooks, Subagents, MCP, Plugins, and deterministic programs.

[Actions, Commands, Workflows, and Dots](docs/actions-and-commands.md) explains Object-Aware Actions, the small user-facing Skill surface, and how provider methods remain reusable internally.

The public repository contains only reusable architecture, schemas, deterministic programs, tests, and documentation. Personal state, private policies, credentials, conversations, and runtime evidence belong outside this repository.

## Current Status

The current correction compiles the New Blueprint into the active workflow and retains the complete reusable Steal Method without starting Initialized Steal or a Hermes environment. Provider and Tool Skills remain available as maintained internal methods without becoming separate user jobs. A correction remains a candidate until an explicit `/version` order completes build, activation, fresh-turn verification, and exact publication. The repository does not yet make a stability or compatibility promise.

Fractal registers every active or discoverable Skill, Hook, agent role, MCP, Plugin, App, Tool, and adapter individually. Use `fractal components show` for the human-readable registered view and `fractal codex inspect` to compare it with the live Codex runtime. Registration, loading, callability, and successful execution are reported separately. A component being installed or present in a cache is not proof that it is active or working.

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
