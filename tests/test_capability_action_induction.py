from __future__ import annotations

import copy
import json

import pytest

from fractal.capability_action_induction import (
    ActionInductionInputError,
    classify_incremental_action_fit,
    induce_candidate_actions,
)
from fractal.capability_workflow import build_workflow


def workflow(workflow_id: str, *, human_name: str | None = None) -> dict:
    return build_workflow(
        workflow_id=workflow_id,
        version="1.0.0",
        human_name=human_name or workflow_id.title(),
        match_contract={"intent": workflow_id},
        inputs=["request"],
        outputs=["result"],
        dot_refs=[
            {
                "sequence": 1,
                "dot_id": f"{workflow_id}-step",
                "version": "1.0.0",
                "lifecycle": "candidate",
            }
        ],
        success_contract={"outcome": "one bounded result"},
        side_effect_contract={"effects": ["none"]},
        recovery={"strategy": "restore the bounded attempt", "evidence_ids": ["recover"]},
        provenance={"evidence_refs": [f"workflow-evidence-{workflow_id}"]},
    )


def intent(workflow_id: str, stable: str, statement: str, familiar: str) -> dict:
    return {
        "workflow_id": workflow_id,
        "version": "1.0.0",
        "stable_id": stable,
        "statement": statement,
        "familiar": familiar,
        "evidence_ids": [f"intent-{workflow_id}"],
    }


def naming(*names: tuple[str, str]) -> dict:
    return {
        "proposals": {stable: [name] for stable, name in names},
        "rationale": {stable: "Use the familiar human outcome." for stable, _ in names},
        "alternatives": {stable: [] for stable, _ in names},
        "language": "en",
        "part_of_speech": "verb",
        "provenance": {
            "component_id": "naming-system",
            "version": "0.1.0",
            "evidence_ids": ["naming-system-evaluation"],
        },
    }


def induce(workflows: list[dict], evidence: list[dict], names: dict, **options: object) -> dict:
    return induce_candidate_actions(workflows, evidence, names, **options)


def test_bottom_up_output_is_natural_and_not_old_vocabulary() -> None:
    records = [workflow("summarise"), workflow("compare")]
    evidence = [
        intent("summarise", "review", "Review the result for the person.", "review"),
        intent("compare", "compare", "Compare two results for the person.", "compare"),
    ]
    result = induce(records, evidence, naming(("review", "review"), ("compare", "compare")))
    rendered = json.dumps(result["candidates"], ensure_ascii=False).casefold()

    assert {item["action_id"] for item in result["candidates"]} == {"review", "compare"}
    assert all(item["lifecycle"]["status"] == "candidate" for item in result["candidates"])
    assert all(item["activation"]["status"] == "inactive" for item in result["candidates"])
    assert not any(token in rendered for token in ("source", "dot_group", "provider", "skill"))


def test_legacy_fixture_is_excluded_and_anti_seed_is_order_independent() -> None:
    records = [workflow("review-workflow")]
    evidence = [intent("review-workflow", "review", "Review the result.", "review")]
    names = naming(("review", "review"))
    first = induce(
        records,
        evidence,
        names,
        legacy_fixture=[
            {"action_id": "old-review", "human_name": "Old Review", "status": "active"}
        ],
    )
    second = induce(
        records,
        evidence,
        names,
        legacy_fixture=[{"action_id": "renamed-old", "human_name": "Renamed", "status": "retired"}],
    )

    assert first["input_digest"] == second["input_digest"]
    assert first["candidates"] == second["candidates"]
    assert first["anti_seed_attestation"]["no_legacy_action"] is True
    assert first["legacy_removal_audit"][0]["status"] == "removed"


def test_review_reinduction_appears_only_when_its_workflow_and_evidence_exist() -> None:
    review = workflow("review-workflow")
    teach = workflow("teach-workflow")
    records = [review, teach]
    evidence = [
        intent("review-workflow", "review", "Review the result.", "review"),
        intent("teach-workflow", "teach", "Explain the result for learning.", "teach"),
    ]
    result = induce(records, evidence, naming(("review", "review"), ("teach", "teach")))
    assert any(item["action_id"] == "review" for item in result["candidates"])
    review_action = next(item for item in result["candidates"] if item["action_id"] == "review")
    assert review_action["workflow_refs"][0]["workflow_id"] == "review-workflow"

    without_review = induce([teach], [evidence[1]], naming(("teach", "teach")))
    assert all(item["action_id"] != "review" for item in without_review["candidates"])


def test_compression_merges_same_stable_intent_but_keeps_distinct_intents() -> None:
    records = [workflow("review-one"), workflow("review-two"), workflow("teach-one")]
    evidence = [
        intent("review-one", "review", "Review the result.", "review"),
        intent("review-two", "review", "Review the result.", "review"),
        intent("teach-one", "teach", "Explain the result for learning.", "teach"),
    ]
    result = induce(records, evidence, naming(("review", "review"), ("teach", "teach")))

    assert len(result["candidates"]) == 2
    review = next(item for item in result["candidates"] if item["action_id"] == "review")
    assert {item["workflow_id"] for item in review["workflow_refs"]} == {"review-one", "review-two"}
    assert len(result["candidates"]) != 1
    assert review["induction_evidence"]["compression_decision"]["meaning_preservation_evidence"]


def test_leakage_duplicate_shared_workflow_and_order_are_rejected_or_stable() -> None:
    records = [workflow("one"), workflow("two")]
    evidence = [
        intent("one", "review", "Review the result.", "review"),
        intent("two", "review", "Review the result.", "review"),
    ]
    names = naming(("review", "review"))
    first = induce(records, evidence, names)
    second = induce(list(reversed(records)), list(reversed(evidence)), copy.deepcopy(names))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    with pytest.raises(ActionInductionInputError):
        induce({"record_type": "capability-source", "source_id": "source"}, evidence, names)
    with pytest.raises(ActionInductionInputError):
        induce(records, [{**evidence[0], "provider": "untrusted"}, evidence[1]], names)

    shared = [
        intent("one", "review", "Review the result.", "review"),
        {
            **intent("one", "teach", "Explain the result.", "teach"),
            "distinct_intent_evidence": None,
        },
    ]
    with pytest.raises(ActionInductionInputError, match="multiple intents"):
        induce([records[0]], shared, naming(("review", "review"), ("teach", "teach")))

    duplicate = [records[0], copy.deepcopy(records[0])]
    duplicate[1]["human_name"] = "Different"
    with pytest.raises(ActionInductionInputError):
        induce(duplicate, [evidence[0]], names)


def test_naming_evidence_is_exact_per_stable_intent_and_has_no_fallback() -> None:
    records = [workflow("review-workflow")]
    evidence = [intent("review-workflow", "review", "Review the result.", "review")]

    empty_proposal = naming(("review", "review"))
    empty_proposal["proposals"]["review"] = []
    with pytest.raises(ActionInductionInputError, match="nonblank proposal"):
        induce(records, evidence, empty_proposal)

    unrelated = naming(("teach", "teach"))
    with pytest.raises(ActionInductionInputError, match="every exact stable intent"):
        induce(records, evidence, unrelated)

    mismatched = naming(("review", "inspect"))
    with pytest.raises(ActionInductionInputError, match="familiar name"):
        induce(records, evidence, mismatched)


@pytest.mark.parametrize("proposal", ["Review", "review-result", "review result", "/review"])
def test_induction_rejects_any_non_lowercase_one_word_action_name(proposal: str) -> None:
    records = [workflow("review-workflow")]
    evidence = [intent("review-workflow", "review", "Review the result.", proposal)]
    names = naming(("review", proposal))

    with pytest.raises(ActionInductionInputError, match="lowercase English one-word verb"):
        induce(records, evidence, names)


def test_naming_system_provenance_is_required_and_retained() -> None:
    records = [workflow("review-workflow")]
    evidence = [intent("review-workflow", "review", "Review the result.", "review")]
    names = naming(("review", "review"))
    missing = copy.deepcopy(names)
    del missing["provenance"]

    with pytest.raises(ActionInductionInputError, match="provenance"):
        induce(records, evidence, missing)

    result = induce(records, evidence, names)
    naming_evidence = result["candidates"][0]["induction_evidence"]["naming_system"]
    assert naming_evidence["language"] == "en"
    assert naming_evidence["part_of_speech"] == "verb"
    assert naming_evidence["provenance"] == names["provenance"]


def test_unrelated_intents_cannot_silently_share_or_suffix_one_verb() -> None:
    records = [workflow("audit"), workflow("explain")]
    evidence = [
        intent("audit", "audit-result", "Audit one result.", "review"),
        intent("explain", "teach-result", "Teach one result.", "review"),
    ]
    names = naming(("audit-result", "review"), ("teach-result", "review"))

    with pytest.raises(ActionInductionInputError, match="compete for Action verb"):
        induce(records, evidence, names)


def test_incremental_workflow_fit_is_explicit_and_never_silent() -> None:
    original_workflow = workflow("review-original")
    original = induce(
        [original_workflow],
        [intent("review-original", "review-result", "Review a result.", "review")],
        naming(("review-result", "review")),
    )["candidates"][0]

    related = workflow("review-related")
    related_fit = classify_incremental_action_fit(
        related,
        [original],
        [
            {
                **intent(
                    "review-related",
                    "review-result",
                    "Review a related result.",
                    "review",
                ),
                "same_intent": True,
            }
        ],
    )
    assert related_fit["classification"] == "fit-existing"
    assert related_fit["action_ref"] == {"action_id": "review", "version": "1.0.0"}
    assert related_fit["mutation"] is False

    competing = classify_incremental_action_fit(
        workflow("teach-competing"),
        [original],
        [
            intent(
                "teach-competing",
                "teach-result",
                "Teach an unrelated result.",
                "review",
            )
        ],
    )
    assert competing["classification"] == "ambiguous"
    assert competing["action_refs"] == [{"action_id": "review", "version": "1.0.0"}]

    novel = classify_incremental_action_fit(
        workflow("teach-new"),
        [original],
        [intent("teach-new", "teach-result", "Teach a result.", "teach")],
    )
    assert novel["classification"] == "candidate-new-action"
    assert novel["persistent"] is False
