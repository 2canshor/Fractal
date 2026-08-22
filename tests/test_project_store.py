from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.index import rebuild_project_index, search_project_index
from fractal.models import Change, ProjectRecord, utc_now
from fractal.storage import AuthorityError, IntegrityError, ProjectStore
from fractal.views import render_project_summary


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "projects", tmp_path / "runtime")


@pytest.fixture
def project(store: ProjectStore) -> ProjectRecord:
    record = ProjectRecord(
        project_id="real-project",
        title="Real Project",
        system_version="0.1.0-alpha.1",
    )
    return store.create(record, actor="main-agent", platform="platform-a")


def progress_item(item_id: str, summary: str) -> dict:
    return {
        "id": item_id,
        "summary": summary,
        "status": "succeeded",
        "occurred_at": utc_now(),
        "evidence_ids": [],
    }


def evidence_item(item_id: str, claim: str) -> dict:
    return {
        "id": item_id,
        "kind": "test",
        "claim": claim,
        "source": "local-test",
        "observed_at": utc_now(),
        "sha256": None,
    }


def test_create_read_back_and_verify_event_chain(
    store: ProjectStore, project: ProjectRecord
) -> None:
    read_back = store.read(project.project_id)
    assert read_back.project_id == "real-project"
    assert read_back.system_version == "0.1.0-alpha.1"
    assert store.verify(project.project_id)["event_count"] == 1


def test_two_stale_platforms_keep_compatible_appends(
    store: ProjectStore, project: ProjectRecord
) -> None:
    first = store.apply_changes(
        project.project_id,
        expected_revision=0,
        changes=[Change("append", "/progress", progress_item("progress-a", "First"))],
        actor="agent-a",
        platform="platform-a",
    )
    second = store.apply_changes(
        project.project_id,
        expected_revision=0,
        changes=[Change("append", "/evidence", evidence_item("evidence-b", "Second"))],
        actor="agent-b",
        platform="platform-b",
    )
    read_back = store.read(project.project_id)
    assert first.revision == 1
    assert second.merged is True
    assert second.revision == 2
    assert [item["id"] for item in read_back.progress] == ["progress-a"]
    assert [item["id"] for item in read_back.evidence] == ["evidence-b"]


def test_genuine_stale_conflict_creates_request_decision(
    store: ProjectStore, project: ProjectRecord
) -> None:
    accepted = store.apply_changes(
        project.project_id,
        expected_revision=0,
        changes=[Change("set", "/plan/current_phase", 2, base_value=None)],
        actor="agent-a",
        platform="platform-a",
    )
    conflict = store.apply_changes(
        project.project_id,
        expected_revision=0,
        changes=[Change("set", "/plan/current_phase", 3, base_value=None)],
        actor="agent-b",
        platform="platform-b",
    )
    read_back = store.read(project.project_id)
    assert accepted.applied is True
    assert conflict.applied is False
    assert conflict.conflict_request_id is not None
    assert read_back.plan["current_phase"] == 2
    assert read_back.requests[-1]["path"] == "/plan/current_phase"
    assert read_back.requests[-1]["status"] == "pending"


def test_ordinary_write_cannot_claim_completion(
    store: ProjectStore, project: ProjectRecord
) -> None:
    with pytest.raises(AuthorityError, match="Completion"):
        store.apply_changes(
            project.project_id,
            expected_revision=0,
            changes=[Change("set", "/status", "completed", base_value="in_progress")],
            actor="agent-a",
            platform="platform-a",
        )


def test_ordinary_write_cannot_approve_decision(
    store: ProjectStore, project: ProjectRecord
) -> None:
    decision = {
        "id": "decision-a",
        "subject": "Choose a path",
        "status": "approved",
        "decision": "Path A",
        "reason": "Example",
        "authority": "system-owner",
        "evidence_ids": [],
        "recorded_at": utc_now(),
    }
    with pytest.raises(AuthorityError, match="approval"):
        store.apply_changes(
            project.project_id,
            expected_revision=0,
            changes=[Change("append", "/decisions", decision)],
            actor="agent-a",
            platform="platform-a",
        )


def test_ordinary_create_cannot_claim_completed_state(store: ProjectStore) -> None:
    record = ProjectRecord(
        project_id="completed-project",
        title="Completed Project",
        system_version="0.1.0-alpha.1",
        status="completed",
    )
    with pytest.raises(AuthorityError, match="Completion"):
        store.create(record, actor="agent-a", platform="platform-a")


def test_tampered_record_fails_integrity_check(
    store: ProjectStore, project: ProjectRecord
) -> None:
    record_path = store.project_root / project.project_id / "record.json"
    value = json.loads(record_path.read_text())
    value["title"] = "Tampered"
    record_path.write_text(json.dumps(value))
    with pytest.raises(IntegrityError, match="digest mismatch"):
        store.read(project.project_id)


def test_project_id_cannot_escape_storage_root(store: ProjectStore) -> None:
    with pytest.raises(ValueError, match="Invalid Project id"):
        store.read("../outside")


def test_missing_event_is_detected(store: ProjectStore, project: ProjectRecord) -> None:
    store.apply_changes(
        project.project_id,
        expected_revision=0,
        changes=[Change("append", "/progress", progress_item("progress-a", "First"))],
        actor="agent-a",
        platform="platform-a",
    )
    event_path = store.runtime_root / "events" / f"{project.project_id}.jsonl"
    first_event = event_path.read_text().splitlines()[0]
    event_path.write_text(first_event + "\n")
    with pytest.raises(IntegrityError, match="does not reach"):
        store.verify(project.project_id)


def test_derived_index_can_be_deleted_and_rebuilt(
    store: ProjectStore, project: ProjectRecord, tmp_path: Path
) -> None:
    database_path = tmp_path / "index" / "projects.sqlite"
    assert rebuild_project_index(store.project_root, database_path) == 1
    assert search_project_index(database_path, "Real")[0]["project_id"] == project.project_id
    database_path.unlink()
    assert rebuild_project_index(store.project_root, database_path) == 1
    assert search_project_index(database_path, "Project")[0]["title"] == "Real Project"


def test_human_view_is_derived_from_canonical_state(
    store: ProjectStore, project: ProjectRecord
) -> None:
    output = render_project_summary(store.read(project.project_id))
    assert "# Real Project" in output
    assert "Status: `in_progress`" in output
    assert "System Version: `0.1.0-alpha.1`" in output
