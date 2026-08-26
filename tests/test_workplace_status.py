from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from fractal.workplace_status import (
    build_workplace_status,
    render_status_json,
    render_workplace_status,
    render_workplace_status_details,
    render_workplace_status_json,
)

PUBLIC_VERSION = "0.1.0-alpha.8-r1"
LIVE_VERSION = f"{PUBLIC_VERSION}-b0a9d04"


def active_record(version: str = LIVE_VERSION) -> dict[str, object]:
    return {
        "record_type": "active-system-version",
        "record_version": 1,
        "system_version": version,
        "activation_status": "active",
    }


def candidate_record(
    version: str = LIVE_VERSION,
    *,
    status: str = "activated",
) -> dict[str, object]:
    return {
        "record_type": "candidate-system-version",
        "record_version": 1,
        "system_version": version,
        "candidate_status": status,
    }


def project_record(
    project_id: str,
    *,
    status: str = "in_progress",
    revision: int = 0,
    decisions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "record_type": "project",
        "project_id": project_id,
        "title": project_id.replace("-", " ").title(),
        "status": status,
        "revision": revision,
        "system_version": PUBLIC_VERSION,
        "plan": {"current_phase": 2},
        "decisions": decisions or [],
    }


def live_state(
    *,
    project_id: str = "current-project",
    revision: int = 0,
    system_version: str = LIVE_VERSION,
    status: str = "active",
) -> dict[str, object]:
    return {
        "record_type": "live-runtime-state",
        "record_version": 1,
        "project": {
            "project_id": project_id,
            "revision": revision,
            "status": "in_progress",
        },
        "system_version": {
            "version": system_version,
            "status": status,
        },
    }


def healthy_inputs() -> dict[str, object]:
    return {
        "public_system": {"version": PUBLIC_VERSION},
        "workplace_active": active_record(),
        "workplace_candidate": candidate_record(),
        "projects": [project_record("current-project")],
        "live_state": live_state(),
    }


def test_dynamic_model_uses_changed_canonical_values(tmp_path: Path) -> None:
    public_path = tmp_path / "public.json"
    active_path = tmp_path / "active.json"
    public_path.write_text(json.dumps({"version": PUBLIC_VERSION}), encoding="utf-8")
    active_path.write_text(json.dumps(active_record()), encoding="utf-8")
    first = build_workplace_status(
        public_system_path=public_path,
        active_system_path=active_path,
        projects=[project_record("current-project")],
        live_state=live_state(),
    )
    assert first["system"]["version"] == PUBLIC_VERSION
    assert first["system"]["current_version"] == LIVE_VERSION
    assert first["workplace"]["active_system_version"]["version"] == LIVE_VERSION

    changed_public = "0.1.0-alpha.9"
    public_path.write_text(json.dumps({"version": changed_public}), encoding="utf-8")
    second = build_workplace_status(
        public_system_path=public_path,
        active_system_path=active_path,
        projects=[project_record("current-project")],
        live_state=live_state(),
    )
    assert second["system"]["version"] == changed_public
    assert second["system"]["current_version"] is None
    assert "does not match verified live" in " ".join(second["system"]["issues"])
    rendered = render_workplace_status(second)
    assert "System unknown · mismatch" in rendered
    assert f"Public System provenance {changed_public}" in rendered


def test_activated_or_historical_candidate_matching_active_is_not_unresolved() -> None:
    inputs = healthy_inputs()
    activated = build_workplace_status(**inputs)
    assert activated["workplace"]["unresolved_candidate"] is None
    assert activated["decisions"]["next"] is None

    inputs["workplace_candidate"] = candidate_record(status="historical")
    historical = build_workplace_status(**inputs)
    assert historical["workplace"]["unresolved_candidate"] is None


def test_same_active_and_unresolved_candidate_is_ambiguous() -> None:
    inputs = healthy_inputs()
    inputs["workplace_candidate"] = candidate_record(status="candidate")
    status = build_workplace_status(**inputs)
    assert status["workplace"]["unresolved_candidate"]["version"] == LIVE_VERSION
    assert any("Ambiguous Workplace state" in issue for issue in status["workplace"]["issues"])
    assert status["decisions"]["next"]["status"] == "candidate"


def test_missing_invalid_and_stale_inputs_are_reported() -> None:
    inputs = healthy_inputs()
    missing = build_workplace_status(
        public_system=inputs["public_system"],
        active_system_path=Path("/definitely/missing/active.json"),
        projects=inputs["projects"],
        live_state=inputs["live_state"],
    )
    assert any("active System record is missing" in issue for issue in missing["issues"])

    invalid_live = copy.deepcopy(inputs["live_state"])
    invalid_live["state_sha256"] = "0" * 64
    invalid = build_workplace_status(
        public_system=inputs["public_system"],
        workplace_active=inputs["workplace_active"],
        projects=inputs["projects"],
        live_state=invalid_live,
    )
    assert "Live runtime state integrity failure" in invalid["runtime"]["issues"]


def test_current_project_comes_from_status_not_directory_name(tmp_path: Path) -> None:
    first = tmp_path / "left" / "record.json"
    second = tmp_path / "right" / "record.json"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(json.dumps(project_record("finished", status="completed")), encoding="utf-8")
    second.write_text(json.dumps(project_record("working", status="in_progress")), encoding="utf-8")
    status = build_workplace_status(
        public_system=PUBLIC_VERSION,
        workplace_active=active_record(),
        projects=tmp_path,
        live_state=live_state(project_id="working"),
    )
    assert status["project"]["current"]["project_id"] == "working"
    assert {item["project_id"] for item in status["project"]["projects"]} == {
        "finished",
        "working",
    }


def test_historical_project_decision_is_evidence_not_global_next() -> None:
    current_decision = {
        "id": "current-choice",
        "subject": "Choose the current direction",
        "status": "pending",
    }
    historical_decision = {
        "id": "old-choice",
        "subject": "Old unresolved choice",
        "status": "open",
    }
    inputs = healthy_inputs()
    inputs["projects"] = [
        project_record("finished", status="completed", decisions=[historical_decision]),
        project_record("working", decisions=[current_decision]),
    ]

    status = build_workplace_status(**inputs)

    assert status["decisions"]["next"]["id"] == "current-choice"
    by_id = {item["id"]: item for item in status["decisions"]["items"]}
    assert by_id["current-choice"] | {
        "project_id": "working",
        "project_status": "in_progress",
        "history_role": "current",
    } == by_id["current-choice"]
    assert by_id["old-choice"] | {
        "project_id": "finished",
        "project_status": "completed",
        "history_role": "historical",
    } == by_id["old-choice"]

    inputs["projects"] = [
        project_record("finished", status="completed", decisions=[historical_decision])
    ]
    inputs["live_state"] = live_state(project_id="finished")
    historical_only = build_workplace_status(**inputs)
    assert historical_only["decisions"]["next"] is None
    assert historical_only["decisions"]["items"][0]["history_role"] == "historical"
    assert "Next action\nNo action required" in render_workplace_status(historical_only)


def test_project_system_version_is_labelled_as_provenance() -> None:
    inputs = healthy_inputs()
    project = project_record("current-project")
    project["system_version"] = "0.1.0-alpha.7-project-origin"
    inputs["projects"] = [project]
    status = build_workplace_status(**inputs)

    assert (
        status["project"]["current"]["system_version"]
        == "0.1.0-alpha.7-project-origin"
    )
    assert (
        status["project"]["current"]["system_version_role"]
        == "project-provenance"
    )
    rendered = render_workplace_status(status)
    assert f"System {LIVE_VERSION} · active" in rendered
    assert "Project provenance System 0.1.0-alpha.7-project-origin" in rendered
    assert "Current state" in rendered
    assert "What you can do" in rendered
    assert "Next action" in rendered
    assert "Next action\nContinue current Project phase 2" in rendered
    assert status["decisions"]["next"] is None


def test_live_source_must_match_selected_current_pointer(tmp_path: Path) -> None:
    selected = tmp_path / "selected-active.json"
    selected.write_text(json.dumps(active_record()), encoding="utf-8")
    different = tmp_path / "different-active.json"
    different.write_text(json.dumps(active_record()), encoding="utf-8")
    live = live_state()
    live_system = live["system_version"]
    assert isinstance(live_system, dict)
    live_system["source_path"] = str(different)
    live_system["source_sha256"] = hashlib.sha256(different.read_bytes()).hexdigest()

    status = build_workplace_status(
        public_system=LIVE_VERSION,
        workplace_active=selected,
        projects=[project_record("current-project")],
        live_state=live,
    )

    assert status["runtime"]["status"] == "issue"
    assert any(
        "does not match the selected current pointer" in issue
        for issue in status["runtime"]["issues"]
    )


def test_decisions_details_and_json_are_stable_and_read_only(tmp_path: Path) -> None:
    decision = {
        "id": "decision-a",
        "subject": "Choose a direction",
        "status": "pending",
    }
    inputs = healthy_inputs()
    inputs["projects"] = [project_record("current-project", decisions=[decision])]
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    status = build_workplace_status(**inputs)
    default = render_workplace_status(status)
    detailed = render_workplace_status_details(status)
    encoded = render_workplace_status_json(status)
    assert "Fractal" in default
    assert f"System {LIVE_VERSION} · active" in default
    assert "Project current-project" in default
    assert "Needs your decision Choose a direction" in default
    assert "Evidence" in detailed
    assert "Choose a direction" in detailed
    assert json.loads(encoded) == json.loads(render_status_json(status))
    assert hashlib.sha256(encoded.encode()).hexdigest()
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after


def test_missing_live_state_is_separate_from_workplace_status() -> None:
    inputs = healthy_inputs()
    inputs.pop("live_state")
    status = build_workplace_status(**inputs)
    assert status["runtime"]["status"] == "issue"
    assert status["workplace"]["status"] == "healthy"
    assert "Runtime issue: Live runtime state is missing" in render_workplace_status(status)


def test_status_renderers_drop_nested_runtime_secrets_and_unknown_fields() -> None:
    inputs = healthy_inputs()
    live = inputs["live_state"]
    assert isinstance(live, dict)
    live["token"] = "SECRET_TOKEN"
    live["email"] = "person@example.com"
    live["nested"] = {
        "password": "PASSWORD_VALUE",
        "auth": {"secret": "AUTH_SECRET"},
    }
    live_project = live["project"]
    assert isinstance(live_project, dict)
    live_project["personal_payload"] = {"email": "private@example.com"}
    inputs["projects"] = [
        {
            **project_record("current-project"),
            "metadata": {"token": "PROJECT_TOKEN"},
        }
    ]
    status = build_workplace_status(**inputs)

    outputs = [
        json.dumps(status, ensure_ascii=False),
        render_workplace_status(status),
        render_workplace_status_details(status),
        render_workplace_status_json(status),
    ]
    encoded = "\n".join(outputs).casefold()
    for forbidden in (
        "secret_token",
        "person@example.com",
        "password_value",
        "auth_secret",
        "project_token",
        "private@example.com",
    ):
        assert forbidden.casefold() not in encoded
    assert set(status["runtime"]["state"]) <= {
        "record_type",
        "record_version",
        "state_sha256",
        "project",
        "system_version",
    }
    assert set(status["runtime"]["state"]["project"]) <= {
        "project_id",
        "revision",
        "status",
    }
