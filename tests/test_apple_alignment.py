"""Acceptance tests for the complete Apple-principles registry boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from fractal.apple_alignment import (
    EXPECTED_PRINCIPLE_IDS,
    EXPECTED_REQUIREMENT_IDS,
    AppleAlignmentError,
    load_apple_principles_registry,
    validate_apple_principles_registry,
    validate_apple_version_acceptance,
    validate_responsibility_alignment,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _registry() -> dict[str, object]:
    return load_apple_principles_registry()


def _alignment(registry: dict[str, object]) -> dict[str, object]:
    principles = []
    for principle_id in EXPECTED_PRINCIPLE_IDS:
        record: dict[str, object] = {
            "principle_id": principle_id,
            "applicability": "universal",
            "triggered": True,
            "status": "passed",
            "evidence_ids": [f"evidence:principle:{principle_id}"],
        }
        if principle_id == "delight":
            record["human_qualitative_acceptance"] = True
            record["human_qualitative_evidence_ids"] = ["evidence:human:delight"]
        principles.append(record)
    requirements = [
        {
            "requirement_id": requirement_id,
            "applicability": "universal",
            "triggered": True,
            "status": "passed",
            "evidence_ids": [f"evidence:requirement:{requirement_id}"],
        }
        for requirement_id in EXPECTED_REQUIREMENT_IDS
    ]
    return {
        "record_type": "apple-responsibility-alignment",
        "record_version": 2,
        "responsibility_id": "responsibility:test-boundary",
        "continuous_improvement": {
            "is_core": True,
            "review_path": ["project-review", "system-review"],
            "status": "passed",
            "evidence_ids": ["evidence:continuous-improvement-path"],
        },
        "principle_alignment": principles,
        "requirement_alignment": requirements,
        "source_applicability": {
            "registry_manifest_sha256": registry["library"]["source_manifest_sha256"],
            "universal": {
                "selection": "all",
                "triggered": True,
                "status": "passed",
                "evidence_ids": ["evidence:all-universal-sources"],
            },
            "conditional": {
                "triggered_source_ids": [],
                "triggered_evidence": {},
                "not_triggered_selection": "all-except-triggered",
                "not_triggered_reason": (
                    "The responsibility does not introduce any conditional HIG subject."
                ),
            },
            "not_current": {
                "selection": "all",
                "triggered": False,
                "status": "not-applicable",
                "reason": "The current responsibility inventory has none of these subjects.",
                "evidence_ids": [],
            },
        },
    }


def _version_acceptance() -> dict[str, object]:
    value: dict[str, object] = {
        "record_type": "apple-system-version-acceptance",
        "record_version": 1,
        "apple_registry": {
            "source_count": 171,
            "source_manifest_sha256": (
                "40308dfd08e1c7ad3acdf05b659463b1984c69f7dbff96d2c752de0f169bec1c"
            ),
            "index_sha256": (
                "94b10ebc13cbb5dd7542487e8a232b315c191b6fc2e801f15cec5081c502d1d1"
            ),
        },
        "responsibilities": {
            "count": 20,
            "responsibility_ids": [f"RESP-{index:02d}" for index in range(20)],
            "deterministic_alignment_passed": True,
            "active_live_claimed": False,
        },
        "components": {
            "count": 503,
            "deterministic_audit_passed": True,
            "human_acceptance_was_pending_before_this_receipt": True,
            "audit_sha256": "a" * 64,
        },
        "user_surface": {
            "deterministic_audit_clean": True,
            "human_acceptance_was_pending_before_this_receipt": True,
            "audit_sha256": "b" * 64,
        },
        "continuous_improvement": {
            "core": "continuous-improvement",
            "sole_protagonist": "system-review",
            "parallel_lifecycle": False,
        },
        "human_delight": {
            "status": "accepted",
            "accepted_by": "primary-user",
            "scope": "exact-version-batch",
            "evidence_ids": ["primary-user-delight-acceptance"],
        },
        "authority_scope": {
            "project_id": "project-one",
            "project_revision": 4,
            "decision_batch_id": "batch-one",
        },
        "evidence_ids": ["apple-registry", "responsibility-audit", "surface-walkthrough"],
    }
    value["receipt_sha256"] = _digest(value)
    return value


def test_packaged_registry_passes_schema_and_deterministic_validation() -> None:
    registry = _registry()
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "fractal"
        / "schemas"
        / "apple-principles-registry.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        registry
    )
    assert validate_apple_principles_registry(registry) == registry
    assert len(registry["sources"]) == 171
    assert registry["library"]["applicability_counts"] == {
        "universal": 12,
        "conditional": 109,
        "not-current": 50,
    }
    assert registry["library"]["source_variant_counts"] == {
        "current-(2)": 143,
        "indexed-only": 28,
    }
    assert registry["library"]["index_sha256"] == (
        "94b10ebc13cbb5dd7542487e8a232b315c191b6fc2e801f15cec5081c502d1d1"
    )


def test_source_root_verification_checks_every_retained_hash(tmp_path: Path) -> None:
    registry = _registry()
    guide_root = Path.home() / "Documents" / "Guides" / "Apple"
    if not guide_root.is_dir():
        pytest.skip("retained Apple Guide library is not mounted")
    for source in registry["sources"]:
        relative = Path(*Path(source["source_label"]).parts[1:])
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(guide_root / relative, target)

    assert validate_apple_principles_registry(registry, source_root=tmp_path) == registry
    first = registry["sources"][0]
    relative = Path(*Path(first["source_label"]).parts[1:])
    (tmp_path / relative).write_text("changed", encoding="utf-8")
    with pytest.raises(AppleAlignmentError, match="source hash changed"):
        validate_apple_principles_registry(registry, source_root=tmp_path)


def test_source_root_verification_fails_when_retained_files_are_absent(tmp_path: Path) -> None:
    with pytest.raises(AppleAlignmentError, match="cannot read retained HIG source"):
        validate_apple_principles_registry(_registry(), source_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["sources"].pop(), "exactly 171"),
        (
            lambda value: value["sources"][0].__setitem__(
                "official_url", "https://example.com/not-apple"
            ),
            "unique and official",
        ),
        (lambda value: value["sources"][0].__setitem__("sha256", "bad"), "source SHA-256"),
        (
            lambda value: value["sources"][0].__setitem__("applicability", "conditional"),
            "applicability counts",
        ),
    ],
)
def test_registry_fails_closed_on_source_drift(mutation, message: str) -> None:
    registry = _registry()
    mutation(registry)
    with pytest.raises(AppleAlignmentError, match=message):
        validate_apple_principles_registry(registry)


def test_registry_fails_when_manifest_or_ai_core_reinforcement_changes() -> None:
    registry = _registry()
    registry["sources"][0]["title"] = "Changed title"
    with pytest.raises(AppleAlignmentError, match="manifest digest"):
        validate_apple_principles_registry(registry)

    registry = _registry()
    registry["cross_cutting_requirements"][0][
        "must_reinforce_continuous_improvement"
    ] = False
    with pytest.raises(AppleAlignmentError, match="Generative AI"):
        validate_apple_principles_registry(registry)


def test_complete_responsibility_alignment_passes_without_mutating_input() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    original = copy.deepcopy(alignment)

    assert validate_responsibility_alignment(alignment, registry) == alignment
    assert alignment == original


def test_alignment_requires_all_eight_principles_and_exact_ci_path() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    alignment["principle_alignment"].pop()
    with pytest.raises(AppleAlignmentError, match="all eight"):
        validate_responsibility_alignment(alignment, registry)

    alignment = _alignment(registry)
    alignment["continuous_improvement"]["review_path"].reverse()
    with pytest.raises(AppleAlignmentError, match="project-review then system-review"):
        validate_responsibility_alignment(alignment, registry)


def test_delight_requires_direct_human_qualitative_acceptance() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    delight = alignment["principle_alignment"][-1]
    delight["human_qualitative_acceptance"] = False

    with pytest.raises(AppleAlignmentError, match="human qualitative"):
        validate_responsibility_alignment(alignment, registry)


def test_staged_alignment_keeps_delight_pending_without_claiming_human_acceptance() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    delight = alignment["principle_alignment"][-1]
    delight["human_qualitative_acceptance"] = "pending"
    delight["human_qualitative_evidence_ids"] = []
    delight["observable_proxy_evidence_ids"] = ["evidence:delight-proxy"]

    assert (
        validate_responsibility_alignment(
            alignment,
            registry,
            require_human_qualitative_acceptance=False,
        )
        == alignment
    )
    with pytest.raises(AppleAlignmentError, match="human qualitative"):
        validate_responsibility_alignment(alignment, registry)


def test_conditional_trigger_false_requires_complete_grouped_selection() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    conditional = alignment["source_applicability"]["conditional"]
    conditional["not_triggered_selection"] = "some"

    with pytest.raises(AppleAlignmentError, match="all-except-triggered"):
        validate_responsibility_alignment(alignment, registry)


def test_triggered_conditional_requires_passed_direct_evidence() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    source_id = next(
        source["source_id"]
        for source in registry["sources"]
        if source["applicability"] == "conditional"
    )
    conditional = alignment["source_applicability"]["conditional"]
    conditional["triggered_source_ids"] = [source_id]
    conditional["triggered_evidence"] = {source_id: []}

    with pytest.raises(AppleAlignmentError, match="must contain direct evidence"):
        validate_responsibility_alignment(alignment, registry)


def test_alignment_cannot_rebind_or_misclassify_hig_source_groups() -> None:
    registry = _registry()
    alignment = _alignment(registry)
    alignment["source_applicability"]["registry_manifest_sha256"] = "0" * 64
    with pytest.raises(AppleAlignmentError, match="different HIG source manifest"):
        validate_responsibility_alignment(alignment, registry)

    alignment = _alignment(registry)
    alignment["source_applicability"]["conditional"]["triggered_source_ids"] = [
        "hig:privacy"
    ]
    alignment["source_applicability"]["conditional"]["triggered_evidence"] = {
        "hig:privacy": ["wrong-class"]
    }
    with pytest.raises(AppleAlignmentError, match="non-conditional"):
        validate_responsibility_alignment(alignment, registry)


def test_exact_primary_user_apple_version_acceptance_passes_and_binds_receipt() -> None:
    receipt = _version_acceptance()
    assert validate_apple_version_acceptance(receipt) == receipt


def test_apple_version_acceptance_rejects_pending_tampered_or_overclaimed_state() -> None:
    pending = _version_acceptance()
    pending["human_delight"]["status"] = "pending"
    pending["receipt_sha256"] = _digest(
        {key: value for key, value in pending.items() if key != "receipt_sha256"}
    )
    with pytest.raises(AppleAlignmentError, match="Human Delight"):
        validate_apple_version_acceptance(pending)

    overclaimed = _version_acceptance()
    overclaimed["responsibilities"]["active_live_claimed"] = True
    overclaimed["receipt_sha256"] = _digest(
        {key: value for key, value in overclaimed.items() if key != "receipt_sha256"}
    )
    with pytest.raises(AppleAlignmentError, match="overclaims live proof"):
        validate_apple_version_acceptance(overclaimed)

    tampered = _version_acceptance()
    tampered["authority_scope"]["project_revision"] = 5
    with pytest.raises(AppleAlignmentError, match="integrity"):
        validate_apple_version_acceptance(tampered)
