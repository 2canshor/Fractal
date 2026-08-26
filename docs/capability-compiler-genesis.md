# Capability Compiler / Genesis

> **Candidate only — not active.** This document describes a detached Genesis candidate and its prepared review evidence. It does not change the active System Version, user surface, registry, configuration, Project record, or runtime. The existing active Action surface remains the fallback and recovery surface until a separately authorised future `/version`.

Genesis is a compiler for turning inspected capability evidence into a small, human-facing candidate surface. It is not a second Blueprint, a live source marketplace, or an automatic replacement of the current Skills. The candidate is useful because it makes the route from evidence to a possible human job explicit while keeping execution, persistence, activation, and publication separate.

## Two directions, one boundary

Genesis compiles bottom-up:

```text
Source → responsibility evidence → Candidate Dot → Candidate Workflow → Candidate Action
```

- A **Source** is a non-callable, provenance-bearing record. It can contribute inspected claims, but it cannot run, write Fractal state, or become a dependency by itself.
- A **Dot** is one bounded reusable responsibility with an input/output boundary. Provider identity, if needed for a future implementation choice, stays below the Dot boundary.
- A **Workflow** is an ordered composition of Dots that returns one bounded outcome. It is reusable only when the evidence supports the composition; a one-off path is not promoted as a reusable Workflow.
- An **Action** is the complete job a person names. It is induced from the candidate Workflows plus human-intent and naming evidence; it is not copied from an old Action name.

The human experience is resolved top-down after that compilation. A person starts with a familiar Action and the intended object or outcome. Fractal chooses the narrowest matching Workflow, then resolves its Dots and an eligible implementation. The person does not need to know the Source, donor, provider, package, or internal Dot name.

Genesis Workflows are not Blueprint Flows. A capability Workflow composes reusable responsibilities for one job outcome. A Blueprint Flow is one of the eight ordered System Review use rules owned by the sole `System Review` protagonist. A Workflow cannot create a ninth Flow, replace `Perspective`, or own the System Review lifecycle.

## Placement and authority

The candidate is split by responsibility:

| Boundary | Candidate placement | Authority |
| --- | --- | --- |
| Public System | capability contracts, schemas, compiler, runtime-resolution and migration code under `src/fractal/` | Defines validation and pure candidate operations; does not activate candidates |
| Workplace | `genesis/sources/`, `genesis/evidence/`, and `genesis/candidates/` | Holds candidate records, compact extraction evidence, trial receipts, provenance, digests, review preparation, and recovery evidence |
| Ephemeral runtime | `.runtime/genesis/` clones, downloads, indexes, extraction cache, and generated projection trees | Temporary working material only; not canonical state |
| Active System Version | Only records admitted by a future versioned cutover | The current active pointer and its verified manifest remain authoritative |

Raw Source content is not retained in the candidate graph. Source records remain non-callable and provider execution is not performed by compilation. Candidate records are kept in Workplace evidence; they are not silently mirrored into active runtime state.

## Candidate execution is not persistence

A future bounded candidate trial may receive an expiring, Project/task-local execution authority and emit one typed result. That is candidate execution evidence only. It does not grant registry-write, Workplace-write, canonical-state, activation, or publication authority. A successful trial does not imply that an Action, Workflow, Dot, or implementation has been persisted or promoted.

Runtime resolution also keeps route state separate from lifecycle state. A candidate record is `candidate`; a generated projection rehearsal is `staged-not-active`; only a future authorised cutover can produce `active`. For a user request, the resolver can report `exact`, `partial`, `missing-workflow`, `missing-capability`, or `unavailable`. These are honest resolution outcomes, not permission to improvise or silently activate a missing capability.

Provider identity is removed from a Dot when it is merely one implementation route. Direct provider semantics remain at a provider-specific Dot only when the responsibility itself names that provider's object or behaviour, with evidence; SDK and programming-language variants stay below that Dot as implementations. Provider identity is never part of the user-facing Action selector. A provider or implementation can be unavailable without making the human job disappear; the resolver reports the bounded state and the recovery route.

## Incremental evolution and safe replacement

New evidence enters through the same Source → Dot → Workflow → Action path. It may produce a new candidate or a bounded evolution of an existing candidate, but normal runtime does not scrape Sources, induce Actions, or rewrite the active surface. Old Actions are neither seeds nor naming targets, and old Workflows are not converted one-to-one into new Workflows. This is the incremental boundary: add only an evidenced responsibility and its coherent composition, then review the whole candidate against the Blueprint and human outcome.

The migration order is deliberately:

```text
extract → rebuild → test → switch → remove
```

The rehearsal covers the first three stages only. `switch` and `remove` remain blocked until a future exact `/version` explicitly authorises the permitted batch, the active candidate has representative execution and recovery proof, the active pointer reads back correctly, a fresh live turn verifies the surface, and the old fallback is still recoverable. This document is not that authority.

## Candidate snapshot evidence

The following is a fixed evidence snapshot, not a second source of active truth. The candidate graph, views, and metrics all report `candidate_only: true`; no active Action, command, configuration, adapter, or registry was changed.

| Metric | Snapshot |
| --- | ---: |
| Source references in the graph | 604 |
| Source decisions | 604 / 604 |
| Extracted responsibility findings | 613 |
| Legally reusable candidate contributions / blocked findings | 600 / 13 |
| Materialised comparisons | 77 (69 duplicate, 8 complementary) |
| Actual responsibility-record collapses | 39 |
| Candidate Dots | 561 |
| Provider-specific Dots with intrinsic evidence | 145 |
| Reusable Workflows | 3 |
| Candidate Actions | 3 |
| Candidate Dot implementations after procedure binding | 606 total; 6 verified-staged, 600 unverified |
| Source-level reusable paths / actual observed repetitions | 3 / 0 |
| Provider leakage | 0 |
| Unresolved conflicts | 0 |
| Routing fragments / raw historical locators promoted to Dots | 0 / 0 |
| Deterministic lookup estimate | 1,861 |
| Average Dots per Workflow | 2.666667 |
| Average responsibilities per Dot | 1.092692 |
| Average Workflows per Action | 1.0 |

Every Source receives a finding or `no-finding` decision. Routing fragments such as `Triggers:` and raw historical locators remain extraction evidence only. Direct provider semantics are classified before Dot synthesis, and obvious SDK/language variants share one provider-specific responsibility. The compiler stages are extraction (613), integration/comparison (77), Dot synthesis (561), Workflow synthesis (3), Action induction (3), and Action compression (3). Their recorded output digests are retained in the compilation record.

The three induced human Actions are:

| Action | Human outcome statement | Candidate state |
| --- | --- | --- |
| `build` | Develop software from a change request through a tested and verified result. | candidate, inactive, verified-staged |
| `fix` | Resolve software failures by diagnosing the cause, testing the fix, and verifying the result. | candidate, inactive, verified-staged |
| `find` | Build knowledge retrieval that returns a grounded answer. | candidate, inactive, verified-staged |

Each Action identity is one lowercase English verb. The complete job remains in the human-intent statement and Workflow; the slash is invocation syntax only. These are the exact current candidate Actions, not an active menu. No old Action record or old Action name was used as a seed.

## Prepared Section 83 assessment

The preparation record at `genesis/evidence/system-review-preparation.json` contains the Case For, Case Against, exact evidence digests, and the recommendation. The current provider-aware candidate presents three one-word Actions over three source-evidenced Workflows. Three bounded representative trials cover all three Actions, all three Workflows, six selected Dots, six selected implementations, and the exact `workplace://` procedure refs. Every receipt is bound to Project revision 155, an exact graph digest, an expiring task scope, zero external spend/network calls, and `persistence_state_change: false`.

This remains staged evidence, not a live-system conclusion. Six selected implementations are `verified-staged`; 600 donor-derived alternatives or standalone candidates remain unverified and cannot claim callability. Only six Workflow-selected Dots are eligible for simulated System admission; the other 555 remain Workplace candidates. The Codex projection remains staged-only, and no post-activation user turn, candidate active-pointer read-back, or restore after a real switch has occurred.

The base, procedure-bound, and trial-evaluated graphs pass the maintained Candidate Graph validator. The compiler input rebuilds the base graph exactly; trial evaluation is order-independent and a fixed point. The current projection and revision-155 cutover rehearsals bind the same evaluated candidate and retained fallback. These receipts make the staged chain independently reproducible; they still do not authorise switch, removal, activation, or publication.

The exact old surface remains the fallback/recovery boundary until the authorised version switch. The candidate rebuild exposes three Actions and four lifecycle Commands, while the earlier six Actions become routed internal capabilities instead of disappearing. The fallback bytes remain retained until post-switch verification proves recovery and removal are safe.

## Blueprint and active-document boundaries

Genesis is mapped to the existing Blueprint, not a new architecture. The rehearsal records 24 existing Elements, eight Flows, no new Element, `workflow_is_flow: false`, and preservation of the `continuous-improvement` core. The canonical definitions remain in the [Blueprint](blueprint.md), [System Review and versioning](system-review-and-versioning.md), [Actions and Commands](actions-and-commands.md), [Capabilities](capabilities.md), and [Platform adapters](platform-adapters.md). The current implementation gaps and their proof levels remain in the [Blueprint implementation gap](blueprint-implementation-gap.md).

The active Project is still incomplete and `in_progress`. Consequently, this is System Review preparation only: System Review is `prepared-not-open`, no Project Completion is declared, no decision is approved, and no `/version` authority is present.
