from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from fractal.models import ProjectRecord
from fractal.storage import ProjectStore, value_sha256
from fractal.workplace import create_workplace
from fractal.workplace_migration import (
    MigrationPreflightError,
    MigrationValidationError,
    build_migration_plan,
    inventory_workplace,
    migrate_workplace_tree,
    rehearse_workplace_migration,
)


def _write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def _verified_active_state(
    tmp_path: Path,
    version: str = "0.1.0-alpha.8-r1-test",
) -> dict[str, Path]:
    runtime_root = tmp_path / "verified-runtime"
    system_root = runtime_root / "system-version"
    pointer_path = system_root / "active.json"
    manifest_path = system_root / "versions" / f"{version}.json"
    manifest = {
        "record_type": "system-version-manifest",
        "record_version": 3,
        "version": version,
        "components": [],
    }
    manifest["manifest_sha256"] = value_sha256(
        {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    )
    _write_json(manifest_path, manifest)
    pointer = {
        "version": version,
        "activated_at": "2026-08-24T13:41:49.367997Z",
        "activated_by": "primary-user",
        "manifest_sha256": manifest["manifest_sha256"],
        "status": "active",
    }
    pointer_bytes = _write_json(pointer_path, pointer)
    live_state_path = runtime_root / "live-state" / "current.json"
    live_state = {
        "record_type": "live-runtime-state",
        "record_version": 1,
        "refreshed_at": "2026-08-24T13:41:49.367997Z",
        "system_version": {
            "version": version,
            "status": "active",
            "activated_at": pointer["activated_at"],
            "activated_by": pointer["activated_by"],
            "manifest_sha256": pointer["manifest_sha256"],
            "source_path": str(pointer_path),
            "source_sha256": hashlib.sha256(pointer_bytes).hexdigest(),
        },
    }
    live_state["state_sha256"] = value_sha256(live_state)
    _write_json(live_state_path, live_state)
    assert hashlib.sha256(pointer_bytes).hexdigest() == live_state["system_version"][
        "source_sha256"
    ]
    return {
        "runtime_root": runtime_root,
        "pointer": pointer_path,
        "live_state": live_state_path,
    }


def _source_entry(root_id: str, path: str) -> dict[str, object]:
    return {
        "root_id": root_id,
        "path": path,
        "source_type": "canonical_record",
        "sensitivity": "private",
        "instruction_authority": "canonical_state",
        "personalisation": False,
        "topics": [root_id],
        "applicability": {"task_types": [], "keywords": [], "project_ids": []},
        "include_suffixes": [".json"],
    }


def _legacy_root(tmp_path: Path, *, distinct_candidate: bool = False) -> Path:
    root = tmp_path / "legacy-workplace"
    _write_json(
        root / "workspace.json",
        {
            "record_type": "fractal-workspace",
            "record_version": 1,
            "workspace_id": "primary",
            "system": {
                "repository": "2canshor/fractal",
                "active_version": "0.1.0-alpha.1",
                "candidate_version": (
                    "0.1.0-alpha.2" if distinct_candidate else "0.1.0-alpha.1"
                ),
            },
            "runtime": {"storage_class": "local-application-support", "committed": False},
        },
    )
    _write_json(
        root / "system/active-version.json",
        {
            "record_type": "active-system-version",
            "record_version": 1,
            "system_version": "0.1.0-alpha.1",
            "activation_status": "active",
            "public_commit": "a" * 40,
        },
    )
    _write_json(
        root / "system/candidate-version.json",
        {
            "record_type": "candidate-system-version",
            "record_version": 1,
            "system_version": "0.1.0-alpha.2" if distinct_candidate else "0.1.0-alpha.1",
            "candidate_status": "candidate" if distinct_candidate else "activated",
            "public_commit": "b" * 40,
        },
    )
    _write_json(
        root / "policies/current.json",
        {
            "record_type": "authority-policy",
            "record_version": 1,
            "policy_id": "primary-authority",
            "authorities": {
                "system_owner": "primary-user",
                "project_completion": "primary-user-only",
            },
            "lifecycle": {
                "required_sequence": ["extract", "rebuild", "test", "switch", "remove"],
                "proposal_is_active_state": False,
                "installation_proves_capability": False,
            },
            "preferences": {"locale": "zh-Hant-HK", "explain_before_jargon": True},
        },
    )
    _write_json(
        root / "profile/current.json",
        {"record_type": "profile", "profile_id": "primary-user"},
    )
    _write_json(
        root / "memory/notes.json",
        {"record_type": "memory", "id": "memory-1", "summary": "retained"},
    )
    _write_json(
        root / "memory/catalogue/context-catalogue.json",
        {
            "record_type": "context-catalogue",
            "record_version": 1,
            "sources": [
                _source_entry("project-records", str(root / "projects/active")),
                _source_entry("profile", str(root / "profile/current.json")),
            ],
        },
    )
    _write_json(
        root / "system/method-registry.json",
        {
            "record_type": "method-activation",
            "record_version": 2,
            "methodologies": {
                "values": [
                    {"id": "fatigue", "status": {"execution": "verified-live"}},
                    {"id": "curiosity", "status": {"execution": "verified-synthetic"}},
                ],
                "duplicates": [
                    {"id": "fatigue", "status": {"execution": "verified-live"}},
                ],
            },
        },
    )
    _write_json(
        root / "adapters/claude/model-route.json",
        {
            "record_type": "claude-model-route",
            "record_version": 1,
            "model": "sonnet",
            "gateway": {
                "base_url": "http://127.0.0.1:8000",
                "api_format": "anthropic-messages",
                "models": ["qwen3.5:9b"],
            },
        },
    )
    # These are durable history/evidence and must not be classified as method
    # or registry definitions during a Workplace migration.
    for relative, record_type in (
        ("system/reviews/review-1.json", "system-review"),
        ("system/decisions/decision-1.json", "decision"),
        ("imports/manifests/phase-1.json", "import-manifest"),
        ("evidence/evidence-1.json", "evidence"),
        ("system/components/registry.json", "component-registry"),
        ("system/components/status.json", "component-status"),
    ):
        _write_json(root / relative, {"record_type": record_type, "id": Path(relative).stem})
    _write_json(
        root / "sessions/raw-turn.json",
        {"record_type": "raw-session", "receipt_id": "r-1"},
    )
    _write_json(
        root / "tool-dumps/raw-tool.json",
        {"record_type": "receipt", "claim": "observed"},
    )
    return root


def _add_projects_and_events(root: Path, tmp_path: Path) -> dict[str, bytes]:
    source_runtime = tmp_path / "source-runtime"
    store = ProjectStore(source_runtime / "projects", source_runtime / "runtime")
    active = store.create(
        ProjectRecord(project_id="active-project", title="Active", system_version="0.1.0-alpha.1"),
        actor="test",
        platform="test",
    )
    completed = store.create(
        ProjectRecord(
            project_id="completed-project",
            title="Completed",
            system_version="0.1.0-alpha.1",
            status="completed",
        ),
        actor="test",
        platform="test",
        authority_write=True,
    )
    project_bytes: dict[str, bytes] = {}
    for record, bucket in ((active, "active"), (completed, "completed")):
        source = source_runtime / "projects" / record.project_id
        destination = root / "projects" / bucket / record.project_id
        shutil.copytree(source, destination)
        project_bytes[record.project_id] = (destination / "record.json").read_bytes()
    event_root = tmp_path / "event-root"
    event_root.mkdir()
    for project_id in (active.project_id, completed.project_id):
        shutil.copy2(
            source_runtime / "runtime/events" / f"{project_id}.jsonl",
            event_root / f"{project_id}.jsonl",
        )
    return {**project_bytes, "__event_root__": str(event_root).encode()}


def _add_legacy_schema_project(root: Path, tmp_path: Path) -> tuple[bytes, Path, bytes]:
    source_runtime = tmp_path / "legacy-schema-runtime"
    store = ProjectStore(source_runtime / "projects", source_runtime / "runtime")
    record = store.create(
        ProjectRecord(
            project_id="legacy-schema-project",
            title="Legacy Schema",
            system_version="0.1.0-alpha.1",
        ),
        actor="test",
        platform="test",
    )
    source = source_runtime / "projects" / record.project_id
    destination = root / "projects/active" / record.project_id
    shutil.copytree(source, destination)
    legacy = json.loads((destination / "record.json").read_text(encoding="utf-8"))
    legacy["schema_version"] = "1.1"
    legacy["plan"].pop("resources")
    legacy_bytes = _write_json(destination / "record.json", legacy)
    (destination / "record.sha256").write_text(
        value_sha256(legacy) + "\n", encoding="ascii"
    )
    event_root = tmp_path / "legacy-schema-events"
    event_root.mkdir()
    event_path = source_runtime / "runtime/events" / f"{record.project_id}.jsonl"
    shutil.copy2(event_path, event_root / event_path.name)
    return legacy_bytes, event_root, event_path.read_bytes()


def test_preflight_is_read_only_and_names_the_whole_tree_operations(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    before = inventory_workplace(root)

    plan = build_migration_plan(root)

    assert plan["record_type"] == "workplace-tree-migration"
    assert "workspace-json-to-workplace-json" in plan["operations"]
    assert "mixed-policy-split-authority-and-user-policy" in plan["operations"]
    assert "raw-live-session-tool-state-to-ignored-runtime" in plan["operations"]
    assert inventory_workplace(root) == before


def test_fresh_canonical_root_is_a_verified_dry_run_noop(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    create_workplace(root)
    before = inventory_workplace(root)

    plan = migrate_workplace_tree(root, dry_run=True)
    result = migrate_workplace_tree(root)

    assert plan["already_canonical"] is True
    assert result.changed is False
    assert inventory_workplace(root) == before


def test_whole_tree_migration_preserves_records_history_and_separates_domains(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    project_bytes = _add_projects_and_events(root, tmp_path)
    event_root = Path(project_bytes.pop("__event_root__").decode())
    preserved = {
        relative: (root / relative).read_bytes()
        for relative in (
            "profile/current.json",
            "memory/notes.json",
            "system/reviews/review-1.json",
            "system/decisions/decision-1.json",
            "imports/manifests/phase-1.json",
            "evidence/evidence-1.json",
            "system/components/registry.json",
            "system/components/status.json",
        )
    }

    result = migrate_workplace_tree(root, event_root=event_root)

    assert result.changed is True
    assert result.verified is True
    assert not (root / "workspace.json").exists()
    assert (root / "workplace.json").is_file()
    assert not (root / "projects/active").exists()
    assert not (root / "projects/completed").exists()
    for project_id, raw in project_bytes.items():
        assert (root / "projects" / project_id / "record.json").read_bytes() == raw
        assert (
            (root / "projects" / project_id / "record.sha256").read_text().strip()
            == value_sha256(json.loads(raw))
        )
    assert result.validation["event_chains"]
    assert all(item["event_chain_valid"] for item in result.validation["event_chains"])

    context = json.loads((root / "context/sources.json").read_text())
    assert all(
        "path" not in source and source["locator"].startswith("workplace://")
        for source in context["sources"]
    )
    assert next(
        source["locator"]
        for source in context["sources"]
        if source["root_id"] == "project-records"
    ) == "workplace://projects"
    assert (root / "system/history/context/context-catalogue.json").is_file()

    policy = json.loads((root / "policies/current.json").read_text())
    authority = json.loads((root / "authority/bindings.json").read_text())
    assert policy["record_type"] == "user-policy"
    assert "lifecycle" not in policy and "authorities" not in policy
    assert authority["record_type"] == "authority-bindings"
    assert authority["bindings"]["project_completion"] == "primary-user-only"
    assert json.loads(
        (root / "system/history/policy/current-authority-policy.json").read_text()
    )["lifecycle"]

    versions = json.loads((root / "system/versions/0.1.0-alpha.1.json").read_text())
    assert versions["status"] == "active"
    assert (root / "system/active-version.json").read_text().find("system-version-pointer") >= 0
    assert not (root / "system/candidate-version.json").exists()
    assert (root / "system/history/legacy-version-pointers/candidate-version.json").is_file()

    methods = json.loads((root / "system/method-status.json").read_text())
    assert [item["id"] for item in methods["methods"]] == ["curiosity", "fatigue"]
    assert (root / "system/history/method-registry.json").is_file()
    assert not (root / "system/method-registry.json").exists()

    preferences = json.loads((root / "adapters/preferences.json").read_text())
    assert "base_url" not in json.dumps(preferences)
    endpoints = json.loads((root / ".runtime/adapters/endpoints.json").read_text())
    assert endpoints["endpoints"][0]["endpoint"] == "http://127.0.0.1:8000"
    assert (root / "system/history/adapters/claude/model-route.json").is_file()
    assert (root / ".runtime/.gitignore").read_text() == "*\n!.gitignore\n"
    assert not (root / "sessions").exists() and not (root / "tool-dumps").exists()
    receipts = json.loads((root / "evidence/runtime-receipts.json").read_text())
    assert any(item.get("receipt_id") == "r-1" for item in receipts["receipts"])

    for relative, raw in preserved.items():
        assert (root / relative).read_bytes() == raw


def test_legacy_project_schema_upgrade_updates_copy_sidecar_and_event_chain(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    legacy_bytes, event_root, source_event_bytes = _add_legacy_schema_project(root, tmp_path)
    source_event_path = event_root / "legacy-schema-project.jsonl"
    before_event_root = source_event_path.read_bytes()

    first = migrate_workplace_tree(root, event_root=event_root)

    canonical_path = root / "projects/legacy-schema-project/record.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    event_path = root / ".runtime/events/legacy-schema-project.jsonl"
    events = [json.loads(line) for line in event_path.read_text().splitlines() if line.strip()]
    assert first.changed is True
    assert canonical["schema_version"] == "1.2"
    assert canonical["revision"] == 1
    assert (root / "projects/legacy-schema-project/record.sha256").read_text().strip() == (
        value_sha256(canonical)
    )
    assert len(events) == 2
    migration_event = events[-1]
    assert migration_event["action"] == "migrate-project-schema"
    assert migration_event["base_revision"] == 0
    assert migration_event["new_revision"] == 1
    assert migration_event["previous_event_hash"] == events[-2]["event_hash"]
    event_body = dict(migration_event)
    event_hash = event_body.pop("event_hash")
    assert value_sha256(event_body) == event_hash
    assert source_event_path.read_bytes() == before_event_root == source_event_bytes
    assert legacy_bytes != canonical_path.read_bytes()

    canonical_bytes = canonical_path.read_bytes()
    event_bytes = event_path.read_bytes()
    second = migrate_workplace_tree(root, event_root=event_root)
    assert second.changed is False
    assert canonical_path.read_bytes() == canonical_bytes
    assert event_path.read_bytes() == event_bytes
    assert source_event_path.read_bytes() == before_event_root


def test_already_current_project_schema_is_a_noop_without_event_journal(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    _add_projects_and_events(root, tmp_path)

    first = migrate_workplace_tree(root)
    canonical_path = root / "projects/active-project/record.json"
    canonical_bytes = canonical_path.read_bytes()
    second = migrate_workplace_tree(root)

    assert first.changed is True
    assert second.changed is False
    assert second.validation["project_count"] == 2
    current = json.loads((root / "projects/active-project/record.json").read_text())
    assert current["schema_version"] == "1.2"
    assert canonical_path.read_bytes() == canonical_bytes


def test_legacy_project_schema_fails_closed_without_explicit_event_journal(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    _add_legacy_schema_project(root, tmp_path)
    before = inventory_workplace(root)

    with pytest.raises(
        MigrationValidationError, match="Explicit copied event journal unavailable"
    ):
        migrate_workplace_tree(root)

    assert inventory_workplace(root) == before


def test_migration_is_safe_twice_without_second_tree_change(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    _add_projects_and_events(root, tmp_path)
    ignore_bytes = b"/sockets/\n*.sock\n.env\n*.key\n"
    (root / ".gitignore").write_bytes(ignore_bytes)

    first = migrate_workplace_tree(root)
    first_inventory = inventory_workplace(root)
    second_plan = build_migration_plan(root)
    second = migrate_workplace_tree(root)

    assert first.changed is True
    assert second.changed is False
    assert second.idempotent is True
    assert second_plan.operations == ()
    assert second_plan.already_canonical is True
    assert (root / ".gitignore").read_bytes() == ignore_bytes
    assert inventory_workplace(root) == first_inventory


def test_legacy_project_lock_files_are_ephemeral_and_do_not_become_projects(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    _add_projects_and_events(root, tmp_path)
    locks = {
        "active": b"active-lock",
        "completed": b"completed-lock",
    }
    for bucket, raw in locks.items():
        (root / "projects" / bucket / f".{bucket}-project.lock").write_bytes(raw)
    unrelated = root / "projects" / "README.txt"
    unrelated.write_text("keep this unrelated marker", encoding="utf-8")

    first = migrate_workplace_tree(root)
    second = migrate_workplace_tree(root)

    assert first.changed is True
    assert second.changed is False
    assert set(first.validation["project_ids"]) == {
        "active-project",
        "completed-project",
    }
    assert not (root / "projects/active").exists()
    assert not (root / "projects/completed").exists()
    for bucket, raw in locks.items():
        assert (
            root / ".runtime/raw/project-locks" / bucket / f".{bucket}-project.lock"
        ).read_bytes() == raw
    assert unrelated.read_text(encoding="utf-8") == "keep this unrelated marker"


@pytest.mark.parametrize(
    "failure_step",
    ["staging", "candidate-built", "candidate-validated", "switched", "final-validated"],
)
def test_failed_staged_migration_restores_original_tree(tmp_path: Path, failure_step: str) -> None:
    root = _legacy_root(tmp_path)
    before = inventory_workplace(root)

    def fail(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(failure_step)

    with pytest.raises(RuntimeError, match=failure_step):
        migrate_workplace_tree(root, failure_injector=fail)

    assert inventory_workplace(root) == before
    assert (root / "workspace.json").is_file()
    assert not (root / "workplace.json").exists()


def test_distinct_unresolved_candidate_retains_candidate_pointer(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path, distinct_candidate=True)

    migrate_workplace_tree(root)

    pointer = json.loads((root / "system/candidate-version.json").read_text())
    assert pointer["pointer_kind"] == "candidate"
    candidate = json.loads((root / "system/versions/0.1.0-alpha.2.json").read_text())
    assert candidate["status"] == "candidate"


def test_colliding_project_bytes_fail_before_switch(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    project_bytes = _add_projects_and_events(root, tmp_path)
    project_id = "active-project"
    canonical = root / "projects" / project_id
    canonical.mkdir(parents=True)
    (canonical / "record.json").write_bytes(project_bytes[project_id] + b"\n")

    before = inventory_workplace(root)
    with pytest.raises(MigrationPreflightError, match="Project collision"):
        migrate_workplace_tree(root)
    assert inventory_workplace(root) == before


def test_rehearsal_uses_a_copy_and_is_portable_without_absolute_paths(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    project_bytes = _add_projects_and_events(root, tmp_path)
    event_root = Path(project_bytes.pop("__event_root__").decode())
    (root / ".git").mkdir()
    source_before = inventory_workplace(root)

    report = rehearse_workplace_migration(root, event_root=event_root)

    assert report["candidate_only"] is True
    assert report["version_activation"] is False
    assert report["real_migration"] is False
    assert report["source_unchanged"] is True
    assert report["source_had_git"] is True
    assert report["rehearsal_git_omitted"] is True
    assert report["second_root"]["portable"] is True
    assert report["temporary_copy_removed"] is True
    assert inventory_workplace(root) == source_before


def test_verified_active_state_reconciles_stale_workplace_without_activation(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    stale_active = json.loads((root / "system/active-version.json").read_text())
    stale_active["system_version"] = "0.1.0-alpha.2"
    _write_json(root / "system/active-version.json", stale_active)
    stale_candidate = json.loads((root / "system/candidate-version.json").read_text())
    stale_candidate["system_version"] = "0.1.0-alpha.2"
    stale_candidate["candidate_status"] = "activated"
    _write_json(root / "system/candidate-version.json", stale_candidate)
    workspace = json.loads((root / "workspace.json").read_text())
    workspace["system"]["active_version"] = "0.1.0-alpha.2"
    workspace["system"]["candidate_version"] = "0.1.0-alpha.2"
    _write_json(root / "workspace.json", workspace)
    active = _verified_active_state(tmp_path)
    pointer_before = active["pointer"].read_bytes()
    live_before = active["live_state"].read_bytes()

    result = migrate_workplace_tree(
        root,
        runtime_root=active["runtime_root"],
        active_pointer_path=active["pointer"],
        live_state_path=active["live_state"],
    )

    pointer = json.loads((root / "system/active-version.json").read_text())
    old = json.loads((root / "system/versions/0.1.0-alpha.2.json").read_text())
    assert result.validation["active_version"] == "0.1.0-alpha.8-r1-test"
    assert pointer["record_uri"].endswith("0.1.0-alpha.8-r1-test.json")
    assert old["status"] == "previously-active"
    assert old["legacy"]["provenance"]["verified_active_version"] == (
        "0.1.0-alpha.8-r1-test"
    )
    assert not (root / "system/candidate-version.json").exists()
    assert active["pointer"].read_bytes() == pointer_before
    assert active["live_state"].read_bytes() == live_before


def test_raw_component_discovery_dumps_are_ephemeral_but_registry_is_retained(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    registry_before = (root / "system/components/registry.json").read_bytes()
    status_before = (root / "system/components/status.json").read_bytes()
    synthetic_home = "/" + "Users" + "/example"
    _write_json(
        root / "system/components/claude-live-surface.json",
        {
            "cwd": synthetic_home + "/Fractal",
            "messaging_socket_path": "/tmp/example.sock",
            "receipt_id": "surface-1",
            "claim": "observed",
        },
    )
    _write_json(
        root / "system/components/codex-live-tools.json",
        {
            "record_type": "codex-live-tool-catalogue",
            "tool_snapshot": synthetic_home + "/tools.json",
            "receipt_id": "tools-1",
            "status": "observed",
        },
    )

    result = migrate_workplace_tree(root)

    assert result.validation["runtime_ignored"] is True
    assert not (root / "system/components/claude-live-surface.json").exists()
    assert not (root / "system/components/codex-live-tools.json").exists()
    assert (root / ".runtime/raw/system/components/claude-live-surface.json").is_file()
    assert (root / ".runtime/raw/system/components/codex-live-tools.json").is_file()
    receipts = json.loads((root / "evidence/runtime-receipts.json").read_text())
    receipt_ids = {item["receipt_id"] for item in receipts["receipts"] if "receipt_id" in item}
    assert {"surface-1", "tools-1"}.issubset(receipt_ids)
    assert (root / "system/components/registry.json").read_bytes() == registry_before
    assert (root / "system/components/status.json").read_bytes() == status_before


def test_privacy_scans_entire_candidate_and_preserves_project_evidence_source_hash(
    tmp_path: Path,
) -> None:
    root = _legacy_root(tmp_path)
    project_bytes = _add_projects_and_events(root, tmp_path)
    evidence_before = (root / "projects/active/active-project/record.json").read_bytes()
    synthetic_home = "/" + "Users" + "/example"
    selection = {
        "record_type": "component-selection",
        "cwd": synthetic_home + "/Fractal",
        "private_address": "192.168.1.12",
        "socket_path": "/tmp/example.sock",
        "api_key": "should-not-survive",
    }
    _write_json(root / "system/components/selection.json", selection)

    result = migrate_workplace_tree(root)

    assert result.validation["privacy_valid"] is True
    assert result.validation["privacy"]["findings"] == []
    canonical_selection = json.loads((root / "system/components/selection.json").read_text())
    assert "Users" not in json.dumps(canonical_selection)
    assert "192.168.1.12" not in json.dumps(canonical_selection)
    assert "should-not-survive" not in json.dumps(canonical_selection)
    assert (root / "system/history/privacy/system/components/selection.json").is_file()
    assert (root / "projects/active-project/record.json").read_bytes() == evidence_before
    assert (root / "projects/active-project/record.sha256").read_text().strip() == value_sha256(
        json.loads(evidence_before)
    )
    assert project_bytes["active-project"] == evidence_before


def test_unmapped_external_context_source_fails_preflight(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    catalogue_path = root / "memory/catalogue/context-catalogue.json"
    catalogue = json.loads(catalogue_path.read_text())
    catalogue["sources"].append(_source_entry("external-guides", "/opt/private-guides"))
    _write_json(catalogue_path, catalogue)
    before = inventory_workplace(root)

    with pytest.raises(MigrationPreflightError, match="context_roots mapping"):
        migrate_workplace_tree(root)

    assert inventory_workplace(root) == before


def test_external_context_file_retains_filename_under_directory_mapping(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    catalogue_path = root / "memory/catalogue/context-catalogue.json"
    catalogue = json.loads(catalogue_path.read_text())
    architecture = tmp_path / "public" / "ARCHITECTURE.md"
    architecture.parent.mkdir()
    architecture.write_text("# Architecture\n", encoding="utf-8")
    catalogue["sources"].append(_source_entry("public-architecture", str(architecture)))
    _write_json(catalogue_path, catalogue)

    migrate_workplace_tree(
        root,
        context_roots={"public-architecture": architecture},
    )

    migrated = json.loads((root / "context/sources.json").read_text())
    source = next(
        item for item in migrated["sources"] if item["root_id"] == "public-architecture"
    )
    assert source["locator"] == "local://public-architecture/ARCHITECTURE.md"


def test_rehearsal_reports_verified_active_version_and_idempotence(tmp_path: Path) -> None:
    root = _legacy_root(tmp_path)
    active = _verified_active_state(tmp_path)
    report = rehearse_workplace_migration(
        root,
        runtime_root=active["runtime_root"],
        active_pointer_path=active["pointer"],
        live_state_path=active["live_state"],
    )

    assert report["active_state"] == {
        "verified": True,
        "version": "0.1.0-alpha.8-r1-test",
        "live_state_supplied": True,
        "pointer_supplied": True,
    }
    assert report["migration"]["validation"]["active_version"] == (
        "0.1.0-alpha.8-r1-test"
    )
    assert report["second_root"]["changed"] is False
    assert report["second_root"]["idempotent"] is True
    assert report["second_root"]["inventory_unchanged"] is True
    assert report["privacy"]["valid"] is True
