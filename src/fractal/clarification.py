"""Resolve-before-asking rules for bounded Project unknowns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class UnknownCandidate:
    """One uncertainty considered for research, assumption, or human clarification."""

    id: str
    summary: str
    material: bool
    locally_researchable: bool


@dataclass(frozen=True, slots=True)
class ClarificationDecision:
    """Deterministic routing result before any question is asked."""

    action: Literal[
        "resolve-with-evidence",
        "research-first",
        "proceed-with-explicit-assumption",
        "ask-primary-user",
    ]
    reason: str


def resolve_before_asking(
    candidate: UnknownCandidate,
    *,
    evidence_found: bool,
) -> ClarificationDecision:
    """Choose the least interruptive safe route for one unknown."""
    if evidence_found:
        return ClarificationDecision(
            "resolve-with-evidence",
            "Relevant local or primary-source evidence already resolves the unknown.",
        )
    if candidate.locally_researchable:
        return ClarificationDecision(
            "research-first",
            "The answer can still be discovered without shifting authority to the user.",
        )
    if not candidate.material:
        return ClarificationDecision(
            "proceed-with-explicit-assumption",
            "A reversible assumption will not materially change the outcome or authority.",
        )
    return ClarificationDecision(
        "ask-primary-user",
        "The unresolved choice is material and cannot be recovered from available evidence.",
    )
