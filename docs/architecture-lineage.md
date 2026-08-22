# Fractal Architecture Lineage

Continuous Improvement is Fractal's sole **Core Philosophy**. It is carried by this hierarchy:

```text
Continuous Improvement
└── System Review                         Protagonist Mechanism
    ├── Five Steps                        Methodologies
    ├── Fatigue, Curiosity, Greed         Methodologies: Three Values
    ├── Project Review                    Secondary Mechanism
    └── operational Nodes                 Mechanisms
```

The operational Mechanisms include **Deterministic Over Probabilistic**, Quantity over Quality, Subtraction First, Global Outcome Over Local Optimisation, Work Signature, Naming System, Capability Check, Hooks, component governance, Cause Research, Two-Sided Review, experiments, and Human Control.

Fractal works at two connected time scales:

- **Project Review** protects the trajectory of the Project that is happening now.
- **System Review** learns from a completed Project and improves how future Projects are handled.

The **Original Fractal Backbone** preserves the reasoning sequence and the failure modes that caused Fractal to exist. Later Mechanisms implement, deepen, or support that backbone. A newer Node name does not replace the original reason.

## Project Review: protect the whole Project

Project Review exists because an agent can become absorbed in one local problem, make that part excellent, and leave the rest of the Project weak.

Its central question is:

> Are we over-investing attention, time, or resources in one local area at the expense of the Project as a whole?

The Project Plan is the reference. It records stages, priorities, scope, expected effort, resource allocation, and Review Points. A time or resource threshold opens a review; it does not automatically stop useful work.

Project Review has two trigger families:

- **Milestone:** a meaningful Project stage has finished.
- **Exception:** a meaningful deviation appears, such as excess effort, resource imbalance, repeated failure, Fatigue, a failed assumption, or a material change.

The trigger can be local. The review is always whole-Project. It checks Project Direction, Goal, Success Criteria, priorities, Project Plan, progress and evidence, risks and deviations, resources and deadline, and remaining work.

Project Review may create a new Plan version. Earlier Plan versions remain readable so later System Review can compare expectation with reality. A proposed Goal or Success Criteria change goes to Carson for a decision. A finding about Fractal itself is preserved as later System Review evidence.

## Project Completion: change the time scale

Carson's Project Completion decision closes the current-Project time scale and makes the completed evidence available to System Review. This creates the fractal relationship:

```text
Project Review
Keep this Project healthy
        ↓ after Project Completion
System Review
Make future Projects better by improving Fractal
```

## System Review: the original five-step backbone

The modern Nodes remain the working names. They are explicitly mapped to the original reasoning below.

### 1. Find Problems

**Original purpose:** review the entire Project and find as many potentially useful observations as possible before explaining them.

> **Quantity over Quality.**

This means high recall. Project Assessment asks both **What was better than before?** and **What became worse or remained difficult?** It records:

- **Positive Delta:** improvements, unexpectedly successful behaviour, reduced effort, better outcomes, and older problems that were solved;
- **Negative Delta:** failures, regressions, friction, avoidable cost, and unresolved problems.

Issue Scan then examines the whole Project history: work, decisions, corrections, reviews, evidence, failures, successes, strange behaviour, inefficiency, suspicions, unexpected difficulty, missed opportunities, unnecessary work, and apparently unrelated observations.

At this stage, noise and possible false positives are acceptable. Issue Scan does not prematurely deduplicate, force categories, assign a root cause, merge superficially similar observations, prioritise changes, or create one rule per observation.

> See the problems first. Explain them afterward.

**Implemented by:** Project Assessment → Positive Delta + Negative Delta → Issue Scan.

### 2. Find Local Patterns

**Original purpose:** turn the broad observation set into an honest account of why problems may have happened.

Project Patterns distinguishes:

- observation and symptom;
- proximate cause;
- plausible common cause;
- isolated incident;
- recurring local behaviour;
- counterexample and causal uncertainty.

Similarity alone is insufficient. Similar symptoms may have different causes, while different symptoms may express the same system behaviour. Several observations can therefore support one higher-level response instead of producing one new rule per observation.

**Implemented by:** Project Patterns and its causal interpretation.

### 3. Compare History

**Original purpose:** compare the current analysis with previous analysis and improvement history before treating a problem as new.

Cross-Project Patterns reads comparable Projects together with previous System Reviews, Change Proposals, hypotheses, System Versions, fixes, component history, and later outcomes. It looks for:

- **Recurrence:** the behaviour appears again;
- **Contradiction:** current and previous analyses disagree;
- **Overcorrection:** a response creates the opposite problem;
- **Patterns of patterns:** Fractal's improvement process repeatedly adds rules, adds agents, reverses direction, grows complexity, blames the same variable, or optimises the latest criticised metric.

Historical analysis is about reasoning and outcomes, not frequency alone.

Repeated directional reversal is a special warning. If the system moves `detailed → concise → detailed → concise`, Reversal Check asks:

> Are we solving the wrong problem?

The real variable may be question selection, timing, Project type, context quality, stop condition, sequencing, model behaviour, information value, or cognitive burden.

When the causal model may be wrong, Cause Research runs two independent directions:

- **External Research:** how credible outside sources understand this type of problem;
- **Internal Review:** why it happened specifically inside Fractal, based on actual local history and outcomes.

Reconciliation explains agreement and disagreement between them. Neither external practice nor local history automatically wins.

> Research globally. Diagnose locally. Intervene personally.

**Implemented by:** Cross-Project Patterns → Reversal Check → Cause Research (External Research + Internal Review) → Reconciliation.

### 4. Choose the System Response

**Original purpose:** decide what actually needs to change and whether the current system can already solve it.

Improvement Options investigates responses in this order:

1. Delete
2. Shorten
3. Merge
4. Simplify
5. Reconfigure or modify an existing component
6. Add a new component when the existing structure is genuinely insufficient
7. Keep the current system when the evidence supports No Change

This is **Subtraction First**. Complexity has a cost even when every addition has a historical justification. Repeated lack of demonstrated value can support removing a component, while low-frequency, high-value safeguards are evaluated against their real purpose and risk.

Repetition Awareness, Fatigue, Curiosity, Greed, experiments, and Capability evidence can contribute observations or options. Their findings enter Project Review or System Review instead of creating a competing improvement lifecycle.

Curiosity explores useful adjacent ideas and structurally meaningful cross-domain analogies. Repetition Awareness asks whether repeated work suggests abstraction, batching, automation, an upstream repair, or avoidable duplication. Repetition creates investigation pressure rather than a predetermined conclusion.

**Implemented by:** Improvement Options, Subtraction First, evidence from the Three Values, experiments, and Capability evidence.

### 5. Reality Check

**Original purpose:** step back from the feeling of progress and ask whether Fractal actually improved or merely changed.

The review asks:

- What is the real before-and-after difference?
- Did the overall system become better?
- Did one local metric improve while the whole Project or system became worse?
- Did the change add effort or complexity elsewhere?
- Was a similar change tried before, and what happened?
- Is this another overcorrection?
- Is the apparent improvement caused by something other than the hypothesis?
- Are we measuring a convenient proxy rather than the real Goal?

> **Hypothesis validated ≠ system improvement validated.**

Every serious change separates:

- **Expected Effect:** what should happen and over what time horizon;
- **Local Effect:** whether the intended direct effect occurred;
- **Global Effect:** whether Fractal became better across the whole system and future Projects.

```text
Local ✓ / Global ✓  Genuine improvement
Local ✓ / Global ✗  Harmful local optimisation
Local ✗ / Global ✓  Improvement occurred, but the causal explanation was wrong
Local ✗ / Global ✗  Failed intervention
```

The first assessment may produce a Change Proposal, Experiment, Need More Evidence, or No Change. Real Global Effect may require later Projects. Component History therefore preserves the believed problem, causal hypothesis, change, Expected Effect, Local Effect, expected and actual Global Effect, side effects, uncertainty, Project context, and later evaluation.

Two-Sided Review examines a consequential proposal. Final Assessment and Biggest Remaining Concern state the Main Agent's judgement and uncertainty. Your Decision records Carson's choice.

**Implemented by:** Expected Effect → Local Effect → Global Effect → Two-Sided Review when warranted → Final Assessment → Biggest Remaining Concern → Result → Your Decision → later outcome evaluation.

## Lineage rule for every major Node

Every major Node or mechanism declares:

1. which original requirement it implements or deepens;
2. the original failure mode it addresses;
3. later refinements it includes;
4. the modern Nodes that carry the original intention;
5. whether it is supporting infrastructure or a genuinely new, separately approved capability.

The registration, Hook, adapter, Tool, Plugin, MCP, Skill, and capability-management layers serve this Project Review and System Review product identity. They make the environment governable and verifiable; the five-step reasoning backbone determines how Fractal learns.
