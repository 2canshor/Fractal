from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from fractal.adapter_hook import handle_hook, resolve_session_state
from fractal.adapter_hook import main as hook_main
from fractal.cli import main as cli_main
from fractal.live_state import LiveRuntimeStateError, LiveRuntimeStateStore
from fractal.models import ProjectRecord
from fractal.storage import ProjectStore
from fractal.versioning import VersionStore

PROJECT_ID = "current-project"
PROJECT_REVISION = 0


def authority_evidence(store: VersionStore, label: str) -> dict[str, str]:
    text = f"approve {label}\n"
    message_id = f"msg-{label}"
    turn_id = f"turn-{label}"
    path = store.root / f"session-{label}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": message_id,
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            }
        )
        + "\n"
    )
    return {
        "session_path": str(path),
        "turn_id": turn_id,
        "message_id": message_id,
        "message_sha256": hashlib.sha256((text + "\n").encode()).hexdigest(),
    }


def build_version(store: VersionStore, version: str) -> None:
    batch = {"decision_batch_id": f"batch-{version}"}
    target, expected_state = store.build_authority_scope(
        version=version,
        public_commit="a" * 40,
        private_commit="b" * 40,
        decision_batch=batch,
    )
    receipt = store.authority.issue(
        action="build",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(store, f"build-{version}"),
    )
    store.build_candidate(
        version=version,
        public_commit="a" * 40,
        private_commit="b" * 40,
        components=[
            {
                "component_id": "recording-core",
                "version": "1.0.0",
                "sha256": "c" * 64,
                "dependencies": [],
            }
        ],
        adapter_hashes={"codex": "d" * 64},
        migrations=[],
        restore_point={"kind": "manifest", "version": "previous"},
        verification={
            "clean_build": True,
            "tests_passed": True,
            "adapter_hashes_verified": True,
            "migrations_verified": True,
            "restore_verified": True,
        },
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        decision_batch=batch,
        architecture_lineage={"structural_gate_passed": True},
        claim_gate_audit={"passed": True, "claim_count": 20},
        preservation_audits={
            "phase_a_pre_build": {"passed": True, "receipt_sha256": "e" * 64},
            "phase_b_post_build_pre_activation": {
                "passed": True,
                "receipt_sha256": "f" * 64,
            },
        },
        authority_receipt_id=receipt["receipt_id"],
    )


def live_fixture(tmp_path: Path) -> tuple[ProjectStore, VersionStore, Path, Path]:
    runtime_root = tmp_path / "runtime"
    project_store = ProjectStore(tmp_path / "projects" / "active", runtime_root)
    project_store.create(
        ProjectRecord(
            project_id="current-project",
            title="Current Project",
            system_version="0.1.0-alpha.1",
        ),
        actor="main-agent",
        platform="codex",
    )
    version_store = VersionStore(runtime_root / "system-version")
    build_version(version_store, "0.1.0-alpha.1")
    target, expected_state = version_store.action_authority_scope(
        "0.1.0-alpha.1", action="activate"
    )
    receipt = version_store.authority.issue(
        action="activate",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(version_store, "activate-alpha-1"),
    )
    version_store.activate(
        "0.1.0-alpha.1",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=receipt["receipt_id"],
    )
    return (
        project_store,
        version_store,
        tmp_path / "projects" / "active" / "current-project" / "record.json",
        runtime_root / "system-version" / "active.json",
    )


def test_session_start_reconciles_canonical_sources_not_stale_adapter_snapshot(
    tmp_path: Path,
) -> None:
    _, _, record_path, pointer_path = live_fixture(tmp_path)
    context = {
        "system_version": "0.1.0-alpha.0-build-snapshot",
        "active_project": {
            "project_id": "old-project",
            "status": "in_progress",
            "revision": 94,
            "current_phase": 10,
        },
        "live_runtime": {
            "state_path": str(tmp_path / "runtime" / "live-state" / "current.json"),
            "project_record_path": str(record_path),
            "active_pointer_path": str(pointer_path),
        },
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
    }
    LiveRuntimeStateStore(tmp_path / "runtime").reconcile(
        project_record_path=record_path,
        active_pointer_path=pointer_path,
    )
    live_state = resolve_session_state(context)
    result = handle_hook(
        "session-start", context, {"source": "startup"}, live_state=live_state
    )
    summary = result["hookSpecificOutput"]["additionalContext"]
    assert "Fractal 0.1.0-alpha.1" in summary
    assert "current-project" in summary
    assert "revision 0" in summary
    assert "old-project" not in summary
    assert json.loads(
        (tmp_path / "runtime" / "live-state" / "current.json").read_text()
    )["project"]["revision"] == 0


def test_session_start_fails_closed_when_canonical_project_digest_is_invalid(
    tmp_path: Path,
) -> None:
    _, _, record_path, pointer_path = live_fixture(tmp_path)
    tampered = json.loads(record_path.read_text())
    tampered["revision"] = 999
    record_path.write_text(json.dumps(tampered))
    context = {
        "live_runtime": {
            "state_path": str(tmp_path / "runtime" / "live-state" / "current.json"),
            "project_record_path": str(record_path),
            "active_pointer_path": str(pointer_path),
        }
    }
    # The write-time read model still points to these canonical sources. SessionStart
    # must verify them again and reject the newly tampered Project record.
    with pytest.raises(LiveRuntimeStateError, match="Project digest mismatch"):
        resolve_session_state(context)


def test_hook_cli_surfaces_live_state_failure_without_repeating_stale_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, record_path, _ = live_fixture(tmp_path)
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "system_version": "stale-version",
                "active_project": {
                    "project_id": "stale-project",
                    "status": "in_progress",
                    "revision": 94,
                    "current_phase": 10,
                },
                "live_runtime": {
                    "state_path": str(
                        tmp_path / "runtime" / "live-state" / "current.json"
                    )
                },
            }
        )
    )
    tampered = json.loads(record_path.read_text())
    tampered["revision"] = 999
    record_path.write_text(json.dumps(tampered))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"source": "startup"})))
    assert hook_main(["--event", "session-start", "--context", str(context_path)]) == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "FRACTAL LIVE STATE ERROR" in output
    assert "Stop Project-state-dependent routing" in output
    assert "stale-project" not in output


def test_canonical_writes_refresh_live_state_but_candidate_build_does_not(
    tmp_path: Path,
) -> None:
    project_store, version_store, _, _ = live_fixture(tmp_path)
    live_store = LiveRuntimeStateStore(tmp_path / "runtime")
    state = live_store.read()
    assert state["project"]["project_id"] == "current-project"
    assert state["system_version"]["version"] == "0.1.0-alpha.1"
    assert state["system_version"]["status"] == "active"

    build_version(version_store, "0.1.0-alpha.2")
    assert live_store.read()["system_version"]["version"] == "0.1.0-alpha.1"

    project_store.migrate("current-project", actor="main-agent", platform="codex")
    assert live_store.read()["project"]["revision"] == project_store.read(
        "current-project"
    ).revision


def test_live_state_cli_reconciles_and_verifies_current_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, record_path, pointer_path = live_fixture(tmp_path)
    state_path = tmp_path / "runtime" / "live-state" / "current.json"
    assert (
        cli_main(
            [
                "live-state",
                "reconcile",
                "--state",
                str(state_path),
                "--project-record",
                str(record_path),
                "--active-pointer",
                str(pointer_path),
            ]
        )
        == 0
    )
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled["project"]["project_id"] == "current-project"
    assert cli_main(["live-state", "show", "--state", str(state_path)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["system_version"]["status"] == "active"
