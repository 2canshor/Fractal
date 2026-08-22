from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.component_governance import (
    ComponentGovernanceError,
    active_components,
    audit_component_drift,
    load_component_registry,
    render_component_status,
    tree_sha256,
)
from fractal.component_inventory import build_component_registry, observe_platform_components


def component(component_id: str, *, disposition: str = "fractal-owned-canonical") -> dict:
    active = disposition in {
        "fractal-owned-canonical",
        "approved-external-managed",
        "platform-managed-adapter",
    }
    return {
        "component_id": component_id,
        "human_name": component_id.replace("-", " ").title(),
        "kind": "skill",
        "disposition": disposition,
        "external_identifier": None,
        "dependencies": [],
        "owner": {"owner_id": "2canshor/fractal", "source_controlled_by_owner": True},
        "source": {
            "kind": "fractal-public",
            "locator": f"capabilities/skills/{component_id}",
            "version": "1.0.0",
            "content_sha256": "a" * 64,
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
        "trigger": {"mode": "explicit", "description": "Use for a bounded test."},
        "status": {
            "discoverable": active,
            "active": active,
            "execution": "verified-staged" if active else "unavailable",
            "evidence_ids": ["test-evidence"],
        },
        "platforms": ["codex"],
        "projection": {
            "mode": "generated-copy" if active else "quarantine",
            "target": f"skills/{component_id}" if active else "quarantine/test",
            "expected_sha256": "b" * 64 if active else None,
        },
        "verification_evidence": ["test-evidence"],
        "overlap": {"decision": "distinct bounded responsibility", "with": []},
        "recovery": {"removal": "remove generated copy", "restore": "rebuild candidate"},
    }


def write_registry(path: Path, components: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "record_type": "component-registry",
                "record_version": 2,
                "system_version": "0.1.0-alpha.2",
                "candidate_status": "candidate",
                "components": components,
            }
        )
    )
    return path


def test_registry_requires_sorted_complete_individual_records(tmp_path: Path) -> None:
    path = write_registry(tmp_path / "registry.json", [component("a-skill"), component("b-skill")])
    registry = load_component_registry(path)
    assert [item["component_id"] for item in active_components(registry, "codex")] == [
        "a-skill",
        "b-skill",
    ]
    path = write_registry(tmp_path / "bad.json", [component("b-skill"), component("a-skill")])
    with pytest.raises(ComponentGovernanceError, match="unique and sorted"):
        load_component_registry(path)


def test_drift_detects_unmanaged_missing_changed_and_inactive_discovery(tmp_path: Path) -> None:
    registry = load_component_registry(
        write_registry(
            tmp_path / "registry.json",
            [
                component("active-a"),
                component("active-b"),
                component("old-skill", disposition="inactive-quarantined"),
            ],
        )
    )
    audit = audit_component_drift(
        registry,
        [
            {"component_id": "active-a", "content_sha256": "0" * 64},
            {"component_id": "old-skill", "content_sha256": None},
            {"component_id": "unmanaged", "content_sha256": None},
        ],
        platform="codex",
    )
    assert audit == {
        "record_type": "component-drift-audit",
        "platform": "codex",
        "clean": False,
        "unmanaged": ["unmanaged"],
        "registered_missing": ["active-b"],
        "hash_changed": ["active-a"],
        "inactive_but_discoverable": ["old-skill"],
    }


def test_human_view_explains_status_route(tmp_path: Path) -> None:
    registry = load_component_registry(
        write_registry(tmp_path / "registry.json", [component("research")])
    )
    view = render_component_status(registry, platform="codex")
    assert "Active and Managed: `1`" in view
    assert "slash-command menu is not this status surface" in view


def test_active_dependency_must_be_registered_and_active(tmp_path: Path) -> None:
    dependent = component("dependent-skill")
    dependent["dependencies"] = ["missing-tool"]
    path = write_registry(tmp_path / "registry.json", [dependent])
    with pytest.raises(ComponentGovernanceError, match="unavailable dependency"):
        load_component_registry(path)

    dependency = component("missing-tool")
    dependency["kind"] = "tool"
    registry = load_component_registry(
        write_registry(tmp_path / "valid.json", [dependent, dependency])
    )
    assert registry["components"][0]["dependencies"] == ["missing-tool"]


def test_component_hash_ignores_transient_python_and_finder_clutter(tmp_path: Path) -> None:
    source = tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("stable source")
    original = tree_sha256(source)
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-312.pyc").write_bytes(b"temporary")
    (source / ".DS_Store").write_bytes(b"finder")
    assert tree_sha256(source) == original
    (source / "SKILL.md").write_text("changed source")
    assert tree_sha256(source) != original


def test_platform_runtime_observes_live_projection_not_source_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"source artifact")
    live = tmp_path / "site-packages" / "fractal"
    live.mkdir(parents=True)
    (live / "adapter_hook.py").write_text("live runtime")
    runtime = component("platform-runtime")
    runtime["kind"] = "platform-capability"
    runtime["source"]["locator"] = str(artifact)
    runtime["projection"] = {
        "mode": "platform-reference",
        "target": str(live),
        "expected_sha256": tree_sha256(live),
    }
    registry = load_component_registry(
        write_registry(tmp_path / "registry.json", [runtime])
    )
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"platform_version": "test", "tools": []}))
    observed = observe_platform_components(
        registry,
        platform="codex",
        platform_home=tmp_path / "home",
        tool_snapshot_path=tools,
        configured_mcp=[],
    )
    assert observed["components"] == [
        {
            "component_id": "platform-runtime",
            "discoverable": True,
            "active": True,
            "content_sha256": tree_sha256(live),
        }
    ]


def test_live_platform_surface_is_registered_and_extra_item_drifts(tmp_path: Path) -> None:
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"platform_version": "test", "tools": []}))
    surface = tmp_path / "claude-surface.json"
    live_surface = {
        "claude_code_version": "2.1.237",
        "tools": ["Read"],
        "skills": ["research", "verify"],
        "agents": ["Explore", "fractal-verifier"],
        "slash_commands": ["research", "verify", "config"],
        "capabilities": ["msg_lifecycle_v1"],
        "mcp_servers": [],
        "plugins": [],
    }
    surface.write_text(json.dumps(live_surface))
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "system_version": "0.1.0-alpha.2",
                "candidate_status": "candidate",
                "tool_snapshot": str(tools),
                "platform_surfaces": [
                    {
                        "platform": "claude",
                        "snapshot": str(surface),
                        "owner_id": "Anthropic Claude Code",
                        "platform_version": "2.1.237",
                    }
                ],
            }
        )
    )
    registry = build_component_registry(policy, tmp_path / "registry.json")
    assert len(registry["components"]) == 7
    observed = observe_platform_components(
        registry,
        platform="claude",
        platform_home=tmp_path / "home",
        tool_snapshot_path=tools,
        configured_mcp=[],
        platform_surface_path=surface,
    )
    assert audit_component_drift(registry, observed["components"], platform="claude")[
        "clean"
    ] is True

    live_surface["tools"].append("Write")
    surface.write_text(json.dumps(live_surface))
    drifted = observe_platform_components(
        registry,
        platform="claude",
        platform_home=tmp_path / "home",
        tool_snapshot_path=tools,
        configured_mcp=[],
        platform_surface_path=surface,
    )
    audit = audit_component_drift(registry, drifted["components"], platform="claude")
    assert audit["unmanaged"] == ["tool-claude-write"]
