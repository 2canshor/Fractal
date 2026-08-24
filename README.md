# Fractal

Fractal is a self-improving agentic environment for doing Projects and learning from them. While a Project is active, Project Review prevents local work from pulling attention, time, or resources away from the Project as a whole. After the primary user declares Project Completion, System Review uses the original five-step reasoning backbone to make future Projects better.

```text
Brief -> Direction -> Research and Clarification -> Goal
      -> Success Criteria and Priorities -> Plan
      -> Work and Project Review -> Project Completion -> System Review
```

Fractal keeps the Project record, evidence, capabilities, and review history together so an agent can continue work without rebuilding the story from scattered files.

Its improvement hierarchy is:

```text
Continuous Improvement                         Core Philosophy
└── System Review                              Protagonist Mechanism
    ├── Five Steps                             Methodologies
    ├── Fatigue, Curiosity, Greed              Methodologies: Three Values
    ├── Project Review                         Secondary Mechanism
    └── Deterministic Over Probabilistic,
        Work Signature, Naming System,
        Capability Check, Hooks, and others    Mechanisms
```

The [New Blueprint Candidate](docs/blueprint.md) records the primary-user-directed target architecture and its open classifications. It overrides conflicting earlier plans but is deliberately marked inactive until a separately verified System Version implements it. [Blueprint Implementation Gap](docs/blueprint-implementation-gap.md) separates the target from retained alpha.7 implementation evidence, while the [Donor Inventory](docs/donor-inventory.md) treats Hermes and every other external source as a bounded, non-authoritative donor. The currently active alpha.7 reasoning and its mapping to modern Nodes remain documented in [Architecture Lineage](docs/architecture-lineage.md). [Agentic Element Map](docs/agentic-element-map.md) shows which parts belong to the Main Agent, Skills, Hooks, Subagents, MCP, Plugins, and deterministic programs. [Fractal in One Competition Project](docs/product-introduction.md) shows the current complete behaviour through a concrete example. [Architecture Lineage Coverage](docs/architecture-lineage-coverage.md) separates preserved architecture from the runtime work that still needs a new candidate.

[Actions, Commands, Workflows, and Dots](docs/actions-and-commands.md) explains Object-Aware Actions, the small user-facing Skill surface, and how provider methods remain reusable internally.

The public repository contains only reusable architecture, schemas, deterministic programs, tests, and documentation. Personal state, private policies, credentials, conversations, and runtime evidence belong outside this repository.

## Current Status

Development candidate `0.1.0-alpha.8` adds the validated New Blueprint and complete reusable Steal Method while preserving an explicit staged-not-active claim boundary. The active workflow still follows the retained alpha.7 hierarchy until a later bounded implementation closes those recorded gaps. Provider and Tool Skills remain available as maintained internal methods without becoming separate user jobs. The currently active System Version remains unchanged until Carson explicitly invokes `/version`. The repository does not yet make a stability or compatibility promise.

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
