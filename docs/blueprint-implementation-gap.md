# Blueprint Implementation Gap

- Blueprint Version: `0.1.0-candidate.4`
- Active System Version Compared: `0.1.0-alpha.7-778bab7`
- Architecture Only: `0`
- Partial: `21`
- Implemented: `11`

> This audit describes source and retained execution evidence. It does not promote the Blueprint candidate or any donor implementation.

| Blueprint Element | Assessment | Target Alignment | Retained Evidence | Gap |
|---|---|---|---|---|
| `continuous-improvement` | `partial` | `needs-blueprint-projection` | `continuous-improvement`: partially-implemented / not-applicable / verified-synthetic | The philosophy has existing orchestration evidence, but the active registry still projects the old five-step and Mechanisms hierarchy. |
| `system-review` | `partial` | `needs-workflow-redesign` | `system-review`: implemented / staged / verified-synthetic | The active implementation enforces the old five-step workflow rather than the explicit Blueprint Steps. |
| `find-problems` | `implemented` | `aligned-core-concept` | `find-problems`: implemented / staged / verified-synthetic | The core behaviour exists; it still needs projection through the New Blueprint. |
| `find-local-patterns` | `implemented` | `aligned-core-concept` | `find-local-patterns`: implemented / staged / verified-synthetic | The core behaviour exists; it still needs projection through the New Blueprint. |
| `find-global-patterns` | `partial` | `needs-step-extraction` | `compare-history`: implemented / staged / verified-synthetic | Cross-Project comparison exists, but Find Global Patterns is not an explicit target Step with its own contract. |
| `find-global-pattern-reasons` | `partial` | `needs-step-extraction` | `cause-research`: partially-implemented / staged / verified-synthetic | Cause Research exists as a supporting method, but causal reasoning for every Global Pattern is not an explicit enforced Step. |
| `find-global-pattern-solutions` | `partial` | `needs-step-extraction` | `choose-system-response`: implemented / staged / verified-synthetic<br>`curiosity`: partially-implemented / staged / verified-synthetic | Solution search exists inside older Improvement Options and Curiosity contracts, not as one explicit Step per Global Pattern. |
| `map-implementations-to-blueprint` | `partial` | `needs-workflow-redesign` | None | A deterministic Candidate Mapping contract and staged donor record now exist, but the active System Review workflow does not invoke them. |
| `debate-global-pattern-solutions` | `partial` | `needs-step-extraction` | `two-sided-review`: partially-implemented / staged / verified-synthetic | Two-Sided Review exists, but debate is not yet an explicit Step covering every relevant Global Pattern solution. |
| `present-decisions-one-by-one` | `partial` | `needs-step-extraction` | `system-review`: implemented / staged / verified-synthetic<br>`human-control`: implemented / staged / verified-staged | Plain-language handoff exists, but the target one-prepared-decision-at-a-time Step is not represented or enforced. |
| `fatigue` | `partial` | `needs-reclassification` | `fatigue`: partially-implemented / active / verified-live | Work Signature and live repetition evaluation exist, while the full Value remains partially defined. |
| `curiosity` | `partial` | `needs-reclassification` | `curiosity`: partially-implemented / staged / verified-synthetic | The Value has staged synthetic evidence and open design questions rather than verified live end-to-end behaviour. |
| `greed` | `partial` | `needs-reclassification` | `greed`: partially-implemented / staged / verified-synthetic | The Value has staged synthetic evidence and open design questions rather than verified live end-to-end behaviour. |
| `project-review` | `partial` | `needs-reclassification` | `project-review`: partially-implemented / active / verified-synthetic | Whole-Project review exists, but the active system still projects the historical Project Review name and Secondary Mechanism role instead of Perspective. |
| `component-governance` | `implemented` | `needs-reclassification` | `component-governance`: implemented / active / verified-live | The implementation has verified-live evidence; the active architecture must reclassify Component Governance as Infrastructure Extra. |
| `deterministic-over-probabilistic` | `implemented` | `needs-reclassification` | `deterministic-over-probabilistic`: implemented / active / verified-live | The implementation has verified-live evidence; the active architecture still calls it a Mechanism instead of a Principle. |
| `quantity-over-quality` | `partial` | `needs-reclassification` | `quantity-over-quality`: partially-implemented / staged / verified-synthetic | High-recall collection is staged and synthetic rather than verified live as a Principle across the target Step. |
| `subtraction-first` | `partial` | `needs-reclassification` | `subtraction-first`: partially-implemented / staged / verified-synthetic | The option order exists, but context-effect evidence and the target addition priority are not implemented. |
| `global-outcome-over-local-optimisation` | `partial` | `needs-reclassification` | `global-outcome-over-local-optimisation`: partially-implemented / staged / verified-synthetic | Local and Global Effect records exist, but the Principle remains staged and synthetic. |
| `work-signature` | `implemented` | `needs-reclassification` | `work-signature`: implemented / active / verified-live | The implementation has verified-live evidence; only its Blueprint role changes to Infrastructure. |
| `naming-system` | `implemented` | `needs-reclassification` | `naming-system`: implemented / active / verified-synthetic | The implementation is active with synthetic evidence; live end-to-end outcome proof remains limited. |
| `capability-check` | `implemented` | `needs-reclassification` | `capability-check`: implemented / active / verified-live | The implementation has verified-live evidence; only its Blueprint role changes to Infrastructure. |
| `hooks` | `implemented` | `needs-reclassification` | `hooks`: implemented / active / verified-live | The implementation has verified-live evidence; only its Blueprint role changes to Infrastructure. |
| `reality-check` | `partial` | `needs-role-redesign` | `reality-check`: implemented / staged / verified-synthetic | Reality Check is implemented as old Step 5. The target only classifies the core concept as Infrastructure and deliberately leaves its implementation open. |
| `experiment` | `implemented` | `needs-reclassification` | `experiment`: implemented / not-applicable / verified-synthetic | A reversible trial implementation exists with synthetic evidence; the active architecture must reclassify it from Mechanism to Infrastructure Extra. |
| `human-control` | `implemented` | `needs-reclassification` | `human-control`: implemented / staged / verified-staged | A staged Human Control view exists; it must stop reinforcing the old hierarchy before target alignment is complete. |
| `donor-quarantine` | `partial` | `needs-donor-specialisation` | `component-governance`: implemented / active / verified-live | General component quarantine exists, but donor capability intake and architecture-authority stripping are not explicit. |
| `donor-registry` | `partial` | `needs-donor-specialisation` | `component-governance`: implemented / active / verified-live<br>`capability-check`: implemented / active / verified-live | Component source and evidence are registered, but bounded donor capability, licence, hidden-authority and Blueprint mapping fields are missing. |
| `environment-adapters` | `partial` | `needs-blueprint-contract` | None | Platform adapters exist in source, but the donor-neutral environment boundary and Hermes adapter do not yet implement the target contract. |
| `cause-research` | `partial` | `needs-reclassification` | `cause-research`: partially-implemented / staged / verified-synthetic | The method has staged synthetic evidence and must be reclassified from Mechanism to Prop without owning a Step. |
| `two-sided-review` | `partial` | `needs-reclassification` | `two-sided-review`: partially-implemented / staged / verified-synthetic | The method has staged synthetic evidence and must be reclassified from Mechanism to Prop without owning the debate Step. |
| `steal` | `implemented` | `needs-blueprint-projection` | None | A complete validated dry run now covers baseline, research, donor intake, comparison, Blueprint Mapping, disposition and recovery; Steal is not registered or active. |
