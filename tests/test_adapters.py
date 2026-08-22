from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from fractal.adapter_hook import handle_hook
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
    assert (tmp_path / "build" / "claude" / "settings.fragment.json").is_file()
    assert list((tmp_path / "build" / "cowork" / "skill-packages").glob("*.skill"))
    gemini_metadata = json.loads(
        (tmp_path / "build" / "gemini" / "fractal" / "capability-metadata.json").read_text()
    )
    assert {item["activation"] for item in gemini_metadata} == {"unknown"}


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
    }
    session = handle_hook("session-start", context, {"source": "startup"})
    assert "project-a" in session["hookSpecificOutput"]["additionalContext"]
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
