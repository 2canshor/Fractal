---
name: review
description: Review the object named in the user's request using the matching target-specific method, then return the review outcome and next decision or action.
metadata:
  version: "0.1.0"
  permission-summary: "Read the requested object and produce review findings; mutation or external action needs its own task authority."
---

# Review

Treat the user's object as part of the request: `review this document`, `review this code`, `review the design`, `review the active Project`, or `review the completed Project`. The user chooses the job and object; provider names and prerequisite tools stay internal.

Identify the object type and use its maintained review method. Project Review applies to an active formal Project at a milestone or exception. System Review applies only after Carson's Project Completion. Code, document, interface, legal, financial, and other specialist reviews retain their own evidence and acceptance rules inside this job.

Do not pretend that one review method fits every object. If no target-specific method exists but a materially different method may transfer—for example, only code-review instructions exist for a document—explain the proposed transfer in ordinary language and ask Carson before treating it as the method. If he agrees, obtain and validate a maintained method before claiming the review is complete.

Return findings against the object's real goal, evidence, risks, and completion standard. Keep read-only review separate from an instruction to repair, publish, send, approve, or activate anything.
