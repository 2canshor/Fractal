# Donor Inventory

- Observed At: `2026-08-24`
- Blueprint Version: `0.1.0-candidate.4`

> Repository contents were inspected at exact commits. Inventory status does not install, approve or activate donor code.

## Sources

| Donor | Repository Status | Exact Commit | Licence | Role |
|---|---|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `observed` | `91e867631e9d2eb9fbd69edd4459475d38070979` | MIT / verified-file | `environment-and-implementation-source` |
| [Hermes Agent Self-Evolution](https://github.com/NousResearch/hermes-agent-self-evolution) | `observed` | `0a929e3aa20e15cf04dc7c28492a7d41a5139125` | MIT / declared-no-licence-file | `method-and-implementation-source` |
| [Hermes Dojo](https://github.com/Yonkoo11/hermes-dojo) | `observed` | `ee114e72e18b13d3aeb4b76a8d1ade0916972248` | MIT / verified-file | `method-and-implementation-source` |
| [Evey Hermes Plugins](https://github.com/42-evey/hermes-plugins) | `observed` | `b96aa74a44519bcc930b235a43d4e43824af9433` | MIT / verified-file | `plugin-implementation-source` |
| Super Hermes | `no-primary-source-finding` | None | Unknown / unknown | `unresolved-source` |
| dot-skill | `no-primary-source-finding` | None | Unknown / unknown | `unresolved-source` |

## Bounded Capabilities

| Donor | Capability | Blueprint Target | Disposition | Reason |
|---|---|---|---|---|
| `hermes-agent` | `hermes-runtime` — Agent runtime, provider abstraction, Tool transport and multiple terminal backends. | `environment-adapters` | `future-migration-source` | Potential Hermes environment foundation; requires a Fractal adapter and portability proof. |
| `hermes-agent` | `hermes-session-memory` — Local session history, FTS5 search, memory and skill persistence. | `environment-adapters` | `research-only` | Useful runtime storage, but Hermes state cannot become Fractal canonical authority. |
| `hermes-agent` | `hermes-cron` — Scheduled and background jobs with platform delivery. | `environment-adapters` | `future-migration-source` | Potential Infrastructure implementation; job execution cannot inherit system-change authority. |
| `hermes-agent` | `hermes-autonomous-skill-change` — Autonomous skill creation and self-improvement during use. | `steal` | `quarantined` | Detection and candidate generation may be decomposed; automatic mutation and promotion conflict with Fractal authority. |
| `hermes-agent-self-evolution` | `evolution-dataset-builder` — Synthetic, golden and session-derived evaluation datasets with train, validation and holdout splits. | `steal` | `research-only` | Relevant evaluation design, but licence packaging and dataset integrity require resolution before code reuse. |
| `hermes-agent-self-evolution` | `evolution-constraint-validator` — Artifact size, growth, structure and test-suite constraints. | `capability-check` | `research-only` | Potential bounded validation logic; current repository issues document critical pipeline gaps and the licence file is absent. |
| `hermes-agent-self-evolution` | `evolution-fitness` — DSPy and GEPA fitness and reflective mutation pipeline. | `experiment` | `quarantined` | Observed source still uses keyword overlap as the optimizer metric; Experiment remains unclassified in the Blueprint. |
| `hermes-dojo` | `dojo-session-signals` — Detect tool failures, user corrections and rapid retry loops from session events. | `find-problems` | `staged-adaptation-candidate` | Fills a partial evidence gap when adapted to typed, privacy-bounded signals without automatic change. |
| `hermes-dojo` | `dojo-weakness-analysis` — Rank tool and skill weaknesses and distinguish some infrastructure and authentication failures. | `find-global-pattern-reasons` | `research-only` | Useful categories, but heuristic recommendations do not establish Fractal causal evidence. |
| `hermes-dojo` | `dojo-auto-fixer` — Patch or create Skills and invoke self-evolution automatically. | `steal` | `reject-authority` | Mutation authority and automatic promotion conflict with Blueprint Mapping and Carson control. |
| `hermes-dojo` | `dojo-reporter` — Generate performance reports and retain learning-curve metrics. | `human-control` | `research-only` | Potential operator-facing Infrastructure after its metrics and claims are independently validated. |
| `evey-hermes-plugins` | `evey-council` — Three-model debate and consensus extraction. | `two-sided-review` | `research-only` | Potential implementation source, but consensus is not proof of independent Case For and Case Against. |
| `evey-hermes-plugins` | `evey-model-routing` — Task-sensitive model routing, retries, fallback and privacy filtering. | `environment-adapters` | `research-only` | Potential Infrastructure implementation after deterministic routing and privacy behaviour are verified. |
| `evey-hermes-plugins` | `evey-status-and-cost` — Unified status and cost-control plugins. | `human-control` | `research-only` | Potential operator Infrastructure; external telemetry and budget semantics require separate review. |
| `evey-hermes-plugins` | `evey-autonomy` — Autonomous decision, planning and reflection tools. | `system-review` | `reject-authority` | The donor autonomy engine cannot replace Fractal's Protagonist or Human authority. |
