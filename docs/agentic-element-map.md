# Agentic Element Map

This map answers one practical question: **which kind of agentic element should perform each Fractal Node?**

The selection uses cross-platform ideas retained in `Documents/Guides/Claude`. It uses each element for the job it is good at:

| Element | Plain meaning | Best fit in Fractal |
|---|---|---|
| Main Agent | The agent holding the complete Project story | Connected judgement, synthesis, trade-offs, and the final recommendation |
| Skill | A reusable way of doing a task | System Review, Project Review, research, Naming, and other procedures |
| Hook | A response to a known event | Session start, tool boundary, milestone signal, and work completion |
| Subagent | A fresh, isolated worker | Independent research, Case For/Against, and black-box verification |
| MCP | A governed connection to outside data or services | External Research and current source evidence |
| Plugin | A distributable bundle | Packaging a coherent family of Skills, Hooks, Subagents, and MCP connections |
| deterministic program | Code that gives the same answer from the same input | State, schema, hashes, permissions, routing, comparison, evidence capture, and restore |

The architecture keeps dependent reasoning in the Main Agent. It creates a Subagent when independent context has a real benefit. This follows the retained Guide advice to start with the simplest workflow and split work by context boundary instead of inventing many roles.

## Canonical hierarchy and mapping

| Node | Layer | Primary element | Supporting elements | Current evidence |
|---|---|---|---|---|
| Continuous Improvement | Core Philosophy | Main Agent | Skill, Hook, deterministic program | Partially implemented; complete behaviour has synthetic evidence |
| System Review | Protagonist Mechanism | Main Agent | Skill, Subagent, MCP, deterministic program | Partially implemented; staged Skill and synthetic review tests |
| Step 1: Find Problems | Methodology | Main Agent | Skill, deterministic program | Partially implemented; synthetic tests |
| Step 2: Find Local Patterns | Methodology | Main Agent | Skill, deterministic program | Partially implemented; synthetic tests |
| Step 3: Compare History | Methodology | Main Agent | Skill, Subagent, MCP, deterministic program | Partially implemented; synthetic tests |
| Step 4: Choose the System Response | Methodology | Main Agent | Skill, deterministic program | Partially implemented; synthetic tests |
| Step 5: Reality Check | Methodology | Main Agent | Skill, Subagent, deterministic program | Partially implemented; synthetic tests |
| Fatigue | Value / Methodology | deterministic program | Hook, Subagent, Skill | Work Signature and repetition trigger verified live; full investigation path remains partial |
| Curiosity | Value / Methodology | Skill | Subagent, MCP, deterministic program | Partially implemented; staged and synthetic evidence |
| Greed | Value / Methodology | Skill | Main Agent, MCP, deterministic program | Partially implemented; synthetic evidence |
| Project Review | Secondary Mechanism | Main Agent | Skill, Hook, deterministic program, optional verifier Subagent | Canonical Project Review records exist; the complete Skill contract has synthetic evidence |
| Deterministic Over Probabilistic | Mechanism | deterministic program | Hook, Skill | Implemented and used live by state, Hook, hash, and governance paths |
| Quantity over Quality | Mechanism | Skill | Main Agent, deterministic program | Partially implemented; whole-history and no-early-causality contract still needs full runtime enforcement |
| Subtraction First | Mechanism | Skill | deterministic program | Partially implemented; option order tested synthetically |
| Global Outcome Over Local Optimisation | Mechanism | Main Agent | Skill, deterministic program | Partially implemented; later real outcome loop remains incomplete |
| Work Signature | Mechanism | Hook | deterministic program | Verified live |
| Naming System | Mechanism | Skill | deterministic linter, activation Hook | Built and projected; synthetic naming eval |
| Capability Check | Mechanism | deterministic program | Skill, Hook, MCP status | Verified live for registry and reconciliation |
| Hooks | Mechanism | Hook | deterministic program | SessionStart, PreToolUse, and work completion verified live on Codex |
| Component Governance | Mechanism | deterministic program | Hook, Plugin, MCP | Registry, adapter projection, reconciliation, and drift handling verified live |
| Cause Research | Mechanism | Subagents | Main Agent, Research Skill, MCP, deterministic independence check | Partially implemented; synthetic evidence |
| Two-Sided Review | Mechanism | Subagents | Main Agent, deterministic independence check | Partially implemented; synthetic evidence |
| Experiment | Mechanism | deterministic program | Skill, verifier Subagent | Built and synthetic-tested; no general live claim |
| Human Control | Mechanism | deterministic renderer | Main Agent explanation | Candidate view verified staged |

## The five Steps in operational detail

### Step 1 — Find Problems

- Main Agent reads the completed Project as one story.
- System Review Skill instructs it to collect broadly before explaining.
- deterministic program assembles the whole-history manifest and keeps Positive Delta, Negative Delta, source evidence, and uncertainty in fixed fields.
- Quantity over Quality is the Mechanism that preserves high recall here.

### Step 2 — Find Local Patterns

- Main Agent separates symptoms from plausible causes and identifies Local Patterns.
- System Review Skill provides the causal questions.
- deterministic program keeps observation, proximate cause, possible common cause, counterexample, and confidence distinct.

### Step 3 — Compare History

- deterministic program retrieves previous Projects, System Reviews, proposals, System Versions, interventions, reversals, and outcomes.
- Main Agent looks for recurrence, contradiction, overcorrection, and patterns of patterns.
- Reversal Check uses exact history first to flag repeated directional changes.
- When the causal model is doubtful, one read-only Subagent investigates external evidence and another independently reviews Fractal's internal history.
- MCP supplies registered external sources. Main Agent reconciles agreement and disagreement.

### Step 4 — Choose the System Response

- Main Agent decides what the evidence calls for.
- Subtraction First Skill orders the response families.
- Capability Check uses the registry, overlap decisions, permissions, status, and evidence.
- deterministic program verifies that delete, shorten, merge, simplify, reconfigure, modify, add, experiment, and no change received the required consideration.

### Step 5 — Reality Check

- deterministic program preserves the baseline, Expected Effect, Local Effect, Global Effect, and later follow-up state.
- Main Agent asks whether Fractal genuinely improved.
- For a consequential proposal, isolated Subagents form Case For and Case Against before seeing each other's position.
- Main Agent produces Final Assessment and Biggest Remaining Concern.
- the primary user's typed decision enters through the deterministic authority gate.

## The Three Values in operational detail

### Fatigue

The work-completed Hook captures one compact Work Signature. A deterministic matcher handles clear repetition. When the evidence warrants investigation, the read-only `improvement-researcher` Subagent explores a better method and returns evidence to Project Review or System Review.

### Curiosity

The Research Skill defines the search procedure. A read-only Subagent handles a large, independent investigation. MCP connects registered sources. A deterministic provenance check keeps source, date, and the structural relationship of an analogy visible.

### Greed

The Skill runs a Success Criteria Challenge against a verified baseline. The Main Agent chooses the meaningful improvement dimension. A deterministic comparator preserves the original achieved result while comparing quality, time, Tokens, scope, or another approved outcome dimension.

## Platform packaging

Plugin is a packaging choice, not a reasoning authority. A Fractal Plugin can distribute a coherent capability family, while every included Skill, Hook, Subagent, MCP, Tool, and adapter remains individually registered, permissioned, versioned, and evidenced.

The generated root `AGENTS.md` remains a short Router. It points the active agent to Fractal's generated context and approved component set; the procedures remain in Skills and deterministic programs.

## Evidence boundary

The canonical JSON keeps three statements separate for every mapped Node:

1. **source** — whether the implementation exists;
2. **projection** — whether an agent platform currently exposes it;
3. **execution** — whether a real event, staged candidate, or synthetic test has proved it.

The machine-readable source is `src/fractal/data/agentic-element-map.json`. Its validator requires one and only one mapping for every Node in `method-registry.json`.
