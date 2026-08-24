# Historical Architecture Lineage Coverage

> Historical evidence only. The canonical current workflow architecture is [Fractal Blueprint](blueprint.md). Names and step counts below describe the superseded pre-Blueprint design and must not be used for active routing.

This audit checks the follow-up correction against the current public architecture. It covers the current README, Revision 8 Implementation Plan, Product Introduction, Node Implementation Map, System Review design, and Project Review design. The legacy Architecture Draft is no longer a live canonical file; its retained requirement mapping is Revision 8 Appendix A.

Status describes the architecture after this correction. **Build impact** states whether the current runtime already enforces the requirement or needs a separately approved implementation change.

| Original Requirement | Current Implementation / Node | Status | Drift Risk | Build Impact |
|---|---|---|---|---|
| Preserve an Original Backbone and a Later Mechanisms layer | `architecture-lineage.md`; modern Node mapping | Strengthened | New Nodes could hide the reason they exist | Add lineage metadata and validators |
| Keep the original five System Review steps visible | Five canonical sections in `architecture-lineage.md` | Strengthened | Generic stage names compress the reasoning | Add backbone-step projection to review records and Human Control |
| Step 1 reviews the entire Project history | Project Assessment + Issue Scan | Strengthened | Reviewing only the final deliverable misses process evidence | Require whole-history manifest and coverage receipt |
| Preserve the exact phrase **Quantity over Quality** | Step 1 architecture and Product Introduction | Strengthened | “High recall” alone loses the deliberate trade-off | Require the Step 1 collection mode in schema and tests |
| Step 1 maximises recall | Issue Scan | Strengthened | Early confidence filters can hide useful evidence | Record broad observations before synthesis |
| Include major and small failures, friction, corrections, strange behaviour, inefficiency, suspicions, difficulty, missed opportunities, unnecessary work, and unrelated observations | Issue Scan coverage contract | Strengthened | Narrow bug scanning loses weak signals | Add an explicit coverage checklist |
| Accept noise and false positives during harvest | Issue Scan | Strengthened | Premature cleanup changes Step 1 into Step 2 | Preserve raw observations and uncertainty |
| Keep Step 1 separate from Step 2 | Issue Scan → Project Patterns | Strengthened | Deduplication, clustering, cause, priority, or fixes may happen too early | Reject causal/prioritised Issue Scan payloads |
| Ask what was better than before | Project Assessment | Strengthened | Failure-only learning grows defensive complexity | Require Positive Delta |
| Record Negative Delta | Project Assessment | Strengthened | Problems become implicit prose | Add a dedicated field |
| Record Positive Delta | Project Assessment | Strengthened | Successful behaviour is not preserved | Add a dedicated field |
| Make Issue Scan the high-recall harvest, not a generic bug list | Issue Scan | Strengthened | The name alone is ambiguous | Add purpose, scope, and stage-boundary checks |
| Step 2 asks why the observations happened | Project Patterns | Strengthened | Similarity grouping can masquerade as cause analysis | Require causal interpretation with uncertainty |
| Distinguish symptom, proximate cause, plausible common cause, isolated incident, and recurring local behaviour | Project Patterns | Strengthened | Plausible causes can be presented as confirmed | Add typed fields, confidence, counterexamples, and evidence |
| Allow different-looking symptoms to share a cause and similar symptoms to have different causes | Project Patterns | Strengthened | Surface clustering creates wrong patterns | Add positive and counterexample tests |
| Avoid one-problem-one-rule growth | Project Patterns → Improvement Options | Strengthened | Each observation can create a component | Require pattern-level response consideration |
| Step 3 compares previous Projects | Cross-Project Patterns | Fully Preserved | Comparability may be ignored | Keep explicit insufficient-history state |
| Compare previous System Reviews and improvement analyses | Cross-Project Patterns + review history | Strengthened | Project frequency alone loses reasoning history | Add review-history input manifest |
| Compare previous Change Proposals, hypotheses, System Versions, fixes, and outcomes | Cross-Project Patterns + Component History | Strengthened | Earlier interventions can disappear from context | Require proposal/version/outcome links |
| Ask what Fractal previously thought, changed, and observed afterward | Historical comparison | Strengthened | A recurring symptom can be treated as new | Add hypothesis-intervention-outcome chain |
| Detect recurrence | Cross-Project Patterns | Fully Preserved | Counting without context | Require comparable dimensions |
| Detect contradictions | Cross-Project Patterns | Strengthened | Conflicting analyses can silently overwrite one another | Record explicit contradiction links |
| Detect overcorrection | Reversal Check + Component History | Strengthened | Opposite changes may be treated as isolated | Link opposite direction, reason, and later outcome |
| Detect patterns of patterns in Fractal's own improvement behaviour | Cross-Project Patterns | Strengthened | Rule, Agent, and complexity growth can look individually justified | Add improvement-process pattern type |
| Treat global patterns as reasoning history, not frequency alone | Cross-Project Patterns | Strengthened | Counts can replace causal comparison | Require hypotheses, interventions, corrections, and outcomes |
| Treat repeated directional reversal as a wrong-dimension warning | Reversal Check | Strengthened | The system may search for a midpoint on the wrong variable | Add dimension-challenge result |
| Ask **Are we solving the wrong problem?** | Reversal Check | Strengthened | “Did we overcorrect?” is too narrow | Require a problem-representation challenge |
| Use Cause Research for a possibly wrong causal model | Cause Research | Strengthened | Another opposite policy may be proposed immediately | Add warrant and block options until reconciliation |
| Keep Cause Research separate from Two-Sided Review | Cause Research and Two-Sided Review | Strengthened | Causal diagnosis and proposal debate can contaminate each other | Separate state, inputs, and provenance checks |
| Run independent External Research and Internal Review | Cause Research branches | Fully Preserved | Branch leakage biases reconciliation | Existing independence evidence remains required |
| Treat external and internal findings as evidence rather than automatic authority | Reconciliation | Strengthened | “Best practice” or local habit can win by default | Require weighting and unresolved disagreement |
| Preserve **Research globally. Diagnose locally. Intervene personally.** | Cause Research architecture | Strengthened | Generic research wording loses application logic | Project this principle in review guidance |
| Step 3 purpose is historical comparison before calling a problem new | Cross-Project Patterns | Strengthened | A stage can exist without its original question | Add purpose to lineage metadata and tests |
| Step 4 asks whether the current agentic system can solve the problem | Improvement Options | Strengthened | Problem discovery can trigger automatic component creation | Require existing-capability assessment |
| Prefer adjusting the existing system before adding a new element | Improvement Options | Strengthened | “Add” may be easier to describe than “modify” | Require existing-component candidates |
| Evaluate delete, shorten, merge, simplify, reconfigure, modify, add, and no-change | Subtraction First | Strengthened | Current runtime omits an explicit reconfigure distinction | Extend option coverage and preserve no-change |
| Treat complexity as a cost | Improvement Options + Global Effect | Strengthened | Locally justified additions accumulate | Add complexity delta to effects |
| Let long-term lack of demonstrated value support removal | Capability Check + Component History | Strengthened | Components can persist forever without evidence | Add history window and value evidence |
| Preserve low-frequency, high-value safeguards | Capability evaluation | Strengthened | Trigger counts alone can remove safety value | Require purpose, severity, and counterfactual risk |
| Feed Repetition, Fatigue, Curiosity, Greed, experiments, and capability evidence into Step 4 | Supporting capability routing | Strengthened | Supporting signals can form a competing loop | Add explicit destination lineage and route tests |
| Curiosity explores adjacent or indirect ideas with structural similarity | Curiosity Value / Methodology | Strengthened | It may collapse into ordinary bug search | Add analogy hypothesis and bounded experiment output |
| Repetition Awareness creates investigation pressure | Fatigue / Work Signature evidence | Strengthened | Repetition may be treated as automatic waste | Preserve alternative explanations and review routing |
| Step 5 steps back from the feeling of progress | Reality Check | Strengthened | Proposal review may be mistaken for outcome review | Add a distinct later outcome-evaluation state |
| Ask whether Fractal improved or merely changed | Final Assessment + later evaluation | Strengthened | Passing a local test can close learning early | Require before/after and whole-system judgement |
| Compare current proposals with historical attempts | Reversal Check + Component History | Strengthened | Repeated interventions can lose lineage | Require prior-attempt links |
| Separate hypothesis validation from improvement validation | Local Effect + Global Effect | Strengthened | A correct local prediction can hide global harm | Add explicit causal conclusion status |
| Preserve all four Local/Global outcome combinations | Effect evaluation | Strengthened | Only success/failure may be represented | Add four outcome classifications and tests |
| Evaluate real outcomes in later Projects when needed | Component History + later System Reviews | Strengthened | Step 5 may stop at approval | Add pending/follow-up evaluation records |
| Preserve the full learning record for persistent changes | Change Proposal + Component History | Strengthened | Future Fractal cannot detect wrong causes or overcorrection | Add believed problem, cause, context, effects, side effects, uncertainty, and later evaluation |
| Map current Nodes explicitly to all five original steps | Architecture lineage and Node map | Strengthened | Modern names may replace conceptual lineage | Add machine-readable backbone mapping |
| Preserve Project Review's anti-local-optimisation failure mode | Project Review | Strengthened | “Project health” is too generic | Add original failure mode and resource-balance evidence |
| Use the Project Plan as Project Review's reference | Project Review + versioned Plan | Fully Preserved | Review can become free-form | Add planned-versus-actual resource comparison |
| Treat time limits as review thresholds rather than automatic stops | Review Point | Strengthened | Threshold automation can cut off justified work | Add threshold semantics and continuation decision |
| Support milestone and exception triggers | Review Point | Fully Preserved | Trigger reason may be lost | Keep typed family and evidence |
| Keep local trigger and whole-Project review | Project Review | Fully Preserved | Review can collapse into the triggering issue | Existing nine-dimension validation remains required |
| Allow versioned Plan updates and preserve history | Project Plan Update | Fully Preserved | Earlier planning assumptions may be erased | Existing before/after hashes remain required |
| Keep persistent Fractal changes in System Review | Project Review authority | Fully Preserved | Project-local urgency can bypass system review | Keep system observation as evidence only |
| Keep Goal and Success Criteria changes human-gated | Project Review authority | Fully Preserved | Plan updates can move the outcome | Existing typed decision path remains required |
| Use Project Completion to separate the two time scales | Lifecycle state machine | Fully Preserved | `awaiting_completion` can be mistaken for completion | Existing typed primary-user event remains required |
| Keep the fractal relationship visible | README, architecture lineage, Product Introduction | Strengthened | Infrastructure can eclipse product identity | Project it in Human Control and generated router docs |
| Keep supporting capabilities as evidence, triggers, research, experiments, or option support | Supporting route map | Strengthened | A Fatigue → Curiosity → Greed change loop can emerge | Add route constraints and tests |
| Keep Fractal's identity centred on current-Project protection and between-Project improvement | README + architecture lineage | Strengthened | Fractal can appear to be a registry or environment manager | Update product and Human Control language |
| Require every major Node to declare its original lineage or later-addition status | Node Implementation Map | Strengthened | Original reasons can disappear after renaming | Add required lineage schema and validation |
| Preserve reasons, failure modes, refinements, and modern implementers | Node lineage record | Strengthened | Names can survive while meaning drifts | Add these four mandatory fields |
| Audit Architecture, Revision 8, Product Introduction, Node map, System Review, and Project Review | This document | Fully Preserved | Later edits may break coverage | Add a CI coverage check against stable requirement ids |
| Show exact Step 1–5 evidence rather than similarly named Nodes | This table and architecture lineage | Strengthened | Presence can be mistaken for operation | Add runtime receipts and human-readable evidence per step |
| Make Positive Delta visible | Project Assessment and simulation | Strengthened | Success learning can remain implied | Add dedicated runtime field and display |
| Keep modern Node names while preserving original five-step lineage | All architecture documents | Fully Preserved | Renaming everything would lose modern operational clarity | Keep mapping rather than duplicate lifecycle stages |
| Demonstrate Milestone Review, Exception Review, Plan comparison, whole-Project zoom-out, and Plan update in the product simulation | `product-introduction.md` | Strengthened | Abstract description does not show behaviour | No runtime change by itself |
| Demonstrate the complete System Review reasoning progression in the product simulation | `product-introduction.md` | Strengthened | A short “noticed an improvement” story hides the backbone | No runtime change by itself |
| Treat this correction as architecture alignment before further Build | Candidate boundary and implementation impact list | Fully Preserved | Prior candidate may be mistaken for aligned runtime | Build a new candidate only after Carson's approval |
| Preserve the architecture lineage that defines Fractal | Architecture lineage, coverage audit, and build impact | Strengthened | A polished system can drift from the original design | Make lineage checks part of future candidate acceptance |

## Audit result

After the document correction, no requirement is architecturally classified as **Missing** or **Contradicted**. The current live candidate does not yet enforce the rows marked with a Build impact. Those items are proposed implementation work and require Carson's separate Build approval.
