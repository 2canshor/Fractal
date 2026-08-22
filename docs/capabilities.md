# Capabilities

Fractal records three separate facts for every capability:

1. **Availability**: the source, package, connection, or installed element exists and matches its expected digest.
2. **Activation and Authority**: the current System Version and platform projection permit discovery or execution, within a stated authority boundary.
3. **Execution and Evidence**: a representative operation was observed at the claimed surface and produced typed evidence.

One dimension cannot stand in for another. A linked Skill can be available but inactive; a connected Tool can be active but never executed; a synthetic eval does not prove live external operation.

## Canonical Skill Source

The initial source lives under `capabilities/skills`. Each Skill uses concise discovery metadata, a bounded entrypoint, and on-demand references only where the modes differ materially. Packages and platform projections are derived from this source and compared file by file.

Legacy design selectors were merged into `Interface Design` because routing evals showed that direction, build, reference, and review are modes of the same user intent. Read-only web discovery and retrieval belong to `Research`; interactive or persistent external effects belong to separately authorised `Web Operations`.

No plugin family is created in the initial version. The canonical Skills do not need to be installed, disabled, or versioned as one bundle, and the routing eval supplies no evidence that a family would improve selection.

## Authority

A capability cannot activate itself. A Skill, Hook, Subagent, MCP server, Plugin, package, or adapter may propose a change, but persistent activation goes through a human-approved System Version. External delivery, submission, publication, notification, monitoring, payment, or a new recipient requires authority that matches that exact action.
