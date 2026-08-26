from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fractal import SYSTEM_VERSION
from fractal.cli import main
from fractal.models import ProjectRecord
from fractal.storage import value_sha256
from fractal.workplace import create_workplace


def _legacy_record() -> dict[str, object]:
    return {
        "record_type": "fractal-workspace",
        "record_version": 1,
        "workspace_id": "primary",
        "system": {
            "repository": "2canshor/fractal",
            "active_version": "0.1.0-alpha.1",
            "candidate_version": "0.1.0-alpha.2",
        },
        "runtime": {"storage_class": "local-application-support", "committed": False},
    }


def _healthy_workplace(root: Path) -> None:
    workplace = create_workplace(root)
    workplace.write_version_record(
        {
            "record_type": "system-version",
            "record_version": 1,
            "version": SYSTEM_VERSION,
            "status": "active",
        }
    )
    workplace.set_version_pointer("active", SYSTEM_VERSION)
    workplace.write_project(
        ProjectRecord(
            project_id="cli-project",
            title="CLI Project",
            system_version=SYSTEM_VERSION,
        )
    )
    workplace.ensure_runtime()
    (workplace.runtime_root / "live-state").mkdir(parents=True)
    (workplace.runtime_root / "live-state" / "current.json").write_text(
        json.dumps(
            {
                "record_type": "live-runtime-state",
                "record_version": 1,
                "project": {"project_id": "cli-project", "revision": 0},
                "system_version": {"version": SYSTEM_VERSION, "status": "active"},
            }
        ),
        encoding="utf-8",
    )


def _verified_migration_inputs(tmp_path: Path) -> dict[str, Path]:
    runtime_root = tmp_path / "external-runtime"
    version_root = runtime_root / "system-version"
    versions_root = version_root / "versions"
    versions_root.mkdir(parents=True)
    version = SYSTEM_VERSION
    manifest = {
        "record_type": "system-version-manifest",
        "record_version": 1,
        "version": version,
        "status": "active",
    }
    manifest["manifest_sha256"] = value_sha256(manifest)
    manifest_path = versions_root / f"{version}.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pointer = {
        "record_type": "active-system-version",
        "record_version": 1,
        "version": version,
        "status": "active",
        "activated_at": "2026-01-01T00:00:00Z",
        "activated_by": "primary-user",
        "manifest_sha256": manifest["manifest_sha256"],
    }
    pointer_path = version_root / "active.json"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    pointer_digest = hashlib.sha256(pointer_path.read_bytes()).hexdigest()
    live_state = {
        "record_type": "live-runtime-state",
        "record_version": 1,
        "system_version": {
            "version": version,
            "status": "active",
            "activated_at": pointer["activated_at"],
            "activated_by": pointer["activated_by"],
            "manifest_sha256": pointer["manifest_sha256"],
            "source_path": str(pointer_path),
            "source_sha256": pointer_digest,
        },
    }
    live_state["state_sha256"] = value_sha256(live_state)
    live_path = runtime_root / "live-state" / "current.json"
    live_path.parent.mkdir(parents=True)
    live_path.write_text(json.dumps(live_state), encoding="utf-8")
    event_root = tmp_path / "events"
    event_root.mkdir()
    return {
        "runtime_root": runtime_root,
        "event_root": event_root,
        "active_pointer": pointer_path,
        "live_state": live_path,
    }


def test_status_first_run_bootstraps_neutral_workplace(tmp_path: Path, capsys) -> None:
    root = tmp_path / "fresh"

    assert main(["status", "--root", str(root)]) == 0
    output = capsys.readouterr().out

    assert (root / "workplace.json").is_file()
    assert "Workplace" in output
    assert "Perspective" in output
    assert "sha256" not in output
    assert "Components" not in output


def test_status_on_legacy_root_is_read_only_and_requires_explicit_migration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    root = tmp_path / "legacy-status"
    root.mkdir()
    source = root / "workspace.json"
    source_bytes = json.dumps(_legacy_record(), separators=(",", ":")).encode("utf-8")
    source.write_bytes(source_bytes)
    system_root = root / "system"
    system_root.mkdir()
    (system_root / "active-version.json").write_text(
        json.dumps(
            {
                "record_type": "active-system-version",
                "system_version": "0.1.0-alpha.1",
                "activation_status": "active",
            }
        ),
        encoding="utf-8",
    )
    (system_root / "candidate-version.json").write_text(
        json.dumps(
            {
                "record_type": "candidate-system-version",
                "system_version": "0.1.0-alpha.2",
                "candidate_status": "candidate",
            }
        ),
        encoding="utf-8",
    )

    assert main(["status", "--root", str(root), "--details"]) == 2
    output = capsys.readouterr().out

    assert source.read_bytes() == source_bytes
    assert not (root / "workplace.json").exists()
    assert "fractal workplace migrate" in output
    assert "System 0.1.0-alpha.1" not in output
    assert "0.1.0-alpha.2" not in output
    assert "fractal status --active-system PATH --live-state PATH" in output


def test_status_discovers_verified_external_runtime_without_using_legacy_pointer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "legacy-with-current-runtime"
    root.mkdir()
    (root / "workspace.json").write_text(json.dumps(_legacy_record()), encoding="utf-8")

    project_path = root / "projects" / "active" / "current-project" / "record.json"
    project_path.parent.mkdir(parents=True)
    project = {
        "record_type": "project",
        "record_version": 1,
        "project_id": "current-project",
        "title": "Current Project",
        "status": "in_progress",
        "revision": 7,
        "system_version": "0.1.0-alpha.7-project-origin",
        "plan": {"current_phase": 4},
        "decisions": [],
    }
    project_path.write_text(json.dumps(project), encoding="utf-8")

    runtime_root = home / "Library/Application Support/Fractal/runtime"
    active_path = runtime_root / "system-version" / "active.json"
    active_path.parent.mkdir(parents=True)
    active = {
        "record_type": "active-system-version",
        "record_version": 1,
        "version": SYSTEM_VERSION,
        "status": "active",
    }
    active_path.write_text(json.dumps(active), encoding="utf-8")
    live_path = runtime_root / "live-state" / "current.json"
    live_path.parent.mkdir(parents=True)
    live = {
        "record_type": "live-runtime-state",
        "record_version": 1,
        "project": {
            "project_id": "current-project",
            "revision": 7,
            "status": "in_progress",
            "source_path": str(project_path),
            "source_sha256": hashlib.sha256(project_path.read_bytes()).hexdigest(),
        },
        "system_version": {
            "version": SYSTEM_VERSION,
            "status": "active",
            "source_path": str(active_path),
            "source_sha256": hashlib.sha256(active_path.read_bytes()).hexdigest(),
        },
    }
    live_path.write_text(json.dumps(live), encoding="utf-8")

    # The legacy root still requires explicit migration, so the command stays
    # nonzero; its current-state projection nevertheless comes from the
    # verified runtime route rather than the stale legacy version fields.
    assert main(["status", "--root", str(root)]) == 2
    output = capsys.readouterr().out

    assert f"System {SYSTEM_VERSION} · active" in output
    assert "System 0.1.0-alpha.1" not in output
    assert "Project current-project (in_progress, phase 4)" in output
    assert "Project provenance System 0.1.0-alpha.7-project-origin" in output
    assert "Runtime Healthy" in output
    assert "Run the explicit fractal workplace migrate command shown above" in output


def test_status_discovers_verified_external_runtime_for_canonical_workplace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "canonical-workplace"
    workplace = create_workplace(root)
    workplace.write_version_record(
        {
            "record_type": "system-version",
            "record_version": 1,
            "version": SYSTEM_VERSION,
            "status": "active",
        }
    )
    workplace.set_version_pointer("active", SYSTEM_VERSION)
    project = workplace.write_project(
        ProjectRecord(
            project_id="canonical-project",
            title="Canonical Project",
            system_version="0.1.0-alpha.7-project-origin",
        )
    )
    project_path = root / "projects" / "canonical-project" / "record.json"

    runtime_root = home / "Library/Application Support/Fractal/runtime"
    active_path = runtime_root / "system-version" / "active.json"
    active_path.parent.mkdir(parents=True)
    active = {
        "record_type": "active-system-version",
        "record_version": 1,
        "version": SYSTEM_VERSION,
        "status": "active",
    }
    active_path.write_text(json.dumps(active), encoding="utf-8")
    live_path = runtime_root / "live-state" / "current.json"
    live_path.parent.mkdir(parents=True)
    live = {
        "record_type": "live-runtime-state",
        "record_version": 1,
        "project": {
            "project_id": "canonical-project",
            "revision": project["revision"],
            "status": project["status"],
            "source_path": str(project_path),
            "source_sha256": hashlib.sha256(project_path.read_bytes()).hexdigest(),
        },
        "system_version": {
            "version": SYSTEM_VERSION,
            "status": "active",
            "source_path": str(active_path),
            "source_sha256": hashlib.sha256(active_path.read_bytes()).hexdigest(),
        },
    }
    live_path.write_text(json.dumps(live), encoding="utf-8")

    assert main(["status", "--root", str(root)]) == 0
    output = capsys.readouterr().out

    assert f"System {SYSTEM_VERSION} · active" in output
    assert "Project canonical-project (in_progress)" in output
    assert "Project provenance System 0.1.0-alpha.7-project-origin" in output
    assert "Runtime Healthy" in output
    assert "Next action\nContinue current Project" in output
    assert not (root / ".runtime").exists()


def test_workplace_validate_and_migrate_are_explicit_and_safe_twice(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "workspace.json").write_text(json.dumps(_legacy_record()), encoding="utf-8")
    inputs = _verified_migration_inputs(tmp_path)

    migrate_args = [
        "workplace",
        "migrate",
        str(root),
        "--active-pointer",
        str(inputs["active_pointer"]),
        "--live-state",
        str(inputs["live_state"]),
        "--runtime-root",
        str(inputs["runtime_root"]),
        "--event-root",
        str(inputs["event_root"]),
    ]
    assert main(migrate_args) == 0
    first = capsys.readouterr().out
    assert "Workplace migration verified" in first
    assert (root / "workplace.json").is_file()
    assert not (root / "workspace.json").exists()

    assert main(migrate_args) == 0
    second = capsys.readouterr().out
    assert "Workplace migration verified" in second
    assert "idempotent=True" in second
    assert main(["workplace", "validate", str(root)]) == 0
    assert "Workplace valid" in capsys.readouterr().out


def test_workplace_migrate_fails_closed_on_mismatched_live_state(tmp_path: Path, capsys) -> None:
    root = tmp_path / "legacy-mismatch"
    root.mkdir()
    source = root / "workspace.json"
    source_bytes = json.dumps(_legacy_record(), separators=(",", ":")).encode("utf-8")
    source.write_bytes(source_bytes)
    inputs = _verified_migration_inputs(tmp_path)
    live = json.loads(inputs["live_state"].read_text(encoding="utf-8"))
    live["system_version"]["version"] = "0.1.0-alpha.1"
    inputs["live_state"].write_text(json.dumps(live), encoding="utf-8")

    args = [
        "workplace",
        "migrate",
        str(root),
        "--active-pointer",
        str(inputs["active_pointer"]),
        "--live-state",
        str(inputs["live_state"]),
        "--runtime-root",
        str(inputs["runtime_root"]),
        "--event-root",
        str(inputs["event_root"]),
    ]
    assert main(args) == 2
    assert "Workplace issue" in capsys.readouterr().out
    assert source.read_bytes() == source_bytes
    assert not (root / "workplace.json").exists()


def test_status_concise_details_and_json_are_dynamic(tmp_path: Path, capsys) -> None:
    root = tmp_path / "status"
    _healthy_workplace(root)

    assert main(["status", "--root", str(root)]) == 0
    concise = capsys.readouterr().out
    assert "System " + SYSTEM_VERSION + " · active" in concise
    assert "Perspective CLI Project" in concise
    assert "sha256" not in concise
    assert "Components" not in concise

    assert main(["status", "--root", str(root), "--details"]) == 0
    details = capsys.readouterr().out
    assert "Evidence" in details
    assert "Digests" in details

    assert main(["status", "--root", str(root), "--json"]) == 0
    encoded = json.loads(capsys.readouterr().out)
    assert encoded["system"]["state"] == "active"
    assert encoded["project"]["current"]["project_id"] == "cli-project"

    project = json.loads(
        (root / "projects" / "cli-project" / "record.json").read_text(encoding="utf-8")
    )
    project["title"] = "Changed Project"
    (root / "projects" / "cli-project" / "record.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    assert main(["status", "--root", str(root)]) == 0
    changed = capsys.readouterr().out
    assert "Changed Project" in changed


def test_status_suppresses_activated_candidate_and_rejects_ambiguous_state(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "candidate"
    _healthy_workplace(root)
    workplace = create_workplace(root)
    workplace.set_version_pointer("candidate", SYSTEM_VERSION)

    assert main(["status", "--root", str(root), "--details"]) == 0
    activated = capsys.readouterr().out
    assert "Unresolved candidate: null" in activated

    version_path = root / "system" / "versions" / f"{SYSTEM_VERSION}.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["status"] = "candidate"
    version_path.write_text(json.dumps(version), encoding="utf-8")
    assert main(["status", "--root", str(root)]) == 2
    ambiguous = capsys.readouterr().out
    assert "Ambiguous Workplace state" in ambiguous


def test_version_does_not_bootstrap_workplace(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "version-only"
    monkeypatch.setenv("FRACTAL_WORKPLACE", str(root))

    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == SYSTEM_VERSION
    assert not root.exists()
