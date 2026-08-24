import json
from copy import deepcopy
from pathlib import Path

import pytest

from fractal.surface_symbols import surface_symbol_by_entry
from fractal.user_surface import (
    UserSurfaceError,
    audit_codex_skill_path_surface,
    audit_codex_skill_surface,
    build_codex_skill_config_edits,
    build_user_surface,
    load_user_surface,
    validate_user_surface,
)

SYMBOL_NAMES = {
    "assess": "arrow.left.arrow.right.square.fill",
    "automate": "repeat.circle.fill",
    "complete": "checkmark.square.fill",
    "create": "plus.circle.fill",
    "match": "slider.horizontal.2.square",
    "version": "arrow.clockwise.square.fill",
}


def sf_symbol(entry_id: str) -> dict:
    registered = surface_symbol_by_entry()[entry_id]
    assert registered["name"] == SYMBOL_NAMES[entry_id]
    return {
        "system": "sf-symbols",
        "name": registered["name"],
        "selection": registered["selection"],
    }


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
            skill("complete"),
            skill("create"),
            skill("dot-browser"),
            skill("dot-document"),
            skill("match"),
            skill("version"),
        ],
    }


def surface() -> dict:
    return {
        "record_type": "user-surface",
        "record_version": 2,
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
                "entry_id": "assess",
                "interface_type": "command",
                "component_id": "assess",
                "outcome": "Decide whether to continue, change, or stop one idea.",
                "symbol": sf_symbol("assess"),
            },
            {
                "entry_id": "automate",
                "interface_type": "action",
                "component_id": "automate",
                "outcome": "Make a repeated job run reliably.",
                "symbol": sf_symbol("automate"),
            },
            {
                "entry_id": "complete",
                "interface_type": "command",
                "component_id": "complete",
                "outcome": "Finish the eight-Flow New Blueprint System Review.",
                "symbol": sf_symbol("complete"),
            },
            {
                "entry_id": "create",
                "interface_type": "action",
                "component_id": "create",
                "outcome": "Make the requested outcome.",
                "symbol": sf_symbol("create"),
            },
            {
                "entry_id": "match",
                "interface_type": "command",
                "component_id": "match",
                "outcome": "Match an active Project to reality.",
                "symbol": sf_symbol("match"),
            },
            {
                "entry_id": "version",
                "interface_type": "command",
                "component_id": "version",
                "outcome": "Apply, record, activate, and publish a permitted version.",
                "symbol": sf_symbol("version"),
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
    assert next(
        item["symbol"]["name"]
        for item in validated["entries"]
        if item["entry_id"] == "match"
    ) == "slider.horizontal.2.square"


def test_user_surface_rejects_the_old_match_equal_symbol() -> None:
    changed = deepcopy(surface())
    next(item for item in changed["entries"] if item["entry_id"] == "match")["symbol"][
        "name"
    ] = "equal.square.fill"

    with pytest.raises(UserSurfaceError, match="SF Symbol drifted: match"):
        validate_user_surface(changed, registry())


def test_user_surface_rejects_duplicate_symbols() -> None:
    changed = deepcopy(surface())
    next(item for item in changed["entries"] if item["entry_id"] == "automate")["symbol"] = (
        sf_symbol("create")
    )

    with pytest.raises(UserSurfaceError, match="distinct SF Symbol"):
        validate_user_surface(changed, registry())


def test_user_surface_rejects_a_symbol_from_the_wrong_interface_class() -> None:
    changed = deepcopy(surface())
    next(item for item in changed["entries"] if item["entry_id"] == "automate")["symbol"][
        "name"
    ] = "equal.square.fill"

    with pytest.raises(UserSurfaceError, match="SF Symbol drifted: automate"):
        validate_user_surface(changed, registry())


def test_user_surface_requires_a_symbol_for_every_entry() -> None:
    changed = deepcopy(surface())
    changed["entries"][0].pop("symbol")

    with pytest.raises(UserSurfaceError, match="'symbol' is a required property"):
        validate_user_surface(changed, registry())


def test_user_surface_requires_symbol_selection_evidence() -> None:
    changed = deepcopy(surface())
    changed["entries"][0]["symbol"].pop("selection")

    with pytest.raises(UserSurfaceError, match="'selection' is a required property"):
        validate_user_surface(changed, registry())


def test_user_surface_rejects_an_unreasoned_symbol_selection() -> None:
    changed = deepcopy(surface())
    changed["entries"][0]["symbol"]["selection"]["rationale"] = "Looks good."

    with pytest.raises(UserSurfaceError, match="is too short"):
        validate_user_surface(changed, registry())


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
            "name": "complete",
            "path": "/candidate/skills/complete/SKILL.md",
            "enabled": True,
        },
        {
            "name": "create",
            "path": "/candidate/skills/create/SKILL.md",
            "enabled": True,
        },
        {
            "name": "match",
            "path": "/candidate/skills/match/SKILL.md",
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
