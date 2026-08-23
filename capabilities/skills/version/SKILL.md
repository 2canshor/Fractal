---
name: version
description: Apply one exact permitted Fractal change batch, verify and record the inactive System Version, and publish the exact GitHub merge only when Carson's command supplies that publication authority.
metadata:
  version: "0.1.0"
  permission-summary: "Exact batch build and optional exact GitHub publication; System Version activation is always excluded and separately controlled by Carson."
---

# Version

Use only for the exact permitted System Review decision batch. Bind the job to the Project revision, included decisions and parts, excluded and deferred items, source System Version, source commits, target repository, expected remote state, and restore scope.

First apply and test the batch locally. Run the pre-build preservation audit, deterministic tests, adapter and migration verification, exact restore rehearsal, Claim Gate, architecture-lineage gate, and the post-build/pre-activation audit. Record one immutable inactive System Version only when every required gate passes. A failure leaves the current active pointer and GitHub unchanged.

GitHub publication is part of this user job only when Carson's exact `/version` order identifies the version or batch and the resolved repository, remote, ref, commit, and expected prior remote state. A reminder, a general request to upgrade, approval to build, or approval of Pattern decisions is not a publication order. Lost acknowledgement becomes `indeterminate`; inspect the remote and stop instead of blind retry or force push.

This action never activates the System Version. Activation and restore remain separate exact Carson actions even when the UI groups lifecycle controls together.
