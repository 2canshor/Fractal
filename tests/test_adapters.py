from __future__ import annotations

import io
import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from fractal.adapter_hook import capture_work_completion, handle_hook
from fractal.adapter_hook import main as hook_main
from fractal.adapters import (
    AdapterBuilder,
    AdapterError,
    AdapterInstaller,
    audit_adapter,
    find_legacy_references,
    load_adapter_registry,
    parse_typed_tool_result,
    smoke_adapter,
    tree_manifest,
)
from fractal.component_governance import tree_sha256 as component_tree_sha256
from fractal.component_installation import (
    ClaudeComponentInstaller,
    CodexComponentInstaller,
    GeminiComponentInstaller,
)
from fractal.storage import value_sha256

ROOT = Path(__file__).parents[1]


def private_workspace(root: Path) -> Path:
    (root / "profile").mkdir(parents=True)
    (root / "policies").mkdir()
    (root / "projects" / "active" / "project-a").mkdir(parents=True)
    (root / "profile" / "current.json").write_text(
        json.dumps(
            {
                "communication": {"default_locale": "zh-Hant-HK"},
                "interaction": {"verify_at_user_experience_level": True},
            }
        )
    )
    (root / "policies" / "current.json").write_text(
        json.dumps(
            {
                "authorities": {
                    "project_completion": "primary-user-only",
                    "external_action": "explicit-scope-only",
                }
            }
        )
    )
    (root / "projects" / "active" / "project-a" / "record.json").write_text(
        json.dumps(
            {
                "project_id": "project-a",
                "status": "in_progress",
                "revision": 7,
                "plan": {"current_phase": 9},
            }
        )
    )
    return root


def builder(tmp_path: Path, output: str) -> AdapterBuilder:
    return AdapterBuilder(
        public_root=ROOT,
        private_root=private_workspace(tmp_path / f"private-{output}"),
        output_root=tmp_path / output,
        public_commit="a" * 40,
        private_commit="b" * 40,
        system_version="0.1.0-alpha.1",
        legacy_root=Path("/synthetic/legacy"),
        runtime_python=Path("/runtime with spaces/bin/python"),
    )


def governed_builder(tmp_path: Path, output: str) -> AdapterBuilder:
    private = private_workspace(tmp_path / f"private-{output}")
    registry_path = private / "system" / "components" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    research = ROOT / "capabilities" / "skills" / "research"
    projection_hash = value_sha256(tree_manifest(research))
    registry_path.write_text(
        json.dumps(
            {
                "record_type": "component-registry",
                "record_version": 2,
                "system_version": "0.1.0-alpha.2",
                "candidate_status": "candidate",
                "components": [
                    {
                        "component_id": "research",
                        "human_name": "Research",
                        "kind": "skill",
                        "disposition": "fractal-owned-canonical",
                        "external_identifier": None,
                        "dependencies": [],
                        "owner": {
                            "owner_id": "2canshor/fractal",
                            "source_controlled_by_owner": True,
                        },
                        "source": {
                            "kind": "fractal-public",
                            "locator": "capabilities/skills/research",
                            "version": "0.1.0",
                            "content_sha256": component_tree_sha256(research),
                        },
                        "naming": {
                            "registry_key_status": "passed",
                            "external_identifier_status": "not-applicable",
                            "exemption_reason": None,
                        },
                        "permissions": {
                            "profile": "read-only",
                            "operations": ["read"],
                            "secret_boundary": "no-secrets",
                        },
                        "trigger": {
                            "mode": "explicit",
                            "description": "Use for bounded research.",
                        },
                        "status": {
                            "discoverable": True,
                            "active": True,
                            "execution": "verified-staged",
                            "evidence_ids": ["test-evidence"],
                        },
                        "platforms": ["claude", "codex", "gemini"],
                        "projection": {
                            "mode": "generated-copy",
                            "target": "skills/research",
                            "expected_sha256": projection_hash,
                        },
                        "verification_evidence": ["test-evidence"],
                        "overlap": {"decision": "distinct", "with": []},
                        "recovery": {
                            "removal": "remove projection",
                            "restore": "rebuild projection",
                        },
                    }
                ],
            }
        )
    )
    return AdapterBuilder(
        public_root=ROOT,
        private_root=private,
        output_root=tmp_path / output,
        public_commit="a" * 40,
        private_commit="b" * 40,
        system_version="0.1.0-alpha.2",
        legacy_root=None,
        runtime_python=Path("/runtime/bin/python"),
        runtime_root=tmp_path / "runtime",
    )


def test_registry_keeps_platform_limitations_honest() -> None:
    registry = load_adapter_registry()
    assert [item["platform"] for item in registry["adapters"]] == [
        "claude",
        "codex",
        "cowork",
        "gemini",
    ]
    cowork = registry["adapters"][2]
    assert cowork["supported_surfaces"]["mcp"] == "server-side-unknown"
    assert any(item["status"] == "unknown" for item in cowork["limitations"])


def test_all_staging_homes_build_reproducibly_and_smoke(tmp_path: Path) -> None:
    first = builder(tmp_path, "first").build_all()
    builder(tmp_path, "second").build_all()
    assert [item["platform"] for item in first["adapters"]] == [
        "claude",
        "codex",
        "cowork",
        "gemini",
    ]
    for platform in ("claude", "codex", "cowork", "gemini"):
        first_tree = tree_manifest(tmp_path / "first" / platform)
        second_tree = tree_manifest(tmp_path / "second" / platform)
        assert first_tree == second_tree
        assert smoke_adapter(tmp_path / "first" / platform)["passed"] is True


def test_builder_refuses_existing_output_and_manifest_tamper(tmp_path: Path) -> None:
    adapter_builder = builder(tmp_path, "build")
    adapter_builder.build_all()
    with pytest.raises(AdapterError, match="already exists"):
        adapter_builder.build_all()
    context = tmp_path / "build" / "codex" / "fractal" / "context.json"
    context.write_text("{}")
    with pytest.raises(AdapterError, match="file drift"):
        smoke_adapter(tmp_path / "build" / "codex")


def test_metadata_first_projection_and_platform_specific_outputs(tmp_path: Path) -> None:
    builder(tmp_path, "build").build_all()
    codex = tmp_path / "build" / "codex"
    metadata = json.loads((codex / "fractal" / "capability-metadata.json").read_text())
    assert metadata[0]["description"]
    assert (codex / "AGENTS.md").is_file()
    assert (codex / "hooks.json").is_file()
    hooks = json.loads((codex / "hooks.json").read_text())
    command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command.startswith("'/runtime with spaces/bin/python'")
    agent = tomllib.loads((codex / "agents" / "fractal-verifier.toml").read_text())
    assert agent["name"] == "fractal_verifier"
    assert agent["sandbox_mode"] == "read-only"
    assert "Do not edit" in agent["developer_instructions"]
    researcher = tomllib.loads((codex / "agents" / "improvement-researcher.toml").read_text())
    assert researcher["name"] == "improvement_researcher"
    assert researcher["sandbox_mode"] == "read-only"
    claude = tmp_path / "build" / "claude"
    assert (claude / "settings.fragment.json").is_file()
    claude_agent = (claude / "agents" / "fractal-verifier.md").read_text()
    assert claude_agent.startswith("---\nname: fractal-verifier\n")
    assert "description: Fresh-context acceptance checker" in claude_agent
    assert "tools: Read, Grep, Glob, Bash" in claude_agent
    assert "permissionMode: plan" in claude_agent
    assert "Do not edit" in claude_agent
    assert (claude / "agents" / "improvement-researcher.md").is_file()
    assert list((tmp_path / "build" / "cowork" / "skill-packages").glob("*.skill"))
    gemini = tmp_path / "build" / "gemini"
    assert not (gemini / "skills").exists()
    assert list((gemini / "config" / "skills").glob("*/SKILL.md"))
    assert "~/.gemini/fractal/context.json" in (gemini / "GEMINI.md").read_text()
    gemini_metadata = json.loads((gemini / "fractal" / "capability-metadata.json").read_text())
    assert {item["activation"] for item in gemini_metadata} == {"active-when-adapter-is-installed"}
    assert "Stop" in hooks["hooks"]
    assert "--event work-completed" in hooks["hooks"]["Stop"][0]["hooks"][0]["command"]


def test_session_hook_and_protected_legacy_guard() -> None:
    context = {
        "system_version": "0.1.0-alpha.1",
        "active_project": {
            "project_id": "project-a",
            "status": "in_progress",
            "revision": 7,
            "current_phase": 9,
        },
        "protected_legacy_roots": ["/synthetic/legacy"],
        "authority": {"legacy_removal_enabled": False},
        "component_governance": {"managed_roots": ["~/.codex/skills"]},
    }
    session = handle_hook("session-start", context, {"source": "startup"})
    session_context = session["hookSpecificOutput"]["additionalContext"]
    assert "project-a" in session_context
    assert "Legacy removal is disabled with 1 protected legacy roots" in session_context
    blocked = handle_hook(
        "pre-tool-use",
        context,
        {"tool_input": {"command": "rm -rf /synthetic/legacy"}},
    )
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = handle_hook(
        "pre-tool-use",
        context,
        {"tool_input": {"command": "rg pattern /synthetic/legacy"}},
    )
    assert "permissionDecision" not in allowed["hookSpecificOutput"]
    exact_candidate_path = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_input": {
                "command": (
                    "'/opt/Fractal Candidate/runtime/"
                    "system/0.1.0-alpha.2/venv/bin/fractal' components "
                    "install-candidate --home ~/.codex"
                )
            }
        },
    )
    assert "permissionDecision" not in exact_candidate_path["hookSpecificOutput"]


def test_direct_component_install_is_denied_outside_governed_route() -> None:
    context = {
        "system_version": "0.1.0-alpha.2",
        "active_project": {
            "project_id": "project-a",
            "status": "in_progress",
            "revision": 8,
            "current_phase": 9,
        },
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": ["~/.codex/skills"]},
    }
    denied = handle_hook(
        "pre-tool-use",
        context,
        {"tool_input": {"command": "cp -R candidate ~/.codex/skills/new-skill"}},
    )
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_input": {
                "command": "fractal components install-candidate --manifest candidate.json"
            }
        },
    )
    assert "permissionDecision" not in allowed["hookSpecificOutput"]


def test_real_stop_payload_captures_and_evaluates_work_signature(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "exec_command"},
            }
        )
        + "\n"
    )
    context = {
        "platform": "codex",
        "active_project": {"project_id": "project-a"},
    }
    payload = {
        "session_id": "session-a",
        "transcript_path": str(transcript),
        "last_assistant_message": "Finished the bounded probe.",
    }
    journal = tmp_path / "work-signatures.jsonl"
    evaluations = tmp_path / "work-signature-evaluations.jsonl"
    first = capture_work_completion(
        context,
        payload,
        journal_path=journal,
        evaluations_path=evaluations,
    )
    assert "first-occurrence" in first["hookSpecificOutput"]["additionalContext"]
    signature = json.loads(journal.read_text().strip())
    assert signature["tools"] == ["exec_command"]
    assert signature["project_id"] == "project-a"
    second = capture_work_completion(
        context,
        {**payload, "session_id": "session-b"},
        journal_path=journal,
        evaluations_path=evaluations,
    )
    assert "possible-repetition" in second["hookSpecificOutput"]["additionalContext"]
    assert len(journal.read_text().splitlines()) == 2


def test_final_cutover_context_removes_the_legacy_guard(tmp_path: Path) -> None:
    adapter_builder = builder(tmp_path, "final-cutover")
    adapter_builder.legacy_root = None
    adapter_builder.build_all()
    context = json.loads(
        (tmp_path / "final-cutover" / "codex" / "fractal" / "context.json").read_text()
    )
    assert context["protected_legacy_roots"] == []
    assert context["authority"]["legacy_removal_enabled"] is True
    session = handle_hook("session-start", context, {"source": "startup"})
    assert (
        "Legacy removal is enabled with 0 protected legacy roots"
        in session["hookSpecificOutput"]["additionalContext"]
    )


def test_hook_cli_expands_home_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    context_path = home / ".codex" / "fractal" / "context.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(
        json.dumps(
            {
                "system_version": "0.1.0-alpha.1",
                "active_project": {
                    "project_id": "project-a",
                    "status": "in_progress",
                    "revision": 7,
                    "current_phase": 10,
                },
                "protected_legacy_roots": ["/synthetic/legacy"],
                "authority": {"legacy_removal_enabled": False},
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"source": "startup"})))
    assert (
        hook_main(
            [
                "--event",
                "session-start",
                "--context",
                "~/.codex/fractal/context.json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert "project-a" in result["hookSpecificOutput"]["additionalContext"]


def test_typed_multi_block_results_preserve_partial_failure() -> None:
    blocks = [
        {"type": "text", "text": "one"},
        {"type": "image", "data": "reference"},
        {"type": "structured", "value": {"count": 2}},
        {"type": "warning", "message": "stale"},
        {"type": "error", "message": "one source failed"},
    ]
    result = parse_typed_tool_result(blocks)
    assert result["status"] == "partial-failure"
    assert [item["index"] for item in result["blocks"]] == list(range(5))
    assert result["blocks"][2]["content"]["value"]["count"] == 2
    unknown = parse_typed_tool_result([{"type": "future-block", "raw": "ref"}])
    assert unknown["status"] == "unverified"
    assert unknown["blocks"][0]["content"]["raw"] == "ref"


def test_drift_and_stale_reference_audits_are_deterministic(tmp_path: Path) -> None:
    builder(tmp_path, "build").build_all()
    expected = tmp_path / "build" / "codex"
    installed = tmp_path / "installed"
    shutil.copytree(expected, installed)
    assert audit_adapter(expected, installed)["clean"] is True
    (installed / "AGENTS.md").write_text("old rulebook")
    (installed / "unexpected.txt").write_text("extra")
    audit = audit_adapter(expected, installed)
    assert audit["changed"] == ["AGENTS.md"]
    assert audit["unexpected"] == ["unexpected.txt"]
    managed_only = audit_adapter(expected, installed, include_unexpected=False)
    assert managed_only["unexpected"] == []
    legacy = tmp_path / "legacy.md"
    legacy.write_text("Load /legacy/rules and Mega Rulebook")
    findings = find_legacy_references([legacy], ["/legacy/", "Mega Rulebook"])
    assert [item["marker"] for item in findings] == ["/legacy/", "Mega Rulebook"]


def test_staging_install_audit_and_restore_preserve_previous_files(tmp_path: Path) -> None:
    builder(tmp_path, "build").build_all()
    built = tmp_path / "build" / "codex"
    home = tmp_path / "home"
    home.mkdir()
    (home / "AGENTS.md").write_text("legacy entrypoint")
    installer = AdapterInstaller(tmp_path / "install-state")
    record = installer.install(built, home)
    assert (home / "AGENTS.md").read_text().startswith("# Fractal Router")
    assert audit_adapter(built, home)["clean"] is True
    restored = installer.restore(record["install_id"])
    assert "AGENTS.md" in restored["restored"]
    assert (home / "AGENTS.md").read_text() == "legacy entrypoint"
    assert not (home / "fractal" / "context.json").exists()


def test_governed_component_install_quarantines_and_restores_extras(
    tmp_path: Path,
) -> None:
    governed_builder(tmp_path, "governed").build_all()
    built = tmp_path / "governed" / "codex"
    home = tmp_path / "home"
    (home / "skills" / "fable-loop").mkdir(parents=True)
    (home / "skills" / "fable-loop" / "SKILL.md").write_text("legacy extra")
    (home / "AGENTS.md").write_text("previous entrypoint")
    installer = CodexComponentInstaller(tmp_path / "component-installs", tmp_path / "quarantine")
    record = installer.install(built, home)
    assert (home / "AGENTS.md").is_symlink()
    assert (home / "skills" / "research").is_symlink()
    assert not (home / "skills" / "fable-loop").exists()
    assert record["persistent_system_version_activated"] is False
    restored = installer.restore(record["install_id"])
    assert (home / "AGENTS.md").read_text() == "previous entrypoint"
    assert (home / "skills" / "fable-loop" / "SKILL.md").read_text() == "legacy extra"
    assert "skills/fable-loop" in restored["restored_quarantine"]


def test_codex_mcp_activation_projection_preserves_config_and_secrets() -> None:
    config = """theme = \"dark\"

[mcp_servers.node_repl]
command = \"node\"

[mcp_servers.firecrawl]
enabled = true
command = \"npx\"

[mcp_servers.firecrawl.env]
FIRECRAWL_API_KEY = \"secret-value\"

[mcp_servers.higgsfield]
url = \"https://example.invalid/mcp\"
"""
    registry = {
        "components": [
            {
                "kind": "mcp",
                "platforms": ["codex"],
                "source": {
                    "locator": "~/.codex/config.toml#mcp_servers.node_repl"
                },
                "status": {"active": True},
            },
            {
                "kind": "mcp",
                "platforms": ["codex"],
                "source": {
                    "locator": "~/.codex/config.toml#mcp_servers.firecrawl"
                },
                "status": {"active": True},
            },
            {
                "kind": "mcp",
                "platforms": ["codex"],
                "source": {
                    "locator": "~/.codex/config.toml#mcp_servers.higgsfield"
                },
                "status": {"active": False},
            },
        ]
    }
    projected = CodexComponentInstaller._project_mcp_activation(config, registry)
    assert "[mcp_servers.node_repl]\nenabled = true\ncommand" in projected
    assert "[mcp_servers.firecrawl]\nenabled = true\ncommand" in projected
    assert "[mcp_servers.higgsfield]\nenabled = false\nurl" in projected
    assert 'FIRECRAWL_API_KEY = "secret-value"' in projected


def test_claude_component_install_merges_settings_and_restores_extras(
    tmp_path: Path,
) -> None:
    governed_builder(tmp_path, "governed-claude").build_all()
    built = tmp_path / "governed-claude" / "claude"
    home = tmp_path / "claude-home"
    (home / "skills" / "legacy-extra").mkdir(parents=True)
    (home / "skills" / "legacy-extra" / "SKILL.md").write_text("legacy extra")
    (home / "CLAUDE.md").write_text("previous entrypoint")
    (home / "settings.json").write_text(json.dumps({"theme": "dark", "enabledPlugins": {}}))
    installer = ClaudeComponentInstaller(tmp_path / "component-installs", tmp_path / "quarantine")
    record = installer.install(built, home)
    installed_settings = json.loads((home / "settings.json").read_text())
    assert (home / "CLAUDE.md").is_symlink()
    assert (home / "skills" / "research").is_symlink()
    assert not (home / "skills" / "legacy-extra").exists()
    assert installed_settings["theme"] == "dark"
    assert set(installed_settings["hooks"]) == {"PreToolUse", "SessionStart", "Stop"}
    assert record["persistent_system_version_activated"] is False
    restored = installer.restore(record["install_id"])
    assert (home / "CLAUDE.md").read_text() == "previous entrypoint"
    assert (home / "skills" / "legacy-extra" / "SKILL.md").read_text() == "legacy extra"
    assert "skills/legacy-extra" in restored["restored_quarantine"]


def test_gemini_component_install_switches_and_restores_skills(tmp_path: Path) -> None:
    governed_builder(tmp_path, "governed-gemini").build_all()
    built = tmp_path / "governed-gemini" / "gemini"
    home = tmp_path / "gemini-home"
    (home / "config" / "skills" / "legacy-extra").mkdir(parents=True)
    (home / "config" / "skills" / "legacy-extra" / "SKILL.md").write_text(
        "legacy extra"
    )
    (home / "GEMINI.md").write_text("previous entrypoint")
    installer = GeminiComponentInstaller(
        tmp_path / "component-installs", tmp_path / "quarantine"
    )
    record = installer.install(built, home)
    assert (home / "GEMINI.md").is_symlink()
    assert (home / "config" / "skills" / "research").is_symlink()
    assert not (home / "config" / "skills" / "legacy-extra").exists()
    assert record["persistent_system_version_activated"] is False
    restored = installer.restore(record["install_id"])
    assert (home / "GEMINI.md").read_text() == "previous entrypoint"
    assert (
        home / "config" / "skills" / "legacy-extra" / "SKILL.md"
    ).read_text() == "legacy extra"
    assert "config/skills/legacy-extra" in restored["restored_quarantine"]
