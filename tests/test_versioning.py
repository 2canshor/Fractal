from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.improvement import TrialBoundary, TrialMeasurement
from fractal.storage import AuthorityError
from fractal.versioning import (
    VersionError,
    VersionStore,
    decide_node_map_change,
    propose_node_map_change,
    trial_node_map_change,
)


def verification(**overrides: bool) -> dict[str, bool]:
    value = {
        "clean_build": True,
        "tests_passed": True,
        "adapter_hashes_verified": True,
        "migrations_verified": True,
        "restore_verified": True,
    }
    value.update(overrides)
    return value


def component() -> dict:
    return {
        "component_id": "recording-core",
        "version": "1.0.0",
        "sha256": "a" * 64,
        "dependencies": [],
    }


def build(store: VersionStore, version: str) -> dict:
    return store.build_candidate(
        version=version,
        public_commit="a" * 40,
        private_commit="b" * 40,
        components=[component()],
        adapter_hashes={"codex": "c" * 64},
        migrations=["project-1.0-to-1.1"],
        restore_point={"kind": "manifest", "version": "previous"},
        verification=verification(),
    )


def test_candidate_requires_human_activation_rejection_and_restore(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    first = build(store, "0.1.0-alpha.1")
    assert first["status"] == "candidate"
    assert store.read_active() is None
    with pytest.raises(AuthorityError, match="primary user"):
        store.activate(
            "0.1.0-alpha.1",
            actor="main-agent",
            human_action=False,
        )
    store.activate(
        "0.1.0-alpha.1",
        actor="primary-user",
        human_action=True,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.1"

    build(store, "0.1.0-alpha.2")
    store.reject(
        "0.1.0-alpha.2",
        actor="primary-user",
        human_action=True,
    )
    assert store.version_state("0.1.0-alpha.2") == "rejected"
    assert store.read_active()["version"] == "0.1.0-alpha.1"
    with pytest.raises(VersionError, match="rejected"):
        store.activate(
            "0.1.0-alpha.2",
            actor="primary-user",
            human_action=True,
        )

    build(store, "0.1.0-alpha.3")
    store.activate(
        "0.1.0-alpha.3",
        actor="primary-user",
        human_action=True,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.3"
    store.restore(
        "0.1.0-alpha.1",
        actor="primary-user",
        human_action=True,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.1"
    assert store.version_state("0.1.0-alpha.3") == "previously-active"


def test_candidate_build_fails_closed_when_any_gate_is_missing(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    with pytest.raises(VersionError, match="Every build"):
        store.build_candidate(
            version="0.1.0-alpha.1",
            public_commit="a" * 40,
            private_commit="b" * 40,
            components=[component()],
            adapter_hashes={},
            migrations=[],
            restore_point={},
            verification=verification(restore_verified=False),
        )


def test_candidate_build_is_idempotent_but_version_content_is_immutable(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    first = build(store, "0.1.0-alpha.1")
    second = build(store, "0.1.0-alpha.1")
    assert second == first
    assert len(store.read_events()) == 1
    with pytest.raises(VersionError, match="different content"):
        store.build_candidate(
            version="0.1.0-alpha.1",
            public_commit="a" * 40,
            private_commit="b" * 40,
            components=[{**component(), "sha256": "d" * 64}],
            adapter_hashes={"codex": "c" * 64},
            migrations=["project-1.0-to-1.1"],
            restore_point={"kind": "manifest", "version": "previous"},
            verification=verification(),
        )


def test_manifest_and_active_pointer_integrity_fail_closed(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    build(store, "0.1.0-alpha.1")
    manifest_path = store.versions / "0.1.0-alpha.1.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["public_commit"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(VersionError, match="manifest integrity"):
        store.read_manifest("0.1.0-alpha.1")

    store = VersionStore(tmp_path / "pointer")
    build(store, "0.1.0-alpha.1")
    store.activate("0.1.0-alpha.1", actor="primary-user", human_action=True)
    pointer = json.loads(store.active_pointer.read_text())
    pointer["manifest_sha256"] = "0" * 64
    store.active_pointer.write_text(json.dumps(pointer))
    with pytest.raises(VersionError, match="pointer integrity"):
        store.read_active()


def safe_boundary() -> TrialBoundary:
    return TrialBoundary(*([True] * 12))


@pytest.mark.parametrize("change_type", ["add", "remove", "replace", "merge", "split"])
def test_node_map_changes_require_trial_decision_and_future_version(
    change_type: str,
) -> None:
    active = {"nodes": [{"id": "node-a", "method": "program-a"}]}
    candidate = {"nodes": [{"id": "node-a", "method": "program-b"}]}
    proposal = propose_node_map_change(
        change_type=change_type,
        target_ids=["node-a"],
        active_map=active,
        candidate_map=candidate,
        evidence_ids=["evidence-a"],
    )
    assert proposal["active"] is False
    trialled = trial_node_map_change(
        proposal,
        boundary=safe_boundary(),
        baseline=TrialMeasurement(True, 1.0, 10.0, 1000, 0.99, False),
        candidate=TrialMeasurement(True, 1.0, 8.0, 900, 0.99, False),
    )
    assert trialled["trial_status"] == "candidate-for-review"
    with pytest.raises(AuthorityError, match="primary user"):
        decide_node_map_change(
            trialled,
            decision="approve",
            actor="main-agent",
            human_action=False,
        )
    approved = decide_node_map_change(
        trialled,
        decision="approve",
        actor="primary-user",
        human_action=True,
    )
    assert approved["decision_status"] == "approved-for-version"
    assert approved["active"] is False
    assert approved["restore_map"] == active
