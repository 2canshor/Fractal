---
name: version
description: Apply one exact permitted Fractal change batch, verify, record and activate the System Version, then publish its exact GitHub commit when Carson explicitly orders /version.
metadata:
  version: "0.2.0"
  permission-summary: "Exact batch build, activation and publication from Carson's explicit /version order; no wider lifecycle or external authority."
---

# Version

Use only for the exact permitted System Review decision batch. Bind the job to the Project revision, included decisions and parts, excluded and deferred items, source System Version, source commits, target repository, expected remote state, and restore scope.

First apply and test the batch locally. Run the pre-build preservation audit, deterministic tests, adapter and migration verification, exact restore rehearsal, Claim Gate, architecture-lineage gate, and the post-build/pre-activation audit. Require the exact Apple acceptance receipt that binds the 171-source registry, all persistent responsibilities, component and user-surface audits, the Project decision batch, and Carson's explicit qualitative Delight acceptance; tests or agent judgement must never mint that human acceptance. Record one immutable candidate only when every required gate passes. Prove the exact live adapter boundary, activate that exact manifest with a single-use primary-user authority receipt, rebuild mutable live state, and verify the active pointer plus one fresh session before claiming the System Version is active.

Carson's explicit `/version` order authorises this bounded job to resolve and bind the approved batch, repository, remote, ref, exact commit, and expected prior remote state. Publish only the activated commit and only with a separate single-use publication receipt. Use only `fractal version publish` with the exact order digest, the stored fresh-session and target-real Hook/executor route receipt IDs, Project identity and publication authority receipt; never pass caller-created receipt JSON and never call raw `git push`, `git send-pack`, `gh` ref mutation, or a low-level provider mutation Tool. The receipt IDs must resolve to immutable observations in the active `VersionStore` trusted runtime ledger and are consumed together before remote mutation. The Fractal Hook governs Fractal-owned Tool calls and does not claim to intercept Carson's external Terminal or UI. A reminder, a general request to upgrade, or approval of Pattern decisions is not a `/version` order.

The governed executor must confirm the active pointer and manifest, exact repository identity and local commit, and the expected remote ref before mutation. It must use an argv list with `shell=False`, prohibit force, inspect the exact remote ref after the push, append the canonical `publish-ack` event, and only then finish the single-use authority. A lost acknowledgement becomes `indeterminate`: use `fractal version publish --reconcile` to inspect and close only that exact already-authorised target. Never retry the push blindly.

Completion requires all four observable results: the permitted changes pass their gates, the immutable System Version is recorded, that exact version is active in a fresh session, and the bound GitHub ref contains the exact activated commit. If a late step fails, report the exact partial state and preserve the verified restore route; never describe a recorded candidate or a local commit as a completed `/version`.
