from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from fractal.models import ProjectRecord
from fractal.workplace import (
    LEGACY_WORKPLACE_FILENAME,
    WORKPLACE_FILENAME,
    WorkplaceMigrationError,
    WorkplaceVersionStateError,
    canonical_workplace_exists,
    create_workplace,
    ensure_workplace,
    load_workplace,
    logical_uri,
    migrate_legacy_workspace,
    resolve_logical_location,
    resolve_workplace_root,
    validate_workplace,
    workplace_exists,
)


def legacy_record(*, candidate: str | None = "0.1.0-alpha.2") -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "record_type": "fractal-workspace",
        "record_version": 1,
        "workspace_id": "primary",
        "system": {
            "repository": "2canshor/fractal",
            "active_version": "0.1.0-alpha.1",
            "candidate_version": candidate,
            "live_adapter_projection": "legacy-active",
        },
        "runtime": {"storage_class": "local-application-support", "committed": False},
    }


def test_fresh_bootstrap_is_neutral_and_does_not_speculate_a_tree(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    workplace = create_workplace(root)

    assert workplace.record_path == root / WORKPLACE_FILENAME
    assert sorted(path.name for path in root.iterdir()) == [WORKPLACE_FILENAME]
    payload = workplace.record_path.read_text(encoding="utf-8")
    assert "Carson" not in payload
    assert ("/" + "Users" + "/") not in payload
    assert "credentials" not in payload.lower()
    assert workplace.record["identity"] == {"kind": "neutral"}
    assert workplace.record["system"]["version_records"] == "workplace://system/versions"
    assert not workplace.project_root.exists()
    assert not workplace.runtime_root.exists()


def test_resolution_explicit_then_environment_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    configured = tmp_path / "configured"
    monkeypatch.setenv("FRACTAL_WORKPLACE", str(configured))
    assert resolve_workplace_root(explicit) == explicit
    assert resolve_workplace_root() == configured
    monkeypatch.delenv("FRACTAL_WORKPLACE")
    assert resolve_workplace_root() == (Path.home() / "Fractal Workplace").absolute()


def test_existence_detection_and_schema_load_readback(tmp_path: Path) -> None:
    root = tmp_path / "load"
    assert not workplace_exists(root)
    assert not canonical_workplace_exists(root)
    created = create_workplace(root)
    assert workplace_exists(root)
    assert canonical_workplace_exists(root)
    loaded = load_workplace(root)
    assert loaded.record == created.record
    schema = json.loads(
        (Path(__file__).parents[1] / "src/fractal/schemas/workplace.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(loaded.record)
    validate_workplace(loaded.record)


def test_fresh_bootstrap_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "failed"

    def fail(_path: Path, _content: bytes) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr("fractal.workplace._atomic_write", fail)
    with pytest.raises(OSError, match="injected"):
        create_workplace(root)
    assert not (root / WORKPLACE_FILENAME).exists()
    assert not root.exists() or not list(root.iterdir())


def test_legacy_migration_is_compatible_and_safe_twice(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    source = root / LEGACY_WORKPLACE_FILENAME
    root.mkdir()
    source.write_text(json.dumps(legacy_record()), encoding="utf-8")

    migrated = migrate_legacy_workspace(root)
    assert migrated.record["record_type"] == "fractal-workplace"
    assert not source.exists()
    assert migrated.read_version_record("0.1.0-alpha.1")["status"] == "active"
    assert migrated.read_version_record("0.1.0-alpha.2")["status"] == "candidate"
    assert (root / "system" / "active-version.json").exists()
    assert (root / "system" / "candidate-version.json").exists()
    second = ensure_workplace(root)
    third = migrate_legacy_workspace(root)
    assert second.record == migrated.record == third.record


def test_legacy_migration_keeps_source_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "legacy-failed"
    root.mkdir()
    source = root / LEGACY_WORKPLACE_FILENAME
    source.write_text(json.dumps(legacy_record()), encoding="utf-8")

    def fail(_path: Path, _content: bytes) -> None:
        raise OSError("injected migration failure")

    monkeypatch.setattr("fractal.workplace._atomic_write", fail)
    with pytest.raises(WorkplaceMigrationError, match="Legacy Workplace migration failed"):
        migrate_legacy_workspace(root)
    assert source.exists()
    assert not (root / WORKPLACE_FILENAME).exists()


def test_duplicate_legacy_candidate_is_collapsed_to_resolved_active(tmp_path: Path) -> None:
    root = tmp_path / "legacy-duplicate"
    root.mkdir()
    (root / LEGACY_WORKPLACE_FILENAME).write_text(
        json.dumps(legacy_record(candidate="0.1.0-alpha.1")), encoding="utf-8"
    )
    workplace = migrate_legacy_workspace(root)
    assert workplace.record["migration"]["candidate_duplicate_resolved"] is True
    assert not (root / "system" / "candidate-version.json").exists()
    assert workplace.read_version_record("0.1.0-alpha.1")["status"] == "active"


def test_project_layout_is_neutral_and_views_derive_from_status(tmp_path: Path) -> None:
    workplace = create_workplace(tmp_path / "projects")
    active = ProjectRecord(
        project_id="active-project", title="Active", system_version="0.1.0-alpha.1"
    )
    completed = ProjectRecord(
        project_id="done-project",
        title="Done",
        system_version="0.1.0-alpha.1",
        status="completed",
    )
    workplace.write_project(active)
    workplace.write_project(completed)
    assert workplace.project_record_path(active.project_id) == (
        workplace.root / "projects" / "active-project" / "record.json"
    )
    assert [item["project_id"] for item in workplace.active_projects()] == ["active-project"]
    assert [item["project_id"] for item in workplace.completed_projects()] == ["done-project"]
    assert not (workplace.root / "projects" / "active").exists()
    assert not (workplace.root / "projects" / "completed").exists()


def test_same_version_active_and_unresolved_candidate_is_rejected(tmp_path: Path) -> None:
    workplace = create_workplace(tmp_path / "versions")
    workplace.write_version_record(
        {
            "record_type": "system-version",
            "record_version": 1,
            "version": "0.1.0-alpha.1",
            "status": "candidate",
        }
    )
    workplace.set_version_pointer("active", "0.1.0-alpha.1")
    with pytest.raises(WorkplaceVersionStateError, match="active and an unresolved candidate"):
        workplace.set_version_pointer("candidate", "0.1.0-alpha.1")
    assert not (workplace.root / "system" / "candidate-version.json").exists()


def test_workplace_is_portable_to_a_second_root(tmp_path: Path) -> None:
    first = create_workplace(tmp_path / "first")
    second = create_workplace(tmp_path / "second")
    assert first.record == second.record
    first.write_project(
        ProjectRecord(project_id="first-project", title="First", system_version="0.1.0")
    )
    assert not second.project_record_path("first-project").exists()
    assert str(tmp_path / "first") not in second.record_path.read_text()


def test_logical_uri_resolution_and_runtime_ignored_semantics(tmp_path: Path) -> None:
    workplace = create_workplace(tmp_path / "logical")
    projects = resolve_logical_location(workplace.root, "workplace://projects")
    assert projects == workplace.project_root
    assert workplace.resolve("projects") == workplace.project_root
    assert workplace.resolve("workplace://projects/demo/record.json") == (
        workplace.root / "projects" / "demo" / "record.json"
    )
    assert logical_uri(workplace.root, projects) == "workplace://projects"
    with pytest.raises(ValueError):
        workplace.resolve("workplace://../outside")
    assert workplace.runtime_ignored
    assert not workplace.runtime_root.exists()
    workplace.ensure_runtime()
    assert workplace.runtime_root.is_dir()
    assert workplace.runtime_ignored
    assert (workplace.runtime_root / ".gitignore").read_text(encoding="utf-8") == (
        "*\n!.gitignore\n"
    )
    (workplace.runtime_root / "secret.txt").write_text("ephemeral", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(workplace.root)], check=True)
    ignored = subprocess.run(
        ["git", "-C", str(workplace.root), "check-ignore", "--quiet", ".runtime/secret.txt"],
        check=False,
    )
    not_ignored = subprocess.run(
        [
            "git",
            "-C",
            str(workplace.root),
            "check-ignore",
            "--quiet",
            ".runtime/.gitignore",
        ],
        check=False,
    )
    assert ignored.returncode == 0
    assert not_ignored.returncode != 0
