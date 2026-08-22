from __future__ import annotations

import pytest

from fractal.clarification import UnknownCandidate, resolve_before_asking


@pytest.mark.parametrize(
    ("candidate", "evidence_found", "expected"),
    [
        (
            UnknownCandidate("known", "Known locally", True, True),
            True,
            "resolve-with-evidence",
        ),
        (
            UnknownCandidate("research", "Needs local research", True, True),
            False,
            "research-first",
        ),
        (
            UnknownCandidate("minor", "Safe reversible detail", False, False),
            False,
            "proceed-with-explicit-assumption",
        ),
        (
            UnknownCandidate("material", "Changes the outcome", True, False),
            False,
            "ask-primary-user",
        ),
    ],
)
def test_resolve_before_asking_routes_only_material_gap_to_user(
    candidate: UnknownCandidate,
    evidence_found: bool,
    expected: str,
) -> None:
    assert resolve_before_asking(candidate, evidence_found=evidence_found).action == expected
