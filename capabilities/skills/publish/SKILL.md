---
name: publish
description: Deliver, deploy, release, send, or otherwise make an existing finished outcome available to its intended audience. Use only when the user requests the external delivery itself; not for merely creating an artifact or for Fractal /version publication.
metadata:
  version: "0.1.0"
  permission-summary: "Publish only the exact approved object to the exact destination and audience, with read-back and recovery; Fractal System Versions remain exclusive to /version."
---

# Publish

Bind the finished object, exact destination, audience or recipients, account, visibility, expected current state, and recovery route. Creating an artifact does not imply this Action; the user must request its delivery, deployment, release, or sending.

Read the generated `fractal/internal-workflow-map.json`. Select the narrowest `publish-*` workflow and load only the maintained internal methods required by that destination. Hosting, browser control, file conversion, connector access, and verification are reusable dots, not separate user-facing jobs.

Classify the route before using it: `exact` when a maintained workflow fits; `partial` when only part of a delivery method transfers; `missing` when no maintained workflow exists; or `unavailable` when the right workflow exists but a required dependency is not callable. Use an exact route after checking its dependencies. For a partial route, stop for confirmation whenever destination, audience, visibility, cost, or recovery could change. For a missing route, follow the Curiosity path below. For an unavailable route, report the real constraint and stop before external effect unless a separately authorised safe fallback exists; never claim the dependency worked.

Use read-before-write, preserve the exact prior state, perform the smallest scoped external change, and read back the destination before claiming success. If acknowledgement is lost, report `indeterminate` and inspect rather than retrying blindly. Never add recipients, widen visibility, spend money, or publish a different revision without authority.

Fractal source publication, System Version recording, and activation are not handled here. They require Carson's exact `/version` Command.

If no maintained destination workflow fits, use Curiosity to study the target, adjacent delivery methods, and a different reversible route. Test only a safe bounded candidate and stop for authority before any consequential external effect. Record the missing route for System Review; do not persist it silently.
