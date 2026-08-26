import json
from copy import deepcopy
from pathlib import Path

import pytest

from fractal.models import ProjectRecord
from fractal.user_surface import (
    UserSurfaceError,
    audit_codex_skill_path_surface,
    audit_codex_skill_surface,
    audit_user_surface_experience,
    build_codex_skill_config_edits,
    build_user_surface,
    load_user_surface,
    validate_user_surface,
)
from fractal.views import render_project_summary, render_user_feedback


def skill(component_id: str, *, active: bool = True, platform: str = "codex") -> dict:
    return {
        "component_id": component_id,
        "kind": "skill",
        "status": {"active": active},
        "platforms": [platform],
    }


def registry() -> dict:
    return {
        "system_version": "0.1.0-alpha.6",
        "components": [
            skill("assess"),
            skill("automate"),
            skill("learn"),
            skill("create"),
            skill("dot-browser"),
            skill("dot-document"),
            skill("align"),
            skill("version"),
        ],
    }


def surface() -> dict:
    return {
        "record_type": "user-surface",
        "record_version": 1,
        "system_version": "0.1.0-alpha.6",
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
                "entry_id": "align",
                "interface_type": "command",
                "component_id": "align",
                "outcome": "Align an active Project with current reality.",
            },
            {
                "entry_id": "assess",
                "interface_type": "command",
                "component_id": "assess",
                "outcome": "Decide whether to continue, change, or stop one idea.",
            },
            {
                "entry_id": "automate",
                "interface_type": "action",
                "component_id": "automate",
                "outcome": "Make a repeated job run reliably.",
            },
            {
                "entry_id": "create",
                "interface_type": "action",
                "component_id": "create",
                "outcome": "Make the requested outcome.",
            },
            {
                "entry_id": "learn",
                "interface_type": "command",
                "component_id": "learn",
                "outcome": "Learn from a completed Project through all eight System Review Flows.",
            },
            {
                "entry_id": "version",
                "interface_type": "command",
                "component_id": "version",
                "outcome": "Apply, record, activate, and publish a permitted version.",
            },
        ],
        "dot_groups": [
            {
                "group_id": "browser",
                "purpose": "Control a browser when a workflow needs it.",
                "component_ids": ["dot-browser"],
            },
            {
                "group_id": "document",
                "purpose": "Create or change a document.",
                "component_ids": ["dot-document"],
            },
        ],
        "workflows": [
            {
                "workflow_id": "automate-browser-job",
                "entry_id": "automate",
                "user_job": "Repeat a browser job on a trigger or schedule.",
                "positive_examples": ["Run this check every Monday."],
                "negative_examples": ["Open this page once."],
                "dot_group_ids": ["browser"],
                "completion": "The repeatable workflow is tested and recoverable.",
                "authority_boundary": "No schedule or external write without scoped authority.",
            },
            {
                "workflow_id": "create-document",
                "entry_id": "create",
                "user_job": "Create a document.",
                "positive_examples": ["Create a project brief."],
                "negative_examples": ["Review this existing brief."],
                "dot_group_ids": ["browser", "document"],
                "completion": "The document is rendered and checked.",
                "authority_boundary": "Creation does not imply publication.",
            },
        ],
        "hidden_skill_component_ids": ["dot-browser", "dot-document"],
        "recovery": {
            "disable_method": "Codex skills.config entries; source files are retained.",
            "restore_method": "Restore the prior skills.config value and restart Codex.",
        },
    }


def test_user_surface_is_many_to_many_and_exhaustive() -> None:
    validated = validate_user_surface(surface(), registry())

    assert validated["action_resolution"]["feature_name"] == "Object-Aware Actions"
    assert validated["summary"] == {
        "action_count": 2,
        "command_count": 4,
        "hidden_skill_count": 2,
        "reused_dot_count": 1,
        "workflow_count": 2,
    }


def test_user_surface_rejects_an_unclassified_active_skill() -> None:
    changed = registry()
    changed["components"].append(skill("new-provider-method"))

    with pytest.raises(UserSurfaceError, match="unclassified active Skills"):
        validate_user_surface(surface(), changed)


def test_user_surface_rejects_a_workflow_without_a_visible_entry() -> None:
    changed = deepcopy(surface())
    changed["workflows"][0]["entry_id"] = "missing"

    with pytest.raises(UserSurfaceError, match="does not reference a visible entry"):
        validate_user_surface(changed, registry())


def test_user_surface_rejects_changed_object_aware_route_states() -> None:
    changed = deepcopy(surface())
    changed["action_resolution"]["route_states"] = [
        "exact",
        "missing",
        "partial",
        "unavailable",
    ]

    with pytest.raises(UserSurfaceError, match="Object-Aware Actions route states"):
        validate_user_surface(changed, registry())


def test_user_surface_rejects_a_hidden_skill_without_a_route() -> None:
    changed = deepcopy(surface())
    changed["workflows"][1]["dot_group_ids"] = ["browser"]

    with pytest.raises(UserSurfaceError, match="hidden Skills without a workflow"):
        validate_user_surface(changed, registry())


def test_user_surface_loader_validates_schema_and_registry(tmp_path: Path) -> None:
    path = tmp_path / "user-surface.json"
    path.write_text(json.dumps(surface()), encoding="utf-8")

    loaded = load_user_surface(path, registry())

    assert loaded["platform"] == "codex"


def test_user_surface_build_expands_the_registry_hidden_set(tmp_path: Path) -> None:
    policy = surface()
    policy.pop("system_version")
    policy.pop("hidden_skill_component_ids")
    policy.pop("summary", None)
    policy["record_type"] = "user-surface-policy"
    policy_path = tmp_path / "policy.json"
    output = tmp_path / "surface.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    built = build_user_surface(policy_path, registry(), output)

    assert built["hidden_skill_component_ids"] == ["dot-browser", "dot-document"]
    assert json.loads(output.read_text())["summary"]["reused_dot_count"] == 1


def test_codex_config_edits_hide_every_non_surface_skill_and_keep_sources() -> None:
    listed = [
        {
            "name": "assess",
            "path": "/candidate/skills/assess/SKILL.md",
            "enabled": True,
        },
        {
            "name": "automate",
            "path": "/candidate/skills/automate/SKILL.md",
            "enabled": True,
        },
        {
            "name": "learn",
            "path": "/candidate/skills/learn/SKILL.md",
            "enabled": True,
        },
        {
            "name": "create",
            "path": "/candidate/skills/create/SKILL.md",
            "enabled": True,
        },
        {
            "name": "align",
            "path": "/candidate/skills/align/SKILL.md",
            "enabled": True,
        },
        {
            "name": "version",
            "path": "/candidate/skills/version/SKILL.md",
            "enabled": True,
        },
        {
            "name": "provider:browser",
            "path": "/provider/browser/SKILL.md",
            "enabled": True,
        },
        {
            "name": "provider:documents",
            "path": "/provider/documents/SKILL.md",
            "enabled": False,
        },
    ]

    edits = build_codex_skill_config_edits(surface(), listed)

    assert edits == [
        {
            "keyPath": "skills.config",
            "mergeStrategy": "replace",
            "value": [
                {"enabled": False, "path": "/provider/browser/SKILL.md"},
                {"enabled": False, "path": "/provider/documents/SKILL.md"},
            ],
        }
    ]


def test_codex_config_edits_fail_when_a_visible_entry_is_missing() -> None:
    with pytest.raises(UserSurfaceError, match="missing visible entries"):
        build_codex_skill_config_edits(
            surface(),
            [{"name": "create", "path": "/create/SKILL.md", "enabled": True}],
        )


def test_codex_config_edits_disable_a_duplicate_visible_name_from_an_old_source() -> None:
    listed = [
        {"name": item["entry_id"], "path": f"/old/{item['entry_id']}/SKILL.md", "enabled": True}
        for item in surface()["entries"]
    ]
    expected = {
        item["entry_id"]: f"/candidate/{item['entry_id']}/SKILL.md" for item in surface()["entries"]
    }

    edits = build_codex_skill_config_edits(surface(), listed, visible_skill_paths=expected)

    assert len(edits[0]["value"]) == 6
    assert {item["path"] for item in edits[0]["value"]} == {
        f"/old/{item['entry_id']}/SKILL.md" for item in surface()["entries"]
    }


def test_codex_surface_audit_distinguishes_hidden_files_from_enabled_entries() -> None:
    listed = [
        {"name": item["entry_id"], "path": f"/{item['entry_id']}/SKILL.md", "enabled": True}
        for item in surface()["entries"]
    ]
    listed.append(
        {"name": "provider:browser", "path": "/provider/browser/SKILL.md", "enabled": False}
    )
    listed.append({"name": "create", "path": "/old/create/SKILL.md", "enabled": False})

    report = audit_codex_skill_surface(surface(), listed)

    assert report["clean"] is True
    assert report["source_files_deleted"] is False
    assert "provider:browser" not in report["actual_enabled_skill_names"]


def test_exact_path_audit_rejects_duplicate_names_and_plugin_path_drift() -> None:
    visible_paths = {
        item["entry_id"]: f"/candidate/{item['entry_id']}/SKILL.md" for item in surface()["entries"]
    }
    listed = [{"name": name, "path": path, "enabled": True} for name, path in visible_paths.items()]
    listed.extend(
        [
            {
                "name": "create",
                "path": "/old/create/SKILL.md",
                "enabled": True,
            },
            {
                "name": "provider:new-version",
                "path": "/plugin/cache/2.0.0/skills/new/SKILL.md",
                "enabled": True,
            },
        ]
    )

    report = audit_codex_skill_path_surface(surface(), listed, visible_skill_paths=visible_paths)

    assert report["clean"] is False
    assert report["unexpected_enabled_skill_paths"] == [
        "/old/create/SKILL.md",
        "/plugin/cache/2.0.0/skills/new/SKILL.md",
    ]


def test_exact_path_audit_requires_every_candidate_entry() -> None:
    visible_paths = {
        item["entry_id"]: f"/candidate/{item['entry_id']}/SKILL.md" for item in surface()["entries"]
    }
    listed = [
        {"name": name, "path": path, "enabled": True}
        for name, path in visible_paths.items()
        if name != "create"
    ]

    report = audit_codex_skill_path_surface(surface(), listed, visible_skill_paths=visible_paths)

    assert report["clean"] is False
    assert report["missing_visible_skill_paths"] == ["/candidate/create/SKILL.md"]


def test_user_surface_experience_audit_accepts_plain_recoverable_views() -> None:
    blocked = render_user_feedback(
        "Could not save the change",
        state="blocked",
        summary="The shared record changed before this save could finish.",
        reason="A newer revision is already available.",
        next_action="Review the newer revision, then retry the save.",
        authority="Only the Project owner can approve the conflicting choice.",
        recovery="The earlier revision remains unchanged and can still be restored.",
        ai_assistance={
            "use": "AI compared the two revision summaries.",
            "limits": "The comparison cannot decide which intent is correct.",
            "retry": "Retry after the owner records the intended choice.",
            "revert": "Discard this draft and keep the earlier revision.",
        },
    )

    report = audit_user_surface_experience(
        surface(),
        normal_views=["# Project\n\n## Current status\n\n- Status: In progress"],
        feedback_views=[
            {
                "state": "blocked",
                "text": blocked,
                "significant": True,
                "ai_assisted": True,
            }
        ],
        delight_observations=["The next action follows the reason in reading order."],
    )

    assert report["clean"] is True
    assert report["finding_count"] == 0
    assert report["delight"] == {
        "status": "human-acceptance-pending",
        "observable_proxy_count": 1,
        "observable_proxies": ["The next action follows the reason in reading order."],
    }


def test_user_surface_experience_audit_rejects_internal_and_lifecycle_leaks() -> None:
    changed = deepcopy(surface())
    changed["entries"][1] = {
        "entry_id": "tune",
        "interface_type": "command",
        "component_id": "automate",
        "outcome": "/Workflow",
    }
    feedback = "# Failed\n\n- Summary: You failed to provide a valid Source.\n"

    report = audit_user_surface_experience(
        changed,
        normal_views=["# Result\n### Provider detail\nWorkflow: internal-route"],
        feedback_views=[
            {
                "state": "error",
                "text": feedback,
                "significant": True,
                "ai_assisted": True,
            }
        ],
    )

    assert report["clean"] is False
    check_ids = {item["check_id"] for item in report["findings"]}
    assert {
        "ai-assistance-is-disclosed",
        "commands-are-lifecycle-controls",
        "entry-copy-hides-internal-taxonomy",
        "feedback-does-not-blame",
        "feedback-explains-what-happened",
        "feedback-offers-a-next-action",
        "normal-views-hide-internal-taxonomy",
        "significant-actions-show-authority",
        "significant-actions-show-recovery",
        "slash-is-syntax-only",
        "text-has-semantic-reading-order",
    }.issubset(check_ids)


@pytest.mark.parametrize("state", ["blocked", "empty", "error", "unknown"])
def test_feedback_requires_reason_and_next_action_for_non_success_states(state: str) -> None:
    with pytest.raises(ValueError, match="reason must be specific"):
        render_user_feedback("Status", state=state, summary="No result is available.")


def test_feedback_requires_complete_significant_and_ai_metadata() -> None:
    with pytest.raises(ValueError, match="both authority and recovery"):
        render_user_feedback(
            "Publish",
            state="ready",
            summary="The release is ready.",
            authority="Only the owner can publish it.",
        )
    with pytest.raises(ValueError, match="AI assistance metadata is missing"):
        render_user_feedback(
            "Draft",
            state="ready",
            summary="The draft is ready.",
            ai_assistance={"limits": "Human review remains required."},
        )


def test_project_summary_uses_semantic_order_and_an_honest_next_action() -> None:
    rendered = render_project_summary(
        ProjectRecord(
            project_id="apple-surface",
            title="Apple Surface",
            system_version="0.1.0-alpha.8",
        )
    )

    assert rendered.index("## Current status") < rendered.index("## What needs attention")
    assert rendered.index("## What needs attention") < rendered.index("## Next action")
    assert "Set the current phase" in rendered
    assert "read-only view" in rendered
    assert not any(
        term in rendered.casefold() for term in ("provider:", "component_id", "dot_group")
    )
