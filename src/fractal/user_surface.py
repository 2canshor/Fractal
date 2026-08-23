"""User-facing Actions and Fractal Commands over reusable internal Skill dots."""

from __future__ import annotations

import json
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class UserSurfaceError(RuntimeError):
    """Raised when a user surface leaks internals or loses a routed capability."""


def build_user_surface(
    policy_path: Path, registry: dict[str, Any], output_path: Path
) -> dict[str, Any]:
    """Compile a concise policy into an exhaustive, validated user surface."""
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    required = {
        "record_type",
        "record_version",
        "platform",
        "action_resolution",
        "entries",
        "dot_groups",
        "workflows",
        "recovery",
    }
    if set(policy) != required:
        raise UserSurfaceError("User surface policy fields are incomplete or unexpected")
    if policy["record_type"] != "user-surface-policy" or policy["record_version"] != 1:
        raise UserSurfaceError("User surface policy identity is invalid")
    active = _active_platform_skills(registry, policy["platform"])
    visible = {item["component_id"] for item in policy["entries"]}
    value = {
        "record_type": "user-surface",
        "record_version": 1,
        "system_version": registry["system_version"],
        "platform": policy["platform"],
        "action_resolution": policy["action_resolution"],
        "entries": policy["entries"],
        "dot_groups": policy["dot_groups"],
        "workflows": policy["workflows"],
        "hidden_skill_component_ids": sorted(set(active).difference(visible)),
        "recovery": policy["recovery"],
    }
    validated = validate_user_surface(value, registry)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return validated


def _active_platform_skills(
    registry: dict[str, Any], platform: str
) -> dict[str, dict[str, Any]]:
    return {
        component["component_id"]: component
        for component in registry["components"]
        if component["kind"] == "skill"
        and component["status"]["active"]
        and (platform in component["platforms"] or "shared" in component["platforms"])
    }


def load_user_surface(path: Path, registry: dict[str, Any]) -> dict[str, Any]:
    """Load one surface and validate it against the exact component registry."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_user_surface(value, registry)


def validate_user_surface(
    value: dict[str, Any], registry: dict[str, Any]
) -> dict[str, Any]:
    """Require an exhaustive allowlist and many-to-many workflow-to-dot routing."""
    schema = json.loads(
        files("fractal.schemas")
        .joinpath("user-surface.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path)
        raise UserSurfaceError(f"Invalid user surface at {location}: {errors[0].message}")
    if value["system_version"] != registry["system_version"]:
        raise UserSurfaceError("User surface and component registry System Versions disagree")

    entries = value["entries"]
    if value["action_resolution"]["route_states"] != [
        "exact",
        "partial",
        "missing",
        "unavailable",
    ]:
        raise UserSurfaceError(
            "Object-Aware Actions route states must be exact, partial, missing, and unavailable"
        )
    entry_ids = [item["entry_id"] for item in entries]
    if entry_ids != sorted(entry_ids) or len(entry_ids) != len(set(entry_ids)):
        raise UserSurfaceError("User surface entry ids must be unique and sorted")
    component_ids = [item["component_id"] for item in entries]
    if len(component_ids) != len(set(component_ids)):
        raise UserSurfaceError("One Skill cannot occupy two user-facing entries")

    dot_groups = value["dot_groups"]
    group_ids = [item["group_id"] for item in dot_groups]
    if group_ids != sorted(group_ids) or len(group_ids) != len(set(group_ids)):
        raise UserSurfaceError("Dot group ids must be unique and sorted")
    for group in dot_groups:
        if group["component_ids"] != sorted(group["component_ids"]):
            raise UserSurfaceError(f"Dot group {group['group_id']} component ids must be sorted")
    groups_by_id = {item["group_id"]: item for item in dot_groups}

    workflows = value["workflows"]
    workflow_ids = [item["workflow_id"] for item in workflows]
    if workflow_ids != sorted(workflow_ids) or len(workflow_ids) != len(set(workflow_ids)):
        raise UserSurfaceError("Workflow ids must be unique and sorted")
    hidden = value["hidden_skill_component_ids"]
    if hidden != sorted(hidden):
        raise UserSurfaceError("Hidden Skill component ids must be sorted")

    active = _active_platform_skills(registry, value["platform"])
    visible_set = set(component_ids)
    hidden_set = set(hidden)
    unknown_visible = sorted(visible_set.difference(active))
    unknown_hidden = sorted(hidden_set.difference(active))
    if unknown_visible:
        raise UserSurfaceError(f"User entries reference inactive Skills: {unknown_visible}")
    if unknown_hidden:
        raise UserSurfaceError(f"Hidden list references inactive Skills: {unknown_hidden}")
    overlap = sorted(visible_set.intersection(hidden_set))
    if overlap:
        raise UserSurfaceError(f"Skills cannot be both visible and hidden: {overlap}")
    unclassified = sorted(set(active).difference(visible_set, hidden_set))
    if unclassified:
        raise UserSurfaceError(f"User surface has unclassified active Skills: {unclassified}")
    grouped_components = {
        component_id for group in dot_groups for component_id in group["component_ids"]
    }
    unknown_grouped = sorted(grouped_components.difference(hidden_set))
    if unknown_grouped:
        raise UserSurfaceError(f"Dot groups reference non-hidden Skills: {unknown_grouped}")
    ungrouped = sorted(hidden_set.difference(grouped_components))
    if ungrouped:
        raise UserSurfaceError(f"Hidden Skills have no reusable dot group: {ungrouped}")

    entries_by_id = {item["entry_id"]: item for item in entries}
    routed_dots: Counter[str] = Counter()
    dot_entries: dict[str, set[str]] = {}
    for workflow in workflows:
        entry = entries_by_id.get(workflow["entry_id"])
        if entry is None:
            raise UserSurfaceError(
                f"Workflow {workflow['workflow_id']} does not reference a visible entry"
            )
        unknown_groups = sorted(set(workflow["dot_group_ids"]).difference(groups_by_id))
        if unknown_groups:
            raise UserSurfaceError(
                f"Workflow {workflow['workflow_id']} references unknown dot groups: "
                f"{unknown_groups}"
            )
        for component_id in resolve_workflow_dots(value, workflow):
            routed_dots[component_id] += 1
            dot_entries.setdefault(component_id, set()).add(workflow["entry_id"])
    unrouted = sorted(hidden_set.difference(routed_dots))
    if unrouted:
        raise UserSurfaceError(f"User surface has hidden Skills without a workflow: {unrouted}")

    result = json.loads(json.dumps(value))
    result["summary"] = {
        "action_count": sum(item["interface_type"] == "action" for item in entries),
        "command_count": sum(item["interface_type"] == "command" for item in entries),
        "hidden_skill_count": len(hidden),
        "reused_dot_count": sum(len(entries) > 1 for entries in dot_entries.values()),
        "workflow_count": len(workflows),
    }
    return result


def resolve_workflow_dots(
    surface: dict[str, Any], workflow: dict[str, Any]
) -> list[str]:
    """Expand reusable dot groups for one workflow without assigning dot ownership."""
    groups = {item["group_id"]: item["component_ids"] for item in surface["dot_groups"]}
    return sorted(
        {
            component_id
            for group_id in workflow["dot_group_ids"]
            for component_id in groups[group_id]
        }
    )


def build_codex_skill_config_edits(
    surface: dict[str, Any],
    listed_skills: list[dict[str, Any]],
    *,
    visible_skill_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build one recoverable Codex config replacement that hides every internal Skill."""
    visible = {item["entry_id"] for item in surface["entries"]}
    if visible_skill_paths is None:
        listed_names = {item["name"] for item in listed_skills}
        missing = sorted(visible.difference(listed_names))
        if missing:
            raise UserSurfaceError(f"Codex Skill list is missing visible entries: {missing}")
        visible_skill_paths = {
            item["name"]: str(item["path"])
            for item in listed_skills
            if item["name"] in visible
        }
    elif set(visible_skill_paths) != visible:
        raise UserSurfaceError("Candidate visible Skill paths do not match the user entries")
    config_by_path: dict[str, dict[str, Any]] = {}
    for item in listed_skills:
        path = str(item.get("path") or "")
        if not path.endswith("/SKILL.md"):
            raise UserSurfaceError(f"Codex Skill has an invalid source path: {path!r}")
        if visible_skill_paths.get(item["name"]) == path:
            continue
        config_by_path[path] = {"enabled": False, "path": path}
    return [
        {
            "keyPath": "skills.config",
            "mergeStrategy": "replace",
            "value": [config_by_path[path] for path in sorted(config_by_path)],
        }
    ]


def audit_codex_skill_surface(
    surface: dict[str, Any], listed_skills: list[dict[str, Any]]
) -> dict[str, Any]:
    """Report the real enabled selector surface without treating files as deleted."""
    visible = {item["entry_id"] for item in surface["entries"]}
    enabled = {item["name"] for item in listed_skills if item.get("enabled") is True}
    present = {item["name"] for item in listed_skills}
    unexpected = sorted(enabled.difference(visible))
    missing = sorted(visible.difference(present))
    disabled_visible = sorted(visible.intersection(present).difference(enabled))
    return {
        "record_type": "codex-user-surface-audit",
        "clean": not unexpected and not missing and not disabled_visible,
        "visible_entry_ids": sorted(visible),
        "actual_enabled_skill_names": sorted(enabled),
        "unexpected_enabled_skill_names": unexpected,
        "missing_visible_entry_ids": missing,
        "disabled_visible_entry_ids": disabled_visible,
        "source_files_deleted": False,
    }


def audit_codex_skill_path_surface(
    surface: dict[str, Any],
    listed_skills: list[dict[str, Any]],
    *,
    visible_skill_paths: dict[str, str],
    require_visible_paths: bool = True,
) -> dict[str, Any]:
    """Audit exact source paths so duplicate names and provider path drift fail closed."""
    visible = {item["entry_id"] for item in surface["entries"]}
    if set(visible_skill_paths) != visible:
        raise UserSurfaceError("Candidate visible Skill paths do not match the user entries")
    desired_by_path = {
        str(Path(path).expanduser().resolve(strict=False)): entry_id
        for entry_id, path in visible_skill_paths.items()
    }
    listed_by_path = {
        str(Path(str(item["path"])).expanduser().resolve(strict=False)): item
        for item in listed_skills
    }
    enabled_paths = {
        path for path, item in listed_by_path.items() if item.get("enabled") is True
    }
    desired_paths = set(desired_by_path)
    unexpected = sorted(enabled_paths.difference(desired_paths))
    missing = sorted(desired_paths.difference(listed_by_path)) if require_visible_paths else []
    disabled = sorted(
        path
        for path in desired_paths.intersection(listed_by_path)
        if listed_by_path[path].get("enabled") is not True
    )
    return {
        "record_type": "codex-user-surface-path-audit",
        "clean": not unexpected and not missing and not disabled,
        "visible_entry_ids": sorted(visible),
        "desired_visible_skill_paths": sorted(desired_paths),
        "actual_enabled_skill_paths": sorted(enabled_paths),
        "unexpected_enabled_skill_paths": unexpected,
        "missing_visible_skill_paths": missing,
        "disabled_visible_skill_paths": disabled,
        "source_files_deleted": False,
    }
