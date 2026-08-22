# Project Lifecycle and Human Control

Fractal treats a formal Project as a versioned state machine. Work evidence can move progress forward, but it cannot silently move the Goal, approve Success Criteria, or declare completion.

## Direction Confirmation

A formal Project shows four short summaries:

1. intended outcome;
2. deliverable;
3. completion standard;
4. exclusions.

They remain `provisional` until a typed primary-user action confirms them. A later material change requires a reason, a new direction version, and another confirmation. An identical confirmation is idempotent.

## Review

A `Review Point` opens for a checkpoint, risk, material deviation, failure, or human request. A `Project Review` records the hash of the whole canonical Project, its conclusion, confidence, Plan delta, evidence, and Biggest Remaining Concern. It closes open Review Points only after that whole-Project record exists.

## Plan History

Every Plan update records a reason, materiality, before and after hashes, and authority. Routine execution details may update inside approved scope. A material Goal, criteria, priority, scope, risk, or delivery change requires the primary user.

## Completion

Fractal can enter `awaiting_completion` only when:

- Success Criteria are approved;
- every criterion has canonical evidence;
- the post-work Success Criteria Challenge has one result for the approved criteria version.

The higher-target result does not erase original achievement. Only a typed action by the primary user can move the Project from `awaiting_completion` to `completed`.

## Resolve Before Asking

An unknown follows the least interruptive safe route:

1. use relevant evidence if it already resolves the issue;
2. research locally or through an approved primary source when possible;
3. make an explicit reversible assumption when the choice is not material;
4. ask the primary user only when the unresolved choice is material and not discoverable.
