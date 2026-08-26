from __future__ import annotations

import io
import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

import fractal.adapter_hook as adapter_hook
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
from fractal.models import ProjectRecord
from fractal.storage import ProjectStore, value_sha256
from fractal.user_surface import build_user_surface

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


def test_adapter_selects_unique_non_completed_project_without_filesystem_order(
    tmp_path: Path,
) -> None:
    private = private_workspace(tmp_path / "private-project-selection")
    old = private / "projects" / "active" / "aaa-completed"
    old.mkdir(parents=True)
    (old / "record.json").write_text(
        json.dumps(
            {
                "project_id": "aaa-completed",
                "status": "completed",
                "revision": 99,
                "plan": {"current_phase": 11},
            }
        )
    )
    selected = AdapterBuilder(
        public_root=ROOT,
        private_root=private,
        output_root=tmp_path / "project-selection-output",
        public_commit="a" * 40,
        private_commit="b" * 40,
        system_version="0.1.0-alpha.8-test",
        legacy_root=None,
        verify_source_commits=False,
    )._select_project_snapshot()
    assert selected["project_id"] == "project-a"


def test_adapter_rejects_multiple_non_completed_projects(tmp_path: Path) -> None:
    private = private_workspace(tmp_path / "private-project-conflict")
    second = private / "projects" / "active" / "project-b"
    second.mkdir(parents=True)
    (second / "record.json").write_text(
        json.dumps(
            {
                "project_id": "project-b",
                "status": "awaiting_completion",
                "revision": 4,
                "plan": {"current_phase": 2},
            }
        )
    )
    builder = AdapterBuilder(
        public_root=ROOT,
        private_root=private,
        output_root=tmp_path / "project-conflict-output",
        public_commit="a" * 40,
        private_commit="b" * 40,
        system_version="0.1.0-alpha.8-test",
        legacy_root=None,
        verify_source_commits=False,
    )
    with pytest.raises(AdapterError, match="multiple current Projects"):
        builder._select_project_snapshot()


def test_adapter_rejects_missing_registered_canonical_source(tmp_path: Path) -> None:
    private = private_workspace(tmp_path / "private-missing-source")
    registry_path = private / "system" / "components" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "record_type": "component-registry",
                "record_version": 3,
                "system_version": "0.1.0-alpha.8-r1",
                "candidate_status": "candidate",
                "components": [
                    {
                        "component_id": "missing-canonical-source",
                        "human_name": "Missing Canonical Source",
                        "kind": "platform-capability",
                        "disposition": "fractal-owned-canonical",
                        "external_identifier": None,
                        "dependencies": [],
                        "owner": {
                            "owner_id": "2canshor/fractal",
                            "source_controlled_by_owner": True,
                        },
                        "source": {
                            "kind": "fractal-public",
                            "locator": "missing.py",
                            "version": "0.1.0-alpha.8-r1",
                            "content_sha256": "a" * 64,
                        },
                        "naming": {
                            "registry_key_status": "passed",
                            "external_identifier_status": "not-applicable",
                            "exemption_reason": None,
                        },
                        "permissions": {
                            "profile": "test",
                            "operations": ["test"],
                            "secret_boundary": "none",
                        },
                        "trigger": {"mode": "explicit", "description": "test"},
                        "invocation": {
                            "automatic_matching": False,
                            "explicit_invocation": True,
                        },
                        "surface_audience": "supporting-capability",
                        "job_contract": None,
                        "status": {
                            "discoverable": True,
                            "active": True,
                            "execution": "verified-staged",
                            "evidence_ids": ["test"],
                            "claim_receipt": None,
                        },
                        "platforms": ["codex"],
                        "projection": {
                            "mode": "platform-reference",
                            "target": "missing.py",
                            "expected_sha256": "a" * 64,
                        },
                        "overlap": {"decision": "none", "with": []},
                        "recovery": {"removal": "remove", "restore": "restore"},
                        "verification_evidence": ["test"],
                    }
                ],
            }
        )
    )
    with pytest.raises(AdapterError, match="canonical source is missing"):
        AdapterBuilder(
            public_root=ROOT,
            private_root=private,
            output_root=tmp_path / "missing-source-output",
            public_commit="a" * 40,
            private_commit="b" * 40,
            system_version="0.1.0-alpha.8-r1-test",
            legacy_root=None,
            verify_source_commits=False,
        )


def add_claude_model_route(root: Path) -> None:
    route = root / "adapters" / "claude" / "model-route.json"
    route.parent.mkdir(parents=True)
    route.write_text(
        json.dumps(
            {
                "gateway": {
                    "api_format": "anthropic-messages",
                    "base_url": "http://gateway.test:8000",
                    "component_id": "ollama-gateway",
                    "models": ["gemma4:12b", "qwen3.5:9b"],
                    "version": "0.32.14",
                },
                "model": "sonnet",
                "available_models": ["sonnet"],
                "enforce_available_models": True,
                "model_overrides": {"claude-sonnet-5": "gemma4:12b"},
                "environment": {
                    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "65536",
                    "CLAUDE_CODE_SUBAGENT_MODEL": "inherit",
                },
                "platform": "claude-code",
                "platform_version": "2.1.237",
                "record_type": "claude-model-route",
                "record_version": 1,
                "remove_environment": ["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"],
            }
        )
    )


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
        verify_source_commits=False,
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
        verify_source_commits=False,
    )


def surface_governed_builder(tmp_path: Path, output: str) -> AdapterBuilder:
    base = governed_builder(tmp_path, f"{output}-seed")
    private = base.private_root
    registry_path = private / "system" / "components" / "registry.json"
    registry = json.loads(registry_path.read_text())
    research = registry["components"][0]
    clarification_source = ROOT / "capabilities" / "skills" / "clarification"
    clarification = json.loads(json.dumps(research))
    clarification["component_id"] = "clarification"
    clarification["human_name"] = "Clarification"
    clarification["source"]["locator"] = "capabilities/skills/clarification"
    clarification["source"]["content_sha256"] = component_tree_sha256(clarification_source)
    clarification["projection"]["target"] = "skills/clarification"
    clarification["projection"]["expected_sha256"] = value_sha256(
        tree_manifest(clarification_source)
    )
    registry["components"] = [clarification, research]
    registry_path.write_text(json.dumps(registry))
    policy_path = private / "system" / "components" / "user-surface-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "record_type": "user-surface-policy",
                "record_version": 1,
                "platform": "codex",
                "action_resolution": {
                    "feature_name": "Object-Aware Actions",
                    "technical_id": "object-aware-workflow-routing",
                    "selection_rule": (
                        "The object named after an Action selects the narrowest matching workflow."
                    ),
                    "route_states": ["exact", "partial", "missing", "unavailable"],
                },
                "entries": [
                    {
                        "entry_id": "research",
                        "interface_type": "action",
                        "component_id": "research",
                        "outcome": "Answer one question with verified evidence.",
                    }
                ],
                "dot_groups": [
                    {
                        "group_id": "decision",
                        "purpose": "Resolve consequential unknowns.",
                        "component_ids": ["clarification"],
                    }
                ],
                "workflows": [
                    {
                        "workflow_id": "research-decision",
                        "entry_id": "research",
                        "user_job": "Research a question with a consequential unknown.",
                        "positive_examples": ["Research the decision."],
                        "negative_examples": ["Change the decision."],
                        "dot_group_ids": ["decision"],
                        "completion": "Evidence and the unknown are resolved.",
                        "authority_boundary": "Read only.",
                    }
                ],
                "recovery": {
                    "disable_method": "Disable selector only.",
                    "restore_method": "Restore selector config.",
                },
            }
        )
    )
    build_user_surface(
        policy_path,
        json.loads(registry_path.read_text()),
        private / "system" / "components" / "user-surface.json",
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
        verify_source_commits=False,
    )


def test_builder_rejects_a_false_source_commit(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="Public adapter source commit does not match"):
        AdapterBuilder(
            public_root=ROOT,
            private_root=private_workspace(tmp_path / "private-false-commit"),
            output_root=tmp_path / "false-commit",
            public_commit="0" * 40,
            private_commit="1" * 40,
            system_version="0.1.0-alpha.2",
            legacy_root=None,
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
    for result in first["adapters"]:
        assert result["contract_receipt"] == {
            "built": True,
            "staged_smoke_passed": True,
            "exact_live_boundary_smoke_passed": False,
            "representative_real_task_passed": False,
            "installed": False,
            "loaded": False,
            "active": False,
            "callable": False,
            "actual_user_outcome_observed": False,
            "claim_boundary": (
                "Build and staged smoke evidence do not prove an exact live boundary, "
                "a representative real task, or an actual user outcome."
            ),
        }
        manifest = json.loads(
            (
                tmp_path / "first" / result["platform"] / "fractal" / "adapter-manifest.json"
            ).read_text()
        )
        boundary = manifest["boundary_contract"]
        assert boundary["live_promotion_eligible"] is False
        assert boundary["exact_live_boundary_smoke"] == "not-run"
        assert boundary["facts"]["runtime_interpreter"]["provenance"] == "observed"
        assert boundary["facts"]["path_expansion"]["provenance"] == "inferred"


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


def test_component_management_tool_cannot_bypass_fractal_route() -> None:
    context = {
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": False},
        "component_governance": {"managed_roots": []},
    }
    result = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "mcp__codex_apps__plugin_management_uninstall_app",
            "tool_input": {"plugin_id": "example"},
        },
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    ordinary = handle_hook(
        "pre-tool-use",
        context,
        {"tool_name": "mcp__github__fetch_file", "tool_input": {"path": "README.md"}},
    )
    assert "permissionDecision" not in ordinary["hookSpecificOutput"]


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git send-pack origin refs/heads/main",
        "gh api repos/2canshor/Fractal/git/refs/heads/main --method PATCH",
        "gh ref write refs/heads/main",
    ],
)
def test_fractal_owned_raw_publication_is_denied(command: str) -> None:
    context = {
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": ["/work/Fractal"],
            "repository_ids": ["2canshor/Fractal"],
            "trust_receipt_id": "trusted-live-hook-a",
        },
    }
    result = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "exec_command",
            "tool_input": {"command": command, "workdir": "/work/Fractal"},
        },
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_publication_guard_allows_reads_wrong_repo_and_exact_governed_route() -> None:
    context = {
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": ["/work/Fractal"],
            "repository_ids": ["2canshor/Fractal"],
            "trust_receipt_id": "trusted-live-hook-a",
        },
    }
    for payload in (
        {"tool_input": {"command": "git status", "workdir": "/work/Fractal"}},
        {"tool_input": {"command": "gh api repos/2canshor/Fractal/git/refs/heads/main"}},
        {"tool_input": {"command": "git push origin main", "workdir": "/work/Other"}},
    ):
        result = handle_hook("pre-tool-use", context, payload)
        assert "permissionDecision" not in result["hookSpecificOutput"]
    governed = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "exec_command",
            "tool_input": {
                "command": (
                    "fractal version publish --order order.json "
                    f"--order-sha256 {'a' * 64}"
                ),
                "workdir": "/work/Fractal",
            },
        },
    )
    assert governed["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert (
        governed["hookSpecificOutput"]["fractalObservation"]["decision"]
        == "allow-governed-publication"
    )
    compound = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_input": {
                "command": (
                    "fractal version publish --order order.json "
                    f"--order-sha256 {'a' * 64}; git push origin main"
                ),
                "workdir": "/work/Fractal",
            }
        },
    )
    assert compound["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("command", "workdir"),
    [
        ("/usr/bin/git push origin main", "/work/Fractal/subdirectory"),
        ('git -C "/work/Fractal/subdirectory" push origin main', "/work/Other"),
    ],
)
def test_publication_guard_denies_absolute_git_and_descendant_workdirs(
    command: str, workdir: str
) -> None:
    context = {
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": ["/work/Fractal"],
            "repository_ids": ["2canshor/Fractal"],
            "trust_receipt_id": "trusted-live-hook-a",
        },
    }
    result = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "exec_command",
            "tool_input": {"command": command, "workdir": workdir},
        },
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_adapter_built_context_can_observe_publication_before_post_activation_trust(
    tmp_path: Path,
) -> None:
    builder(tmp_path, "publication-observation").build_all()
    context = json.loads(
        (
            tmp_path
            / "publication-observation"
            / "codex"
            / "fractal"
            / "context.json"
        ).read_text()
    )
    assert "trust_receipt_id" not in context["publication_governance"]
    result = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "exec_command",
            "tool_input": {
                "command": (
                    "fractal version publish --order order.json "
                    f"--order-sha256 {'a' * 64}"
                ),
                "workdir": str(Path.home() / "Fractal"),
            },
        },
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    observation = result["hookSpecificOutput"]["fractalObservation"]
    assert observation["trust_status"] == "requires-version-store-validation"
    assert "trust_receipt_id" not in observation


def test_low_level_provider_ref_mutation_is_denied_for_fractal_repo() -> None:
    context = {
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": [],
            "repository_ids": ["2canshor/Fractal"],
            "trust_receipt_id": "trusted-live-hook-a",
        },
    }
    result = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "mcp__github__update_ref",
            "tool_input": {"repository": "2canshor/Fractal", "ref": "heads/main"},
        },
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_real_stop_payload_captures_and_evaluates_work_signature(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "turn-a",
                "message": {"role": "user", "content": "Run the bounded probe."},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "exec_command"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "uuid": "tool-result-a",
                "toolUseResult": {"status": "ok"},
                "message": {"role": "user", "content": [{"type": "tool_result"}]},
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
    assert first == {"suppressOutput": True}
    signature = json.loads(journal.read_text().strip())
    assert signature["tools"] == ["exec_command"]
    assert signature["project_id"] == "project-a"
    assert signature["work_id"] == "codex-turn-session-a-turn-a"
    assert signature["work_type"] == "request-general"
    assert signature["input_shape"].startswith("codex-request-")
    assert "bounded" not in signature["input_shape"]
    assert signature["thread_id"] == "session-a"
    second = capture_work_completion(
        context,
        {**payload, "last_assistant_message": "A second assistant fragment."},
        journal_path=journal,
        evaluations_path=evaluations,
    )
    assert second == {"suppressOutput": True}
    assert len(journal.read_text().splitlines()) == 1
    assert len(evaluations.read_text().splitlines()) == 1

    with transcript.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "turn-b",
                    "message": {
                        "role": "user",
                        "content": "Run the bounded probe.",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "function_call", "name": "exec_command"},
                }
            )
            + "\n"
        )
    capture_work_completion(
        context,
        payload,
        journal_path=journal,
        evaluations_path=evaluations,
    )
    assert len(journal.read_text().splitlines()) == 2
    evaluation = json.loads(evaluations.read_text().splitlines()[-1])
    assert evaluation["recognition"]["status"] == "possible-repetition"


def test_completed_turn_does_not_inherit_pre_fix_fragment_count(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "real-turn",
                "message": {"role": "user", "content": "Complete one turn."},
            }
        )
        + "\n"
    )
    journal = tmp_path / "work-signatures.jsonl"
    journal.write_text(
        json.dumps(
            {
                "work_id": "claude-stop-legacy-fragment",
                "project_id": "project-a",
                "work_type": "agent-session",
                "input_shape": "claude-stop-event",
                "steps": ["agent-session", "assistant-response"],
                "tools": [],
                "outcome_category": "completed-response",
                "purpose_class": "ordinary",
                "elapsed_seconds": None,
                "token_usage": None,
                "completed_at": "2026-08-22T00:00:00Z",
            }
        )
        + "\n"
    )
    evaluations = tmp_path / "evaluations.jsonl"
    capture_work_completion(
        {"platform": "claude", "active_project": {"project_id": "project-a"}},
        {
            "session_id": "session-a",
            "transcript_path": str(transcript),
            "last_assistant_message": "Done.",
        },
        journal_path=journal,
        evaluations_path=evaluations,
    )
    result = json.loads(evaluations.read_text().strip())
    assert result["recognition"]["status"] == "first-occurrence"
    assert result["recognition"]["occurrence_count"] == 1


def test_request_shape_redacts_paths_urls_ids_and_numbers() -> None:
    first = adapter_hook._request_shape_digest(
        "Edit /private/tmp/alpha.txt from https://example.com/a for item 12345"
    )
    second = adapter_hook._request_shape_digest(
        "Edit /private/tmp/beta.txt from https://other.example/b for item 98765"
    )
    assert first == second
    assert first is not None
    assert "/private/tmp" not in first
    assert "example.com" not in first


def test_different_jobs_using_the_same_tool_do_not_trigger_fatigue(tmp_path: Path) -> None:
    transcript = tmp_path / "different-jobs.jsonl"
    journal = tmp_path / "different-work-signatures.jsonl"
    evaluations = tmp_path / "different-evaluations.jsonl"
    context = {"platform": "codex", "active_project": {"project_id": "project-a"}}
    for index, request in enumerate(
        ("Write a report.", "Fix a Python bug.", "Organize the project folder.")
    ):
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": f"different-turn-{index}",
                        "message": {"role": "user", "content": request},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "function_call", "name": "exec_command"},
                    }
                )
                + "\n"
            )
        capture_work_completion(
            context,
            {
                "session_id": "different-session",
                "transcript_path": str(transcript),
                "last_assistant_message": f"Completed different job {index}",
            },
            journal_path=journal,
            evaluations_path=evaluations,
        )

    signatures = [json.loads(line) for line in journal.read_text().splitlines()]
    result = json.loads(evaluations.read_text().splitlines()[-1])
    assert len({item["input_shape"] for item in signatures}) == 3
    assert {item["work_type"] for item in signatures} == {
        "request-write",
        "request-fix",
        "request-organize",
    }
    assert result["recognition"]["status"] == "first-occurrence"
    assert "orchestration" not in result


def test_work_completed_hook_runs_local_learning_without_any_donor_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "projects"
    project_store = ProjectStore(project_root, runtime_root)
    project_store.create(
        ProjectRecord(
            project_id="project-a",
            title="Hook Learning Project",
            system_version="0.1.0-alpha.8-r1",
        ),
        actor="main-agent",
        platform="codex",
    )
    record_path = project_root / "project-a" / "record.json"
    state_path = runtime_root / "live-state" / "current.json"
    live_state = {
        "project": {
            "project_id": "project-a",
            "revision": 0,
            "status": "in_progress",
            "current_phase": None,
            "source_path": str(record_path),
        },
        "system_version": {"version": "0.1.0-alpha.8-r1"},
    }
    monkeypatch.setattr(adapter_hook, "resolve_session_state", lambda _context: live_state)
    context = {
        "platform": "codex",
        "active_project": {"project_id": "stale-snapshot"},
        "live_runtime": {"state_path": str(state_path)},
    }
    transcript = tmp_path / "transcript.jsonl"
    journal = runtime_root / "work-signatures.jsonl"
    evaluations = runtime_root / "work-signature-evaluations.jsonl"
    for index in range(3):
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": f"turn-{index}",
                        "message": {"role": "user", "content": "Repeat the same work."},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "function_call", "name": "exec_command"},
                    }
                )
                + "\n"
            )
        capture_work_completion(
            context,
            {
                "session_id": "session-a",
                "transcript_path": str(transcript),
                "last_assistant_message": f"Completed {index}",
            },
            journal_path=journal,
            evaluations_path=evaluations,
        )

    evaluation = json.loads(evaluations.read_text().splitlines()[-1])
    learning = evaluation["orchestration"]["learning"]
    assert evaluation["recognition"]["status"] == "investigation-required"
    assert learning["status"] == "candidate"
    assert learning["candidate_id"].startswith("candidate-method-")
    assert learning["canonical_evidence_id"].startswith("evidence-learning-review-")
    assert (runtime_root / "learning" / "candidates" / learning["candidate_id"]).is_dir()
    project = project_store.read("project-a")
    assert any(item["id"] == learning["canonical_evidence_id"] for item in project.evidence)
    assert project_store.verify("project-a")["event_chain_valid"] is True


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


def test_user_surface_projects_only_entries_and_keeps_hidden_methods_internal(
    tmp_path: Path,
) -> None:
    surface_governed_builder(tmp_path, "surface-governed").build("codex")
    built = tmp_path / "surface-governed" / "codex"
    assert (built / "skills" / "research" / "SKILL.md").is_file()
    assert not (built / "skills" / "clarification").exists()
    assert (built / "fractal" / "internal-workflows" / "clarification" / "SKILL.md").is_file()
    workflow_map = json.loads((built / "fractal" / "internal-workflow-map.json").read_text())
    assert workflow_map["visible_component_ids"] == ["research"]
    assert workflow_map["workflows"][0]["dots"][0]["component_id"] == "clarification"
    home = tmp_path / "surface-home"
    installer = CodexComponentInstaller(
        tmp_path / "surface-installs", tmp_path / "surface-quarantine"
    )
    installer.install(built, home)
    assert (home / "skills" / "research").is_symlink()
    assert not (home / "skills" / "clarification").exists()


def test_claude_component_install_merges_settings_and_restores_extras(
    tmp_path: Path,
) -> None:
    adapter_builder = governed_builder(tmp_path, "governed-claude")
    add_claude_model_route(adapter_builder.private_root)
    adapter_builder.build_all()
    built = tmp_path / "governed-claude" / "claude"
    home = tmp_path / "claude-home"
    (home / "skills" / "legacy-extra").mkdir(parents=True)
    (home / "skills" / "legacy-extra" / "SKILL.md").write_text("legacy extra")
    (home / "CLAUDE.md").write_text("previous entrypoint")
    (home / "settings.json").write_text(
        json.dumps(
            {
                "theme": "dark",
                "enabledPlugins": {},
                "model": "old-model",
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "platform-secret",
                    "ANTHROPIC_BASE_URL": "http://old-gateway.test",
                    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                },
            }
        )
    )
    installer = ClaudeComponentInstaller(tmp_path / "component-installs", tmp_path / "quarantine")
    record = installer.install(built, home)
    installed_settings = json.loads((home / "settings.json").read_text())
    assert (home / "CLAUDE.md").is_symlink()
    assert (home / "skills" / "research").is_symlink()
    assert not (home / "skills" / "legacy-extra").exists()
    assert installed_settings["theme"] == "dark"
    assert set(installed_settings["hooks"]) == {"PreToolUse", "SessionStart", "Stop"}
    assert installed_settings["model"] == "sonnet"
    assert installed_settings["availableModels"] == ["sonnet"]
    assert installed_settings["modelOverrides"] == {"claude-sonnet-5": "gemma4:12b"}
    assert installed_settings["env"]["ANTHROPIC_AUTH_TOKEN"] == "platform-secret"
    assert installed_settings["env"]["ANTHROPIC_BASE_URL"] == "http://gateway.test:8000"
    assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" not in installed_settings["env"]
    assert record["applied_model_route"]["gateway_component_id"] == "ollama-gateway"
    assert record["persistent_system_version_activated"] is False
    restored = installer.restore(record["install_id"])
    assert (home / "CLAUDE.md").read_text() == "previous entrypoint"
    assert (home / "skills" / "legacy-extra" / "SKILL.md").read_text() == "legacy extra"
    assert "skills/legacy-extra" in restored["restored_quarantine"]


def test_claude_model_route_rejects_secret_material(tmp_path: Path) -> None:
    adapter_builder = governed_builder(tmp_path, "unsafe-claude-route")
    add_claude_model_route(adapter_builder.private_root)
    route = adapter_builder.private_root / "adapters" / "claude" / "model-route.json"
    value = json.loads(route.read_text())
    value["environment"]["ANTHROPIC_AUTH_TOKEN"] = "must-not-enter-canonical-state"
    route.write_text(json.dumps(value))
    with pytest.raises(AdapterError, match="unapproved environment setting"):
        adapter_builder.build_all()


def test_gemini_component_install_switches_and_restores_skills(tmp_path: Path) -> None:
    governed_builder(tmp_path, "governed-gemini").build_all()
    built = tmp_path / "governed-gemini" / "gemini"
    home = tmp_path / "gemini-home"
    (home / "config" / "skills" / "legacy-extra").mkdir(parents=True)
    (home / "config" / "skills" / "legacy-extra" / "SKILL.md").write_text("legacy extra")
    (home / "GEMINI.md").write_text("previous entrypoint")
    installer = GeminiComponentInstaller(tmp_path / "component-installs", tmp_path / "quarantine")
    record = installer.install(built, home)
    assert (home / "GEMINI.md").is_symlink()
    assert (home / "config" / "skills" / "research").is_symlink()
    assert not (home / "config" / "skills" / "legacy-extra").exists()
    assert record["persistent_system_version_activated"] is False
    restored = installer.restore(record["install_id"])
    assert (home / "GEMINI.md").read_text() == "previous entrypoint"
    assert (home / "config" / "skills" / "legacy-extra" / "SKILL.md").read_text() == "legacy extra"
    assert "config/skills/legacy-extra" in restored["restored_quarantine"]
