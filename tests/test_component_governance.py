from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.component_governance import (
    ComponentGovernanceError,
    active_components,
    audit_component_apple_continuous_improvement_alignment,
    audit_component_drift,
    audit_component_registry_apple_continuous_improvement,
    load_component_registry,
    render_component_status,
    tree_sha256,
    write_component_apple_continuous_improvement_audit,
)
from fractal.component_inventory import (
    _frontmatter,
    build_component_registry,
    observe_platform_components,
)
from fractal.review_contracts import validate_claim_receipt


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
            "claim_receipt": None,
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
    assert "Verified Staged: `1`" in view
    assert "Available, Not Yet Proven: `0`" in view
    assert "Execution Still Unknown" not in view
    assert "Adapter Build State: `candidate`" in view
    assert "Current Version State: `unknown until live verification`" in view
    assert "slash-command menu is not this status surface" in view

    live_view = render_component_status(
        registry,
        platform="codex",
        live_state={
            "system_version": {
                "version": "0.1.0-alpha.2-live",
                "status": "active",
            }
        },
    )
    assert "Adapter Build State: `candidate`" in live_view
    assert "Current Active System Version: `0.1.0-alpha.2-live`" in live_view
    assert "Current Version State: `active`" in live_view


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
    registry = load_component_registry(write_registry(tmp_path / "registry.json", [runtime]))
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
    assert (
        audit_component_drift(registry, observed["components"], platform="claude")["clean"] is True
    )

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


def test_gemini_observes_generated_skills_in_config_directory(tmp_path: Path) -> None:
    live = tmp_path / "gemini" / "config" / "skills" / "research"
    live.mkdir(parents=True)
    (live / "SKILL.md").write_text("governed research")
    research = component("research")
    research["platforms"] = ["gemini"]
    research["projection"]["expected_sha256"] = tree_sha256(live)
    registry = load_component_registry(write_registry(tmp_path / "registry.json", [research]))
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"platform_version": "test", "tools": []}))
    observed = observe_platform_components(
        registry,
        platform="gemini",
        platform_home=tmp_path / "gemini",
        tool_snapshot_path=tools,
        configured_mcp=[],
    )
    assert [item["component_id"] for item in observed["components"]] == ["research"]


def test_skill_frontmatter_parses_literal_and_folded_descriptions(tmp_path: Path) -> None:
    literal = tmp_path / "literal.md"
    literal.write_text(
        "---\nname: review\ndescription: |\n"
        "  Review the requested object.\n  Keep tools internal.\n---\n"
    )
    folded = tmp_path / "folded.md"
    folded.write_text(
        "---\nname: create\ndescription: >\n"
        "  Create one complete outcome.\n  Coordinate tools internally.\n---\n"
    )
    malformed = tmp_path / "malformed.md"
    malformed.write_text("---\nname: bad\ndescription: |\n---\n")
    assert _frontmatter(literal)["description"] == (
        "Review the requested object.\nKeep tools internal."
    )
    assert _frontmatter(folded)["description"] == (
        "Create one complete outcome. Coordinate tools internally."
    )
    assert "description" not in _frontmatter(malformed)


def test_user_job_requires_one_commandable_verb_contract(tmp_path: Path) -> None:
    review = component("review")
    review["surface_audience"] = "user-job"
    review["invocation"] = {"automatic_matching": True, "explicit_invocation": True}
    review["job_contract"] = {
        "job_id": "review",
        "action": "review",
        "outcome": "Review the requested object",
        "completion": "Findings and next action are ready",
        "authority_boundary": "Read-only unless repair is separately requested",
    }
    registry_value = {
        "record_type": "component-registry",
        "record_version": 3,
        "system_version": "0.1.0-alpha.4",
        "candidate_status": "candidate",
        "components": [review],
    }
    path = tmp_path / "user-job.json"
    path.write_text(json.dumps(registry_value))
    assert load_component_registry(path)["components"][0]["job_contract"]["action"] == "review"
    review["job_contract"]["action"] = "inspect"
    path.write_text(json.dumps({**registry_value, "components": [review]}))
    with pytest.raises(ComponentGovernanceError, match="action and component id disagree"):
        load_component_registry(path)


def test_verified_live_status_requires_same_component_claim_gate_receipt(tmp_path: Path) -> None:
    live = component("review")
    live["surface_audience"] = "supporting-capability"
    live["invocation"] = {"automatic_matching": False, "explicit_invocation": True}
    live["job_contract"] = None
    live["status"]["execution"] = "verified-live"
    registry = {
        "record_type": "component-registry",
        "record_version": 3,
        "system_version": "0.1.0-alpha.4",
        "candidate_status": "candidate",
        "components": [live],
    }
    path = tmp_path / "live.json"
    path.write_text(json.dumps(registry))
    with pytest.raises(ComponentGovernanceError, match="requires a Claim Gate receipt"):
        load_component_registry(path)

    live["status"]["claim_receipt"] = validate_claim_receipt(
        {
            "claim_id": "claim-review-live",
            "subject_id": "review",
            "surface": "codex-skill",
            "observed_at": "2026-08-23T05:00:00Z",
            "asserted_state": "verified-live",
            "proof_type": "representative-real-task",
            "evidence_ids": ["representative-review-task"],
            "scope": {"platform": "codex", "account": "local", "task": "review file"},
            "version_dependencies": {"codex": "2026.08", "adapter": "alpha.4"},
            "actual_user_outcome": {"observed": False, "evidence_ids": []},
        }
    )
    path.write_text(json.dumps(registry))
    assert load_component_registry(path)["components"][0]["status"]["execution"] == (
        "verified-live"
    )


def test_apple_continuous_improvement_component_audit_is_deterministic_and_portable(
    tmp_path: Path,
) -> None:
    registry_path = write_registry(tmp_path / "registry.json", [component("research")])
    first = audit_component_registry_apple_continuous_improvement(registry_path)
    second = audit_component_registry_apple_continuous_improvement(registry_path)
    assert first == second
    assert first["summary"] == {
        "overall_status": "deterministic-pass",
        "deterministic_checks_passed": True,
        "component_count": 1,
        "component_pass_count": 1,
        "component_fail_count": 0,
        "human_qualitative_acceptance_pending_count": 0,
        "release_readiness": "ready",
    }
    result = first["components"][0]
    assert result["purpose"] == {
        "kind": "supporting-purpose",
        "basis": "trigger",
        "human_name_present": True,
        "description_present": True,
        "effective_operations": ["read"],
    }
    assert result["execution"] == {
        "registration_state": "registered",
        "execution_state": "staged",
        "active": True,
        "successful_execution_claimed": False,
        "available_unverified_is_success": False,
    }
    assert result["apple_principle_checks"]["delight"] == "not-directly-user-visible"
    assert result["continuous_improvement_route"] == [
        "component-governance",
        "capability-check",
        "environment-adapters",
        "system-review",
        "continuous-improvement",
    ]
    assert ("/" + "Users" + "/") not in json.dumps(first)

    output = tmp_path / "evidence" / "audit.json"
    written = write_component_apple_continuous_improvement_audit(registry_path, output)
    assert json.loads(output.read_text()) == written


def test_user_visible_delight_records_proxy_but_never_claims_pass(tmp_path: Path) -> None:
    registry = load_component_registry(
        write_registry(tmp_path / "registry.json", [component("review")])
    )
    user_job = registry["components"][0]
    user_job["surface_audience"] = "user-job"
    user_job["invocation"] = {"automatic_matching": True, "explicit_invocation": True}
    user_job["job_contract"] = {
        "job_id": "review",
        "action": "review",
        "outcome": "Review one requested object.",
        "completion": "Evidence and next action are ready.",
        "authority_boundary": "Read-only unless change is separately requested.",
    }
    audit = audit_component_apple_continuous_improvement_alignment(
        registry,
        registry_sha256="a" * 64,
    )
    result = audit["components"][0]
    assert result["apple_principle_checks"]["delight"] == (
        "proxy-observed-human-pending"
    )
    assert result["delight"] == {
        "observable_proxy": True,
        "human_qualitative_acceptance": "pending",
        "claimed_pass": False,
    }
    assert audit["summary"]["overall_status"] == (
        "deterministic-pass-human-acceptance-pending"
    )
    assert audit["summary"]["release_readiness"] == "blocked"


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        (
            lambda item: item["status"].update(execution="unknown"),
            "unknown-or-missing-execution-state",
        ),
        (
            lambda item: item["status"].update(success=True),
            "misleading-active-success-claim",
        ),
        (
            lambda item: item["permissions"].update(
                secret_boundary="api_key=sk-proj-abcdefghijklmnopqrstuvwxyz"
            ),
            "credential-content-present",
        ),
        (
            lambda item: item["recovery"].update(removal="rm -rf /tmp/component"),
            "destructive-or-irrecoverable-recovery",
        ),
    ],
)
def test_apple_component_audit_fails_closed_without_leaking_values(
    tmp_path: Path,
    mutation,
    finding: str,
) -> None:
    registry = load_component_registry(
        write_registry(tmp_path / "registry.json", [component("research")])
    )
    mutation(registry["components"][0])
    audit = audit_component_apple_continuous_improvement_alignment(
        registry,
        registry_sha256="b" * 64,
    )
    result = audit["components"][0]
    assert finding in result["findings"]
    assert result["deterministic_result"] == "fail"
    assert audit["summary"]["overall_status"] == "fail-closed"
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz" not in json.dumps(audit)


def test_unavailable_component_has_explicit_no_execution_purpose_operation(
    tmp_path: Path,
) -> None:
    unavailable = component("old-skill", disposition="inactive-quarantined")
    unavailable["permissions"] = {
        "profile": "inactive-no-execution",
        "operations": [],
        "secret_boundary": "No execution and no secret access while quarantined.",
    }
    registry = load_component_registry(
        write_registry(tmp_path / "registry.json", [unavailable])
    )
    audit = audit_component_apple_continuous_improvement_alignment(
        registry,
        registry_sha256="c" * 64,
    )
    result = audit["components"][0]
    assert result["purpose"]["effective_operations"] == ["no-execution"]
    assert result["execution"]["execution_state"] == "unavailable"
    assert result["deterministic_result"] == "pass"
