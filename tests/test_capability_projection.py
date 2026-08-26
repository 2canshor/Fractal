"""Focused tests for the inactive canonical Action platform projection."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_capability_compiler import _inputs

from fractal import capability_projection as projection_module
from fractal.capability_compiler import compile_capability_graph, validate_candidate_graph
from fractal.capability_projection import (
    ProjectionBoundaryError,
    ProjectionPathError,
    ProjectionValidationError,
    audit_candidate_projection,
    project_candidate_graph,
)


@pytest.fixture
def candidate_graph() -> dict[str, object]:
    return compile_capability_graph(*_inputs())


def _project(
    graph: dict[str, object], root: Path, *, fallback: dict[str, object] | None = None
) -> dict[str, object]:
    return project_candidate_graph(graph, "codex", root, fallback=fallback)


def _skill_files(root: Path) -> list[Path]:
    return sorted((root / "skills").glob("*/SKILL.md"))


def test_synthetic_candidate_projection_is_staged_and_audited(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    root = tmp_path / "candidate"
    manifest = _project(candidate_graph, root, fallback={"active_surface": "legacy"})

    assert manifest["record_type"] == "capability-platform-projection"
    assert manifest["status"] == "staged-not-active"
    assert manifest["candidate_only"] is True
    assert manifest["install_authority"] is False
    assert manifest["activation_authority"] is False
    assert manifest["action_count"] == len(candidate_graph["actions"])
    assert manifest["audit"]["status"] == "passed"
    assert all(value is True for value in manifest["audit"]["checks"].values())
    assert len(_skill_files(root)) == len(candidate_graph["actions"])


def test_every_candidate_action_is_projected_once_and_workflows_resolve(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    root = tmp_path / "candidate"
    manifest = _project(candidate_graph, root)
    refs = [tuple(item["action_ref"].values()) for item in manifest["actions"]]
    assert len(refs) == len(set(refs)) == manifest["action_count"]
    assert manifest["audit"]["checks"]["candidate_actions_exactly_once"] is True
    assert manifest["audit"]["checks"]["workflow_refs_resolve"] is True

    raw_skill = _skill_files(root)[0].read_text()
    projection_line = "  fractal_projection: "
    projection_json = next(
        line[len(projection_line) :]
        for line in raw_skill.split("\n")
        if line.startswith(projection_line)
    )
    skill = json.loads(projection_json)
    assert skill["action_ref"] in [item["action_ref"] for item in manifest["actions"]]
    assert skill["workflow_refs"]
    assert "dot_refs" not in skill


def test_old_fallback_changes_recovery_only_not_candidate_digest_or_tree(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    first = _project(
        copy.deepcopy(candidate_graph),
        tmp_path / "first",
        fallback={"action_names": ["old-review"], "surface_digest": "a"},
    )
    second = _project(
        copy.deepcopy(candidate_graph),
        tmp_path / "second",
        fallback={"action_names": ["completely-different-old-name"], "surface_digest": "b"},
    )

    assert first["action_count"] == second["action_count"]
    assert first["candidate_digest"] == second["candidate_digest"]
    assert first["tree_sha256"] == second["tree_sha256"]
    assert first["manifest_digest"] == second["manifest_digest"]
    assert first["actions"] == second["actions"]
    assert first["recovery"]["surface"] != second["recovery"]["surface"]


def test_source_dot_and_provider_details_stay_hidden_from_transport(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    graph = copy.deepcopy(candidate_graph)
    graph["source_refs"] = [{"source_id": "secret-source", "provenance_refs": ["p"]}]
    graph["sources"] = graph["source_refs"]
    root = tmp_path / "candidate"
    manifest = _project(graph, root)
    transport = json.dumps(
        [
            manifest["actions"],
            *[path.read_text(encoding="utf-8") for path in _skill_files(root)],
        ],
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    assert "secret-source" not in transport
    assert "source_id" not in transport
    assert "dot_id" not in transport
    assert "implementation_id" not in transport
    assert '"provider"' not in transport
    assert '"provider_id"' not in transport


def test_same_human_name_alias_fails_the_one_word_action_contract(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    graph = copy.deepcopy(candidate_graph)
    first = graph["actions"][0]
    second = copy.deepcopy(first)
    second["action_id"] = "explain"
    second["human_intent"] = {
        "statement": "Explain a report for a different reader.",
        "familiar": first["human_name"],
        "stable": True,
    }
    second["induction_evidence"]["input_digest"] = "different-input"
    distinction = {"changed_output": True, "independent": True, "evidence_ids": ["distinct"]}
    first["induction_evidence"]["distinct_intent_evidence"] = distinction
    second["induction_evidence"]["distinct_intent_evidence"] = copy.deepcopy(distinction)
    first["name_reuse_evidence"] = {
        "changed_output": True,
        "independent": True,
        "evidence_ids": ["name-first"],
    }
    second["name_reuse_evidence"] = {
        "removed_old_output": True,
        "independent": True,
        "evidence_ids": ["name-second"],
    }
    graph["actions"] = [first, second]

    with pytest.raises(ProjectionValidationError, match="invalid Candidate Action"):
        _project(graph, tmp_path / "candidate")


def test_platform_names_and_hashes_are_deterministic_and_order_independent(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    first = _project(copy.deepcopy(candidate_graph), tmp_path / "first")
    reordered = copy.deepcopy(candidate_graph)
    reordered["actions"] = list(reversed(reordered["actions"]))
    reordered["workflows"] = list(reversed(reordered["workflows"]))
    reordered["dots"] = list(reversed(reordered["dots"]))
    second = _project(reordered, tmp_path / "second")

    assert first["candidate_digest"] == second["candidate_digest"]
    assert first["tree_sha256"] == second["tree_sha256"]
    assert first["manifest_digest"] == second["manifest_digest"]
    assert first["actions"] == second["actions"]


def test_empty_root_and_root_confinement_are_required(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep")
    with pytest.raises(ProjectionPathError, match="empty"):
        _project(candidate_graph, nonempty)

    target = tmp_path / "real"
    target.mkdir()
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(ProjectionPathError, match="symlink"):
        _project(candidate_graph, symlink)

    with pytest.raises(ProjectionPathError, match=".codex"):
        _project(candidate_graph, Path.home() / ".codex" / "candidate")


def test_candidate_lifecycle_and_activation_boundary_is_enforced(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    active = copy.deepcopy(candidate_graph)
    active["actions"][0]["activation"] = {"status": "active", "authorised": True}
    with pytest.raises(ProjectionBoundaryError, match="inactive activation"):
        _project(active, tmp_path / "active")

    active_workflow = copy.deepcopy(candidate_graph)
    active_workflow["workflows"][0]["lifecycle"]["status"] = "active"
    active_workflow["workflows"][0]["lifecycle"]["state"] = "active"
    with pytest.raises(ProjectionBoundaryError, match="candidate lifecycle"):
        _project(active_workflow, tmp_path / "active-workflow")


def test_trial_verified_staged_candidate_can_be_projected_without_becoming_active(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    staged = copy.deepcopy(candidate_graph)
    for dot in staged["dots"]:
        dot["verification"] = {"status": "verified-staged", "evidence_ids": ["trial-dot"]}
        dot["implementations"][0]["verification"] = {
            "status": "verified-staged",
            "evidence_ids": ["trial-implementation"],
        }
    for action in staged["actions"]:
        action["verification"] = {
            "status": "verified-staged",
            "evidence_ids": ["trial-action"],
        }
    for workflow in staged["workflows"]:
        workflow["verification"] = {
            "status": "verified-staged",
            "evidence_ids": ["trial-workflow"],
        }

    manifest = _project(staged, tmp_path / "staged")

    assert manifest["status"] == "staged-not-active"
    assert manifest["activation_authority"] is False
    assert manifest["audit"]["status"] == "passed"


def test_trial_evaluation_overlay_is_a_valid_candidate_graph_contract(
    candidate_graph: dict[str, object]
) -> None:
    staged = copy.deepcopy(candidate_graph)
    metrics = {
        "verified_implementations": 1,
        "unverified_implementations": 1,
        "covered_dots": 1,
        "covered_workflows": 1,
        "covered_actions": 1,
        "failures": 0,
        "failure_count": 0,
        "trial_count": 1,
    }
    staged["trial_metrics"] = copy.deepcopy(metrics)
    staged["evaluation_metrics"] = copy.deepcopy(metrics)
    staged["metrics"]["trial"] = copy.deepcopy(metrics)
    staged["trial_evaluation"] = {
        "record_type": "capability-trial-evaluation",
        "record_version": 1,
        "status": "verified-staged",
        "base_graph_input_digest": staged["input_digest"],
        "base_graph_content_digest": "a" * 64,
        "receipt_ids": ["trial-one"],
        "covered_implementation_refs": ["implementation-one@1.0.0"],
        "covered_dot_refs": ["dot-one@1.0.0"],
        "covered_workflow_refs": ["workflow-one@1.0.0"],
        "covered_action_refs": ["action-one@1.0.0"],
        "failures": [],
        "metrics": copy.deepcopy(metrics),
        "persistence_state_change": False,
        "activation": {"performed": False, "authorised": False, "active_surface": False},
        "publication": {"performed": False},
    }

    assert validate_candidate_graph(staged) == staged


def test_generated_skill_read_back_smoke_and_tamper_audit(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    root = tmp_path / "candidate"
    manifest = _project(candidate_graph, root)
    audit = audit_candidate_projection(candidate_graph, manifest, root)
    assert audit["status"] == "passed"
    assert audit["checks"]["generated_files_read_back"] is True
    assert audit["checks"]["smoke_validated"] is True

    skill = _skill_files(root)[0]
    skill.write_text(skill.read_text().replace("# ", "# Tampered ", 1), encoding="utf-8")
    with pytest.raises(ProjectionValidationError):
        audit_candidate_projection(candidate_graph, output_root=root)


def test_retained_manifest_replays_exact_generated_skill_bytes_without_temp_tree(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    manifest = _project(candidate_graph, tmp_path / "candidate")
    assert audit_candidate_projection(candidate_graph, manifest)["status"] == "passed"

    tampered = copy.deepcopy(manifest)
    tampered["files"][0]["sha256"] = "0" * 64
    tampered["tree_manifest"] = copy.deepcopy(tampered["files"])
    tampered["tree_sha256"] = projection_module._sha256(tampered["files"])
    tampered["manifest_digest"] = projection_module._manifest_digest(tampered)
    with pytest.raises(ProjectionValidationError, match="deterministic generated Skill bytes"):
        audit_candidate_projection(candidate_graph, tampered)


def test_raw_source_record_cannot_enter_candidate_graph(
    candidate_graph: dict[str, object], tmp_path: Path
) -> None:
    bad = copy.deepcopy(candidate_graph)
    bad["sources"] = [{"record_type": "capability-source", "source_id": "raw"}]
    manifest = _project(bad, tmp_path / "candidate")
    assert manifest["audit"]["checks"]["source_leak_free"] is True
    assert "raw" not in "".join(
        path.read_text(encoding="utf-8") for path in _skill_files(tmp_path / "candidate")
    )
