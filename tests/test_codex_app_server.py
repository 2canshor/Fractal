from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fractal.codex_app_server import (
    CodexAppServerError,
    apply_codex_config_transaction,
    audit_agents_hierarchy,
    audit_codex_config_projection,
    detect_legacy_review_inputs,
    reconcile_codex_components,
    resolve_agents_hierarchy,
    verify_live_turn_completion,
)


class FakeClient:
    def __init__(
        self,
        responses: dict[str, dict[str, Any] | list[dict[str, Any]]],
        notifications: list[dict[str, Any]] | None = None,
    ) -> None:
        self.responses = responses
        self.notifications = notifications or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.on_notification: Any = None

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        response = self.responses[method]
        if isinstance(response, list):
            return response.pop(0)
        return response

    def wait_for_notification(
        self,
        methods: set[str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        del timeout
        for index, message in enumerate(self.notifications):
            if message["method"] in methods:
                selected = self.notifications.pop(index)
                if self.on_notification is not None:
                    self.on_notification(selected)
                return selected
        raise CodexAppServerError("No matching fake notification")


def governed_component(
    component_id: str,
    kind: str,
    *,
    external: str | None = None,
    source: str = "source",
    target: str | None = None,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "kind": kind,
        "external_identifier": external,
        "source": {"locator": source},
        "projection": {"target": target},
        "status": {"active": True, "execution": "available-unverified"},
        "platforms": ["codex"],
    }


def test_live_reconciliation_keeps_loaded_callable_and_success_separate(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    skill_path = codex_home / "skills" / "research" / "SKILL.md"
    registry = {
        "system_version": "candidate",
        "components": [
            governed_component("adapter-codex", "adapter", target="AGENTS.md"),
            governed_component(
                "app-figma", "app", external="connector-figma", target="app:figma"
            ),
            governed_component(
                "hook-stop", "hook", external="Stop", target="hooks.json"
            ),
            governed_component("mcp-test", "mcp", external="server-a"),
            governed_component(
                "research", "skill", source="capabilities/research", target="skills/research"
            ),
            governed_component(
                "tool-mcp-test-read", "tool", external="mcp__server_a__read"
            ),
        ],
    }
    client = FakeClient(
        {
            "skills/list": {
                "data": [
                    {
                        "cwd": str(tmp_path),
                        "errors": [],
                        "skills": [
                            {
                                "name": "research",
                                "path": str(skill_path),
                                "enabled": True,
                                "scope": "user",
                            }
                        ],
                    }
                ]
            },
            "hooks/list": {
                "data": [
                    {
                        "cwd": str(tmp_path),
                        "errors": [],
                        "warnings": [],
                        "hooks": [
                            {
                                "key": "stop-a",
                                "eventName": "stop",
                                "sourcePath": str(codex_home / "hooks.json"),
                                "enabled": True,
                                "trustStatus": "trusted",
                            }
                        ],
                    }
                ]
            },
            "mcpServerStatus/list": {
                "data": [
                    {
                        "name": "server-a",
                        "authStatus": "unsupported",
                        "tools": {"read": {"name": "read"}},
                        "resources": [],
                        "resourceTemplates": [],
                    }
                ],
                "nextCursor": None,
            },
            "app/installed": {
                "apps": [
                    {
                        "id": "connector-figma",
                        "runtimeName": "Figma",
                        "enabled": True,
                        "callable": True,
                    }
                ]
            },
            "plugin/installed": {"marketplaces": [], "marketplaceLoadErrors": []},
            "config/read": {"config": {"mcp_servers": {}}, "origins": {}},
            "thread/start": {"instructionSources": [str(codex_home / "AGENTS.md")]},
        }
    )
    report = reconcile_codex_components(
        registry,
        client,  # type: ignore[arg-type]
        cwd=tmp_path,
        codex_home=codex_home,
    )
    assert report["clean"] is True
    assert report["summary"] == {
        "registered_active": 6,
        "observed_by_runtime": 6,
        "loaded": 6,
        "callable": 6,
        "successful_execution": 0,
        "not_observable_by_current_api": 0,
    }
    assert "does not prove" in report["claim_boundary"]


def test_agents_precedence_limit_and_live_sources_are_checked(tmp_path: Path) -> None:
    codex_home = tmp_path / "home" / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "AGENTS.md").write_text("global")
    (codex_home / "AGENTS.override.md").write_text("override")
    project = tmp_path / "project"
    child = project / "child"
    (project / ".git").mkdir(parents=True)
    child.mkdir()
    (project / "AGENTS.md").write_text("project")
    (child / "AGENTS.override.md").write_text("child override")
    resolved = resolve_agents_hierarchy(
        cwd=child,
        codex_home=codex_home,
        maximum_bytes=16,
    )
    assert [Path(item["path"]).name for item in resolved["selected_sources"]] == [
        "AGENTS.override.md",
        "AGENTS.md",
        "AGENTS.override.md",
    ]
    assert resolved["truncated"] is True

    full = resolve_agents_hierarchy(cwd=child, codex_home=codex_home)
    paths = [item["path"] for item in full["selected_sources"]]
    client = FakeClient({"thread/start": {"instructionSources": paths}})
    audited = audit_agents_hierarchy(
        client,  # type: ignore[arg-type]
        cwd=child,
        codex_home=codex_home,
    )
    assert audited["sources_match"] is True


def test_external_detection_only_creates_review_inputs() -> None:
    client = FakeClient(
        {
            "externalAgentConfig/detect": {
                "items": [
                    {
                        "itemType": "SKILLS",
                        "description": "External skills",
                        "cwd": None,
                        "details": {"skills": [{"name": "example"}]},
                    }
                ]
            }
        }
    )
    report = detect_legacy_review_inputs(client, cwds=[Path("/tmp")])  # type: ignore[arg-type]
    assert report["items"][0]["route"] == "legacy-material-review"
    assert report["import_called"] is False
    assert [method for method, _ in client.calls] == ["externalAgentConfig/detect"]


def test_config_batch_readback_and_private_recovery(tmp_path: Path) -> None:
    before = {
        "config": {"features": {"hooks": False}},
        "layers": [{"name": {"type": "user"}, "version": "before-v1"}],
    }
    after = {
        "config": {"features": {"hooks": True}},
        "layers": [{"name": {"type": "user"}, "version": "after-v2"}],
    }
    client = FakeClient(
        {
            "config/read": [before, after],
            "configRequirements/read": {"requirements": None},
            "config/batchWrite": {
                "status": "ok",
                "version": "after-v2",
                "filePath": str(tmp_path / "config.toml"),
            },
        }
    )
    recovery = tmp_path / "private" / "restore.json"
    report = apply_codex_config_transaction(
        client,  # type: ignore[arg-type]
        edits=[{"keyPath": "features.hooks", "value": True, "mergeStrategy": "replace"}],
        recovery_path=recovery,
        cwd=tmp_path,
    )
    assert report["status"] == "verified"
    assert recovery.stat().st_mode & 0o777 == 0o600
    write = next(params for method, params in client.calls if method == "config/batchWrite")
    assert write["expectedVersion"] == "before-v1"


def test_config_transaction_handles_quoted_hook_state_keys(tmp_path: Path) -> None:
    hook_key = "/tmp/hooks.json:stop:0:0"
    key_path = f'hooks.state."{hook_key}".trusted_hash'
    before = {
        "config": {"hooks": {"state": {hook_key: {"trusted_hash": "old"}}}},
        "layers": [{"name": {"type": "user"}, "version": "v1"}],
    }
    after = {
        "config": {"hooks": {"state": {hook_key: {"trusted_hash": "new"}}}},
        "layers": [{"name": {"type": "user"}, "version": "v2"}],
    }
    client = FakeClient(
        {
            "config/read": [before, after],
            "configRequirements/read": {"requirements": None},
            "config/batchWrite": {"status": "ok", "version": "v2", "filePath": "config"},
        }
    )
    report = apply_codex_config_transaction(
        client,  # type: ignore[arg-type]
        edits=[{"keyPath": key_path, "value": "new", "mergeStrategy": "replace"}],
        recovery_path=tmp_path / "restore.json",
        cwd=tmp_path,
    )
    assert report["status"] == "verified"
    recovery = json.loads((tmp_path / "restore.json").read_text())
    assert recovery["before_values"][key_path] == "old"


def test_config_projection_audit_exposes_only_activation_flags() -> None:
    registry = {
        "components": [
            governed_component(
                "mcp-a",
                "mcp",
                external="server-a",
                source="~/.codex/config.toml#mcp_servers.server-a",
            ),
            {
                **governed_component(
                    "mcp-b",
                    "mcp",
                    external="server-b",
                    source="~/.codex/config.toml#mcp_servers.server-b",
                ),
                "status": {"active": False, "execution": "unavailable"},
            },
        ]
    }
    client = FakeClient(
        {
            "config/read": {
                "config": {
                    "mcp_servers": {
                        "server-a": {"enabled": True, "env": {"TOKEN": "secret"}},
                        "server-b": {"enabled": False},
                    }
                },
                "layers": [{"name": {"type": "user"}, "version": "v1"}],
                "origins": {},
            },
            "configRequirements/read": {"requirements": None},
        }
    )
    report = audit_codex_config_projection(
        client, registry, cwd=Path("/tmp")  # type: ignore[arg-type]
    )
    assert report["clean"] is True
    assert report["actual_mcp_activation"] == {"server-a": True, "server-b": False}
    serialized = json.dumps(report)
    assert "TOKEN" not in serialized
    assert '"env"' not in serialized


def test_turn_events_finalize_one_signature_and_run_fatigue(tmp_path: Path) -> None:
    thread_id = "thread-a"
    turn_id = "turn-a"
    client = FakeClient(
        {
            "thread/start": {"thread": {"id": thread_id}},
            "turn/start": {"turn": {"id": turn_id}},
        },
        notifications=[
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "tokenUsage": {"last": {"totalTokens": 30}},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "commandExecution",
                        "status": "completed",
                        "exitCode": 0,
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "completed"},
                },
            },
        ],
    )
    journal = tmp_path / "signatures.jsonl"
    evaluations = tmp_path / "evaluations.jsonl"
    evaluations.write_text(
        json.dumps(
            {
                "record_type": "work-signature-evaluation",
                "work_id": f"codex-turn-{thread_id}-{turn_id}",
                "project_id": "project-a",
                "platform": "codex",
                "captured": True,
                "recognition": {"status": "first-occurrence"},
                "evaluated_at": "2026-08-22T00:00:00Z",
            }
        )
        + "\n"
    )

    def simulate_live_stop_hook(message: dict[str, Any]) -> None:
        if message["method"] != "turn/completed":
            return
        journal.write_text(
            json.dumps(
                {
                    "work_id": f"codex-turn-{thread_id}-{turn_id}",
                    "project_id": "project-a",
                    "work_type": "agent-turn",
                    "input_shape": "codex-completed-turn",
                    "steps": ["user-turn", "assistant-response"],
                    "tools": [],
                    "outcome_category": "completed-response",
                    "purpose_class": "ordinary",
                    "elapsed_seconds": None,
                    "token_usage": None,
                    "completed_at": "2026-08-22T00:00:00Z",
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "tool_evidence": [],
                    "evidence_state": "stop-captured",
                }
            )
            + "\n"
        )

    client.on_notification = simulate_live_stop_hook
    report = verify_live_turn_completion(
        client,  # type: ignore[arg-type]
        cwd=tmp_path,
        project_id="project-a",
        journal_path=journal,
        evaluations_path=evaluations,
    )
    assert report["status"] == "completed"
    assert report["work_signature"]["token_usage"] == 30
    assert report["work_signature"]["evidence_state"] == "turn-completed"
    assert len(journal.read_text().splitlines()) == 1
    assert len(evaluations.read_text().splitlines()) == 1
    final_evaluation = json.loads(evaluations.read_text())
    assert final_evaluation["recognition"]["status"] == (
        "first-occurrence"
    )
    assert final_evaluation["event_evidence"][0] == "turn/completed"


def test_config_mismatch_uses_returned_version_to_restore(tmp_path: Path) -> None:
    before = {
        "config": {"model": "a"},
        "layers": [{"name": {"type": "user"}, "version": "v1"}],
    }
    mismatch = {
        "config": {"model": "unexpected"},
        "layers": [{"name": {"type": "user"}, "version": "v2"}],
    }
    client = FakeClient(
        {
            "config/read": [before, mismatch],
            "configRequirements/read": {"requirements": None},
            "config/batchWrite": [
                {"status": "ok", "version": "v2", "filePath": "config.toml"},
                {"status": "ok", "version": "v3", "filePath": "config.toml"},
            ],
        }
    )
    with pytest.raises(CodexAppServerError, match="restored"):
        apply_codex_config_transaction(
            client,  # type: ignore[arg-type]
            edits=[{"keyPath": "model", "value": "b", "mergeStrategy": "replace"}],
            recovery_path=tmp_path / "restore.json",
            cwd=tmp_path,
        )
    writes = [params for method, params in client.calls if method == "config/batchWrite"]
    assert writes[1]["expectedVersion"] == "v2"
    assert writes[1]["edits"][0]["value"] == "a"
