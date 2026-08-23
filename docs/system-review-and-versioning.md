# System Review and Versioning

A genuine System Review starts from a Project Completion action recorded by the primary user. Its modern Nodes implement the original five-step reasoning backbone.

## Original backbone and modern Nodes

| Original step | Original question | Modern implementation |
|---|---|---|
| 1. Find Problems | What can we learn from the whole Project before filtering or explaining it? | Project Assessment → Positive Delta + Negative Delta → Issue Scan |
| 2. Find Local Patterns | Why may these observations have happened, and which ones express the same behaviour? | Project Patterns with symptom/cause separation and uncertainty |
| 3. Compare History | What did Fractal previously believe, change, and observe afterward? | Cross-Project Patterns → Reversal Check → Cause Research → Reconciliation |
| 4. Choose the System Response | Can the current system solve this through deletion, simplification, reconfiguration, or modification? | Improvement Options + Subtraction First + supporting evidence |
| 5. Reality Check | Did Fractal actually improve, or did it merely change? | Expected Effect → Local Effect → Global Effect → review, result, decision, and later outcome evaluation |

The full reasoning and original failure modes are defined in [Architecture Lineage](architecture-lineage.md).

## Required stage order

1. Project Assessment
2. Issue Scan
3. Project Patterns
4. Cross-Project Patterns
5. Reversal Check
6. Cause Research
7. Reconciliation
8. Improvement Options
9. Expected Effect
10. Local Effect
11. Global Effect
12. Two-Sided Review or an explicit not-warranted result
13. Final Assessment by the Main Agent
14. Biggest Remaining Concern
15. Result: Change Proposal, Experiment, Need More Evidence, or No Change
16. Your Decision

The stage order is the operational route. The five original steps explain why the route exists.

## Step 1 contract: Quantity over Quality

Project Assessment records explicit Positive Delta and Negative Delta. Issue Scan then performs the original **Quantity over Quality** harvest across the complete Project history.

The observation set is preserved before causal synthesis. It can contain duplicates, suspicions, weak signals, successes, failures, friction, corrections, inefficiency, unexpected difficulty, missed opportunities, and unrelated-looking evidence. Confidence, grouping, causes, priority, and system responses belong to later stages.

## Step 2 contract: causal local patterns

Project Patterns links observations while keeping these concepts separate:

- symptom;
- proximate cause;
- plausible common cause;
- isolated incident;
- recurring local behaviour;
- counterexample;
- uncertainty.

A pattern is a supported interpretation of system behaviour, not a label produced by surface similarity.

## Step 3 contract: improvement history

Cross-Project Patterns compares comparable Projects and the analysis that followed them. Inputs include previous System Reviews, proposals, hypotheses, System Versions, fixes, component history, corrections, reversals, and later outcomes. A small comparison set is recorded as insufficient history.

The review checks recurrence, contradiction, overcorrection, and patterns in Fractal's own improvement behaviour. Reversal Check treats repeated directional change as a reason to challenge the problem representation:

> Are we solving the wrong problem?

Cause Research answers why the problem is happening. It is distinct from Two-Sided Review, which tests whether a proposal should be chosen.

External Research and Internal Review start from separate context manifests. Reconciliation records agreement, conflict, evidence weight, and unresolved questions. External practice and local history are both evidence.

## Step 4 contract: existing system before expansion

Improvement Options evaluates:

`delete → shorten → merge → simplify → reconfigure → modify → add → no-change`

The review identifies which existing components can already respond and what prevents them from doing so. Addition is considered when the existing structure is genuinely insufficient. Complexity delta, restore cost, component value history, and the purpose of low-frequency safeguards are part of the comparison.

Repetition Awareness, Fatigue, Greed, experiments, and Capability evidence can add observations, hypotheses, or bounded options to this stage. Whenever this stage needs to choose a solution, Curiosity is mandatory: 60% improves the current method, 20% checks dated findings in the same field, and 20% explores a related field with an explicit transferable relationship. Each route records an outcome, `No Finding` remains valid, and the findings never adopt themselves. A preferred `no-change` response instead records why no solution is needed. Missing 60/20/20 evidence stops Improvement Options rather than silently skipping Curiosity.

## Step 5 contract: real outcome over apparent progress

Expected Effect defines the baseline, observable signal, risk, and time horizon. Local Effect evaluates the intended direct result. Global Effect evaluates whole-system outcome, future Projects, human burden, capability, cost, and side effects.

**Hypothesis validated ≠ system improvement validated.** The outcome record represents all four Local/Global combinations, including a helpful result produced for a different reason than the original hypothesis.

Some Global Effect evidence arrives only after later Projects. Every persistent-change learning record therefore preserves:

- believed problem and context;
- causal hypothesis and uncertainty;
- before state and change;
- Expected Effect;
- Local Effect;
- expected and actual Global Effect;
- side effects;
- later evaluation and evidence.

## Independence and decision

When Two-Sided Review is warranted, Case For and Case Against begin from separate context manifests. A deterministic provenance check verifies branch ids, initial context hashes, input artefacts, and output artefacts. Passed candidates are ranked so the lightest capable source or debate agent is selected. Branches provide evidence and arguments; the Main Agent writes Final Assessment and Biggest Remaining Concern.

The result is Change Proposal, Experiment, Need More Evidence, or No Change. Your Decision records the primary user's typed response. Feedback Review records the strongest reasons supporting feedback, the strongest reasons challenging it, an updated Final Assessment, and an updated Biggest Remaining Concern before returning to Your Decision.

## Proposal, experiment, and later evidence

A Change Proposal contains the baseline, candidate, diff digest, effects, evidence, complexity delta, migration, and restore plan. An experiment runs baseline and candidate on the same representative work in a disposable environment with explicit budget and stop conditions. Experiment results remain linked to the original hypothesis and enter later Local/Global evaluation.

## System Version

A candidate manifest pins full Public and Private commit ids, component versions and hashes, adapter hashes, migrations, restore point, and five passing gates: clean build, tests, adapters, migrations, and restore.

Only the primary user can activate, reject, or restore. The active pointer is checked against the immutable manifest digest. Restore targets carry verified restore evidence and remain part of Component History.

## Node Implementation Map changes

Add, Remove, Replace, Merge, and Split follow the same proposal, isolated trial, primary-user decision, future System Version, and restore path. Every major Node records which original backbone requirement it implements or identifies itself as supporting infrastructure or a separately approved later capability.
