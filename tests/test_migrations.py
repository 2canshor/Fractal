from __future__ import annotations

from pathlib import Path

from fractal.migrations import migrate_project_record
from fractal.models import ProjectRecord, utc_now
from fractal.storage import ProjectStore


def legacy_record() -> dict:
    value = ProjectRecord(
        project_id="migration-project",
        title="Migration Project",
        system_version="0.1.0-alpha.1",
    ).to_dict()
    value["schema_version"] = "1.0"
    value["direction"] = {
        "summary": "Preserve the approved outcome",
        "status": "provisional",
        "confirmed_at": None,
    }
    del value["lifecycle"]
    return value


def test_project_1_0_migrates_without_losing_direction() -> None:
    migrated, applied = migrate_project_record(legacy_record())
    assert applied == ["project-1.0-to-1.1", "project-1.1-to-1.2"]
    assert migrated["schema_version"] == "1.2"
    assert migrated["lifecycle"]["direction"]["intended_outcome"] == (
        "Preserve the approved outcome"
    )


def test_store_migration_is_evented_and_verified(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects", tmp_path / "runtime")
    value = legacy_record()
    store._write_record("migration-project", value)
    store._append_event(
        "migration-project",
        {
            "event_id": "event-create",
            "project_id": "migration-project",
            "base_revision": None,
            "new_revision": 0,
            "actor": "main-agent",
            "platform": "test-adapter",
            "action": "create-project",
            "changes": [],
            "occurred_at": utc_now(),
        },
    )
    migrated = store.migrate(
        "migration-project",
        actor="main-agent",
        platform="test-adapter",
    )
    assert migrated.schema_version == "1.2"
    assert [item["dimension"] for item in migrated.plan["resources"]] == [
        "time",
        "attention",
    ]
    assert migrated.revision == 1
    assert store.verify("migration-project")["event_count"] == 2
