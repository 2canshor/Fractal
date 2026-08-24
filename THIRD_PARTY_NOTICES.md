# Third-Party Notices

## Hermes Dojo session signal adaptation

Fractal's staged `fractal.donor_signals` module adapts bounded detection ideas from:

- Project: Hermes Dojo
- Source: <https://github.com/Yonkoo11/hermes-dojo>
- Commit: `ee114e72e18b13d3aeb4b76a8d1ade0916972248`
- Original files: `scripts/monitor.py`, `scripts/analyzer.py`

The adaptation retains tool-failure, possible-user-correction and retry-loop signal ideas. It removes Hermes storage coupling, Skill mutation, Skill creation, GEPA, cron, report delivery and promotion authority. The adapted output is a local `Hooks` implementation that supplies privacy-bounded evidence to the `Find Problems` Flow; it cannot approve or apply a change.

MIT License

Copyright (c) 2026 Yonkoo11

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Hermes Agent durable execution, investigation and verification adaptations

Fractal's staged `fractal.orchestrator` execution ledger and
`fractal.reality` execution receipt adapt bounded mechanics from:

- Project: Hermes Agent
- Source: <https://github.com/NousResearch/hermes-agent>
- Release: `v0.20.5` / tag `v2026.8.19`
- Commit: `fcbd1076a93841fa88855acce810e342a5b78101`
- Acquired: `2026-08-24`
- Original files: `cron/executions.py`, `agent/verification_evidence.py`, `run_agent.py`, `agent/background_review.py`, `agent/curator.py`, `agent/curator_backup.py`
- `run_agent.py` SHA-256: `b8e0244cfdbdce9328040d92adb9b89d78351000ee88bafae35d71b3e33fb8a1`
- Licence evidence: `LICENSE`, SHA-256 `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6`

The adaptation retains durable claimed/running/terminal execution attempts,
unknown-state recovery, exact command evidence, stale-proof semantics, a
bounded automatic evidence/reason/validate loop and an iteration budget. It
also retains the existing-method-first and later-consolidation bias from the
post-work review path. It
removes the Hermes scheduler, provider and Tool engines, session and memory
stores, retry/fallback policy, Skill mutation, final-decision authority and
autonomous promotion.

MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Temporal Python SDK workflow/activity separation

- Project: Temporal Python SDK
- Source: <https://github.com/temporalio/sdk-python>
- Release: `1.31.0`
- Commit: `84b519e0ff407b049da88ac7d1711f110494ff4d`
- Acquired: `2026-08-24`
- Licence: MIT; `LICENSE` SHA-256 `d7706f28d144dabf07e4946553a4d117d1dab5cda6bf34a602959b4a2fa86b38`

Fractal adapts only the separation between deterministic workflow coordination
and side-effecting Activities. The Temporal SDK, server, worker, task queue,
replay authority and service state are not dependencies of this candidate.

## in-toto command/material/product receipt adaptation

- Project: in-toto
- Source: <https://github.com/in-toto/in-toto>
- Release: `v3.1.0`
- Commit: `c82fe5d21aaa61c7f1a213db20a46f10bb3f411a`
- Acquired: `2026-08-24`
- Original file: `in_toto/runlib.py`
- Licence: Apache-2.0; `LICENSE` SHA-256 `b5c26c8a2ad6dd7cac6646e470a32ee42d23c36124caac4e66c989b6545b2a44`

Fractal adapts the command, materials, products, return value and byproducts
receipt shape. The in-toto package, signing keys, DSSE, GPG integration,
functionary authority and multi-step layout are not dependencies of this
candidate. The Apache License 2.0 text is available at
<https://www.apache.org/licenses/LICENSE-2.0>.

## MLflow outcome-ratchet adaptation

- Project: MLflow
- Source: <https://github.com/mlflow/mlflow>
- Release: `v3.15.1`
- Commit: `9a1c0d9a9827acd23c7a215f0999e4b0f97e9870`
- Acquired: `2026-08-24`
- Original file: `mlflow/models/evaluation/validation.py`
- Original file SHA-256: `277c8d62be9f2584250bf4d6fc8cb3f6446ffbf9e9b94701d7d9721a8bed3df2`
- Licence: Apache-2.0; `LICENSE.txt` SHA-256 `6395355de6f391afff35996a30fb41b189b4991a4cb54993ace35ab69a0bfa28`

Fractal adapts only the generic metric direction, minimum absolute change,
minimum relative change, missing-evidence failure and per-metric comparison
shape. MLflow itself, its tracking service, model result types, registry,
promotion path and product identity are not dependencies of this candidate.
Greed retains the original successful result and can only hand a materially
stronger, evidenced trial to Flow 5; it cannot approve or activate the change.
The same local metric shape supports Global Outcome checks, where named
protected dimensions cannot regress behind a better local metric.

The Apache License 2.0 text is available at
<https://www.apache.org/licenses/LICENSE-2.0>.

## Optuna protected-dimension feasibility adaptation

- Project: Optuna
- Source: <https://github.com/optuna/optuna>
- Release: `v4.9.0`
- Commit: `4db42e31c24b200e52595df9d4c00e2cdeefea2b`
- Acquired: `2026-08-24`
- Original file: `optuna/study/study.py`
- Original file SHA-256: `df4e26f8e2152286fac90c5a60b7caa64a556f38d9c63b91d5d6ebb26d1a9075`
- Licence: MIT; `LICENSE` SHA-256 `c3df8e8523cf46be4b366ee7dd11578454b10ea5ec5159e57df849513aafe059`

Fractal retains only the rule that a best-looking trial is not feasible when
a recorded constraint is violated. It applies that rule to primary-user-
protected global dimensions. Optuna studies, samplers, pruners, storage,
automatic best-trial selection and optimisation authority are excluded.

## Evidence Exploration adaptations

### STORM perspective-guided question source

- Project: STORM
- Source: <https://github.com/stanford-oval/storm>
- Release: `v1.1.0`
- Commit: `e80d9bbea7362141a479940dabb751c1f244e4b6`
- Acquired: `2026-08-24`
- Original files: `knowledge_storm/storm_wiki/modules/knowledge_curation.py`, `knowledge_storm/storm_wiki/modules/persona_generator.py`
- File SHA-256: `c3f037dc808287631825e6e9bb4dd0b51b9ca43f1c790a713d41513b7ae2c7e0`, `5c387e2428808ed6895aad0268fbd7664fcebf362e2cf2a87dde3f712762e219`
- Licence: MIT; `LICENSE` SHA-256 `88241386c28ff04821832762714c9ff0d22c4de6b0633d11554e69a968a718e0`

Fractal retains perspective-labelled question planning, breadth limits and the
separation between evidence collection and later writing. DSPy, donor models,
retrievers, simulated personas, conversations and article generation are
excluded.

### GPT Researcher planned retrieval source

- Project: GPT Researcher
- Source: <https://github.com/assafelovic/gpt-researcher>
- Release: `v3.6.1`
- Commit: `6f998577d547b1e54ec662dac63583aa11e3b84b`
- Acquired: `2026-08-24`
- Original files: `gpt_researcher/actions/query_processing.py`, `gpt_researcher/actions/retriever.py`
- File SHA-256: `aa8ccb47136be004deec41721e85e6604e16354dc624afef9af2b9818246a538`, `0cbfd443b01a0bf6c220cea85391648491034a924239c82ad0aaf3b0e8a315ac`
- Licence: Apache-2.0; `LICENSE` SHA-256 `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`

Fractal retains planned query expansion, a replaceable retriever boundary,
source provenance and honest No Finding. Donor agents, LLM/provider selection,
crawlers, vector stores, Web services and report generation are excluded.

The local implementation is named `Evidence Exploration`. It can only supply
raw evidence to `Cause Research`; it does not own synthesis, a Flow, canonical
state or change authority.

## Locally forked learning methods

`fractal.self_improvement`, `learning-method-sources.json` and the local
Learning Review path adapt bounded methods from the following sources. These
adaptations are implemented and packaged locally. No donor service, model,
website, plugin engine, Skills Hub or repository is contacted at runtime.

### Blind Spot Review source

- Project: Super Hermes
- Source: <https://github.com/Cranot/super-hermes>
- Commit: `ffe2d10042041dcc23325f013e8b7e607e069952`
- Acquired: `2026-08-24`
- Original files: `skills/prism-reflect/SKILL.md`, `skills/prism-3way/SKILL.md`, `skills/prism-scan/SKILL.md`
- Licence: MIT; `LICENSE` SHA-256 `28273a2e019078e77d7ddbecaa18ca518e18a5e30c694c4115b82b431bfe35ca`
- Copyright: Copyright (c) 2026 Cranot

Fractal retains independent structural, temporal and constraint views plus a
carried-forward blind-spot report. Donor Skill invocation, model-scored depth,
Skills Hub installation and product naming are excluded.

### Weakness Triage source

- Project: Hermes Dojo
- Source: <https://github.com/Yonkoo11/hermes-dojo>
- Commit: `ee114e72e18b13d3aeb4b76a8d1ade0916972248`
- Acquired: `2026-08-24`
- Original files: `scripts/monitor.py`, `scripts/analyzer.py`, `scripts/fixer.py`
- Licence: MIT; `LICENSE` SHA-256 `5f9a81437e325c36cc6bfbf0da538aa9437dfb0e1f51d44638c819d5cad561bb`
- Copyright: Copyright (c) 2026 Yonkoo11

Fractal retains recurrence ranking and separation of method weakness from
infrastructure, authentication, rate-limit and temporary setup state. The
auto-fixer, GEPA, cron and direct Skill mutation are excluded.

### Checkpoint Inspection source

- Project: Hermes Workspace
- Source: <https://github.com/outsourc-e/hermes-workspace>
- Commit: `c631425d8baa933f8c61d8447040f4ec8b5f571c`
- Acquired: `2026-08-24`
- Original files: `src/routes/api/swarm-orchestrator-loop.ts`, `src/routes/api/external-memory/candidates.ts`, `src/routes/api/swarm-checkpoint.ts`
- Licence: MIT; `LICENSE` SHA-256 `c113b60466b6181583e1079680900641678e6c84fefe8e6460f262468c350792`
- Copyright: Copyright (c) 2026 Eric (outsourc-e)

Fractal retains compact checkpoint fields, deduplication and review-before-
continuation. The Web workspace, swarm product and remote harness authority
are excluded.

### Learning Evidence source

- Project: Evey Hermes Plugins
- Source: <https://github.com/42-evey/hermes-plugins>
- Commit: `b96aa74a44519bcc930b235a43d4e43824af9433`
- Acquired: `2026-08-24`
- Original files: `evey-learner/__init__.py`, `evey-reflect/__init__.py`, `evey-verification/__init__.py`, `evey-cost-guard/__init__.py`
- Licence: MIT; `LICENSE` SHA-256 `f12fdcd788a49f06544e7fec13dfb62570f0ddf2efcfd1375f14b2faf103d1b4`
- Copyright: Copyright (c) 2026 Evey (https://evey.cc)

Fractal retains compact learning, reflection, verification and cost evidence.
The plugin engine, model routing, wallet, telemetry and autonomous goal
authority are excluded.

### Work Method Extraction source

- Project: Colleague Skill source (`distilly` 1.0.0)
- Source: <https://github.com/titanwings/colleague-skill>
- Commit: `04c72cc26c04e12c673405b94c8a42400287d403`
- Acquired: `2026-08-24`
- Original files: `prompts/work_analyzer.md`, `prompts/correction_handler.md`, `tools/skill_writer.py`, `tools/skill_schema.py`
- Licence: MIT; `LICENSE` SHA-256 `4364a2b804d4c6e6c1ab47226c7b48b7364701e1827b83c600d0c4ba46d009ec`
- Copyright: Copyright (c) 2026 titanwings

Fractal retains portable work-procedure extraction, corrections, evidence gaps
and method-file generation. Persona simulation, relationship recreation,
celebrity research and runtime-specific installers are excluded.

All sources above use the MIT licence text reproduced earlier in this file.
