from __future__ import annotations

import json

from fractal.capability_integration import (
    compare_responsibilities,
    integrate_capabilities,
)
from fractal.capability_source import build_source


def source() -> dict:
    return build_source(
        name="Portable Genesis Source",
        source_type="skill",
        donor_id="portable-donor",
        locator="https://example.invalid/portable",
        commit="a" * 40,
        content_sha256="b" * 64,
        licence={"status": "verified", "spdx": "MIT", "evidence": "licence-file"},
        constraints={"content_reuse": "candidate-copy"},
        claimed_capabilities=["bounded validation"],
    )


def mapping(responsibility: str, **extra: object) -> dict:
    return {
        "responsibility": responsibility,
        "inputs": ["bounded input"],
        "outputs": ["auditable output"],
        "evidence_ids": [f"evidence-{responsibility[:4].lower()}"],
        **extra,
    }


def test_comparison_has_stable_signatures_and_evidence_for_all_relations() -> None:
    duplicate = compare_responsibilities(
        mapping("Validate one bounded input.", provider="provider-a"),
        mapping(" validate one bounded input! ", provider="provider-b"),
    )
    assert duplicate["relation"] == "duplicate"
    assert duplicate["left_signature"] == duplicate["right_signature"]
    assert duplicate["deterministic_evidence"]

    superset = compare_responsibilities(
        mapping("Validate one bounded input.", components=["validate"]),
        mapping(
            "Validate and audit one bounded input.",
            components=["validate", "audit"],
            semantic_evidence=["coverage-review"],
        ),
    )
    assert superset["relation"] == "superset"
    assert superset["semantic_evidence"]

    complementary = compare_responsibilities(
        mapping("Parse one bounded input.", mapping_id="parse"),
        mapping("Audit one bounded input.", mapping_id="audit"),
        relation="complementary",
        criteria=["ordered-boundary"],
        evidence=["complementary-proof"],
        semantic_evidence=["coherent-parse-audit"],
    )
    assert complementary["relation"] == "complementary"
    assert complementary["semantic_evidence"] == ["coherent-parse-audit"]

    conflicting = compare_responsibilities(
        mapping("Read one bounded input.", mapping_id="read"),
        mapping("Write one bounded input.", mapping_id="write"),
        relation="conflicting",
        criteria=["side-effect-boundary"],
        evidence=["conflict-proof"],
        semantic_evidence=["read-write-conflict"],
    )
    assert conflicting["relation"] == "conflicting"
    assert "conflict-remains-unresolved" in conflicting["uncertainty"]

    distinct = compare_responsibilities(
        mapping("Parse one bounded input.", provider="same-provider"),
        mapping("Audit one bounded input.", provider="same-provider"),
    )
    assert distinct["relation"] == "distinct"
    assert distinct["deterministic_evidence"]


def test_duplicate_collapses_across_providers_and_keeps_providers_below_dot() -> None:
    result = integrate_capabilities(
        [
            mapping("Validate one bounded input.", provider="provider-a"),
            mapping("validate one bounded input!", provider="provider-b"),
        ]
    )
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert "provider" not in candidate
    assert {item["provider"] for item in candidate["implementations"]} == {
        "provider-a",
        "provider-b",
    }


def test_superset_keeps_stronger_coverage_without_a_duplicate_dot() -> None:
    result = integrate_capabilities(
        [
            mapping("Validate one bounded input.", components=["validate"]),
            mapping(
                "Validate and audit one bounded input.",
                components=["validate", "audit"],
                semantic_evidence=["coverage-evidence"],
            ),
        ]
    )
    assert len(result["candidates"]) == 1
    assert "audit" in result["candidates"][0]["responsibility"]


def test_complementary_merges_only_with_one_coherent_sentence() -> None:
    result = integrate_capabilities(
        [
            mapping(
                "Parse one bounded input.",
                mapping_id="parse",
                relationship={
                    "relation": "complementary",
                    "target_id": "audit",
                    "criteria": ["ordered-boundary"],
                    "evidence": ["merge-proof"],
                    "semantic_evidence": ["coherent-proof"],
                    "merged_responsibility": "Parse and audit one bounded input.",
                },
            ),
            mapping(
                "Audit one bounded input.",
                mapping_id="audit",
                relationship={
                    "relation": "complementary",
                    "target_id": "parse",
                    "criteria": ["ordered-boundary"],
                    "evidence": ["merge-proof"],
                    "semantic_evidence": ["coherent-proof"],
                    "merged_responsibility": "Parse and audit one bounded input.",
                },
            ),
        ]
    )
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["responsibility"] == "Parse and audit one bounded input."

    blocked = integrate_capabilities(
        [
            mapping(
                "Parse one bounded input.",
                mapping_id="parse",
                relationship={
                    "relation": "complementary",
                    "target_id": "audit",
                    "criteria": ["ordered-boundary"],
                    "evidence": ["merge-proof"],
                    "semantic_evidence": ["semantic-proof"],
                },
            ),
            mapping("Audit one bounded input.", mapping_id="audit"),
        ]
    )
    assert len(blocked["candidates"]) == 2


def test_conflict_retains_alternatives_and_distinct_same_provider_is_not_collapsed() -> None:
    conflict = integrate_capabilities(
        [
            mapping(
                "Read one bounded input.",
                mapping_id="read",
                relationship={
                    "relation": "conflicting",
                    "target_id": "write",
                    "criteria": ["side-effect-boundary"],
                    "evidence": ["conflict-proof"],
                    "semantic_evidence": ["read-write-conflict"],
                },
            ),
            mapping(
                "Write one bounded input.",
                mapping_id="write",
                relationship={
                    "relation": "conflicting",
                    "target_id": "read",
                    "criteria": ["side-effect-boundary"],
                    "evidence": ["conflict-proof"],
                    "semantic_evidence": ["read-write-conflict"],
                },
            ),
        ]
    )
    assert len(conflict["candidates"]) == 2
    assert all(
        "conflict-remains-unresolved" in item["uncertainty"]
        for item in conflict["comparisons"]
    )

    distinct = integrate_capabilities(
        [
            mapping("Parse one bounded input.", provider="shared-provider"),
            mapping("Audit one bounded input.", provider="shared-provider"),
        ]
    )
    assert len(distinct["candidates"]) == 2


def test_explicit_responsibility_list_splits_overloaded_mapping_without_size_heuristics() -> None:
    result = integrate_capabilities(
        {
            "source": source(),
            "responsibility_evidence": [
                {"responsibility": "Parse one bounded input.", "evidence_ids": ["parse-proof"]},
                {"responsibility": "Audit one bounded input.", "evidence_ids": ["audit-proof"]},
            ],
        }
    )
    assert len(result["candidates"]) == 2
    assert {item["responsibility"] for item in result["candidates"]} == {
        "Parse one bounded input.",
        "Audit one bounded input.",
    }


def test_provenance_and_licence_survive_candidate_synthesis() -> None:
    result = integrate_capabilities(
        [mapping("Validate one bounded input.", source=source(), provider="portable-provider")]
    )
    candidate = result["candidates"][0]
    assert candidate["evidence"]["source_ids"]
    assert candidate["evidence"]["provenance"][0]["records"][0]["licence"]["spdx"] == "MIT"
    implementation = candidate["implementations"][0]
    assert implementation["provenance"]["licence"]["status"] == "verified"
    assert candidate["lineage"]["provenance"]

    separate_source = source()
    separate = integrate_capabilities(
        [separate_source],
        [
            mapping(
                "Validate one bounded input.",
                source_id=separate_source["source_id"],
                provider="portable-provider",
            )
        ],
    )
    assert (
        separate["candidates"][0]["evidence"]["provenance"][0]["records"][0]["licence"]["spdx"]
        == "MIT"
    )


def test_order_and_repeated_integration_are_idempotent_and_candidate_is_inactive() -> None:
    values = [
        mapping("Parse one bounded input.", provider="one"),
        mapping("Audit one bounded input.", provider="one"),
    ]
    first = integrate_capabilities(values)
    second = integrate_capabilities(list(reversed(values)))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(first, sort_keys=True) == json.dumps(
        integrate_capabilities(values), sort_keys=True
    )
    for candidate in first["candidates"]:
        assert not candidate["human_name"].startswith("Candidate ")
        assert candidate["human_name"] == candidate["responsibility"].rstrip(".!?")
        assert candidate["lifecycle"]["state"] == "candidate"
        assert candidate["activation"]["status"] == "inactive"
        assert candidate["activation"]["authorised"] is False


def test_broad_intake_materialises_only_identity_or_evidenced_pairs() -> None:
    values = [mapping(f"Validate bounded input number {index}.") for index in range(129)]
    values[0]["provider"] = "provider-a"
    values.append(mapping("Validate bounded input number 0.", provider="provider-b"))

    result = integrate_capabilities(values)

    assert result["comparison_scope"]["mode"] == "bounded-identity-and-evidence"
    assert result["comparison_scope"]["possible_pair_count"] == 8385
    assert result["comparison_scope"]["materialized_pair_count"] == 1
    assert len(result["candidates"]) == 129
    assert result["comparisons"][0]["relation"] == "duplicate"


def test_old_action_is_ignored_and_cannot_seed_a_candidate() -> None:
    result = integrate_capabilities(
        [
            {
                "record_type": "action",
                "record_version": 1,
                "action_id": "legacy",
                "name": "Old Action",
            }
        ]
    )
    assert result["candidates"] == []
    assert result["ignored"]


def test_intrinsic_provider_requires_evidence_and_is_the_only_dot_provider_scope() -> None:
    result = integrate_capabilities(
        [
            mapping(
                "Use a native bounded sandbox.",
                provider="native-runtime",
                provider_specific={
                    "provider_id": "native-runtime",
                    "intrinsic_provider_responsibility": {
                        "reason_code": "required-native-sandbox",
                        "evidence_ids": ["native-proof"],
                    },
                },
            )
        ]
    )
    candidate = result["candidates"][0]
    assert candidate["provider_specific"]["provider_id"] == "native-runtime"
    assert "provider" not in candidate
    assert candidate["implementations"][0]["provider"] == "native-runtime"


def test_extraction_provider_dependency_reaches_the_dot_boundary() -> None:
    result = integrate_capabilities(
        [
            {
                "responsibility": "Use GitHub review threads.",
                "source_refs": ["source-github"],
                "evidence_ids": ["github-review-thread-evidence"],
                "provider_dependency": {
                    "kind": "intrinsic",
                    "intrinsic": True,
                    "provider_id": "github",
                    "intrinsic_provider_responsibility": {
                        "reason": "Review-thread semantics belong to GitHub.",
                        "evidence_refs": ["github-review-thread-evidence"],
                    },
                },
            }
        ]
    )

    candidate = result["candidates"][0]
    assert candidate["provider_specific"]["provider_id"] == "github"
    assert candidate["implementations"][0]["provider"] == "github"


def test_intrinsic_provider_duplicates_keep_stable_comparison_endpoints() -> None:
    dependency = {
        "kind": "intrinsic",
        "intrinsic": True,
        "provider_id": "azure",
        "intrinsic_provider_responsibility": {
            "reason": "The responsibility names Azure Event Grid semantics.",
            "evidence_refs": ["azure-event-grid-evidence"],
        },
    }
    result = integrate_capabilities(
        [
            {
                "responsibility": "Use Azure Event Grid.",
                "source_refs": ["source-java"],
                "evidence_ids": ["azure-event-grid-evidence"],
                "provider_dependency": dependency,
            },
            {
                "responsibility": "Use Azure Event Grid.",
                "source_refs": ["source-python"],
                "evidence_ids": ["azure-event-grid-evidence"],
                "provider_dependency": dependency,
            },
        ]
    )

    assert len(result["candidates"]) == 1
    assert result["comparisons"][0]["relation"] == "duplicate"
