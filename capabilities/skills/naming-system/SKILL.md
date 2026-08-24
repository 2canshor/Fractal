---
name: naming-system
description: Classify and choose semantic names for systems, capabilities, concepts, actions, states, code identifiers, files, and UI labels, then run syntax checks. Use when creating, changing, reviewing, or migrating a controllable name.
metadata:
  version: "0.1.0"
  permission-summary: "Read references and propose names; established external names change only within the approved task scope."
---

# Naming System

Classify the named thing before choosing words: persistent System, Role, Tool, Capability, Concept, Class, File, or Module; triggered Action, Function, or Command; running State; completed State or Event.

Use nouns for persistent things and verbs for actions. Use a present participle for work in progress and an objective completed form for completed state. Separate the user-facing name from the technical identifier when their audiences require different syntax. A Boolean should read as a question. Natural meaning and hierarchy require human judgement; a score cannot replace them.

Before renaming, search all references and migrate source, tests, adapters, and documentation together. Record an exemption only where an external owner or protocol truly prevents a local change. Migration cost is not an exemption.

Run `scripts/check_name.py` for mechanically decidable casing and state-form checks. Treat a pass as syntax evidence only, not proof that the name is semantically natural.
