---
name: legacy-material-review
description: Decide whether a legacy rule, Skill, asset, workflow, or record should be selectively rebuilt, merged, rejected, or retained as reference. Use during a controlled replacement; do not bulk-import material for possible future use.
metadata:
  version: "0.1.0"
  permission-summary: "Read selected legacy material; removal waits for replacement evidence and the approved cutover sequence."
---

# Legacy Material Review

Start from the inventory, dependency graph, and required replacement test. Read metadata and references first; open full legacy content only when a specific extraction question requires it.

For every item, record its digest and provenance, the small meaning worth considering, current accuracy, architecture fit, conflicts, unknowns, disposition, new destination, replacement evidence, and removal dependency. The allowed dispositions are `rebuild`, `merge`, `reject`, and `retain-reference`.

Follow `extract -> rebuild -> test -> switch -> remove`. An installed link, package, old global status, or historical use is not evidence that the new capability works. Do not preserve the original content in the manifest. Keep a live dependency until its replacement passes at the user's actual surface.
