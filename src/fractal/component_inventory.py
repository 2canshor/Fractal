"""Deterministic inventory builder for Fractal-governed platform components."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fractal.component_governance import (
    active_components,
    is_transient_component_path,
    load_component_registry,
    tree_sha256,
)
from fractal.review_contracts import ReviewContractError, validate_claim_receipt
from fractal.storage import value_sha256


def technical_id(kind: str, external_identifier: str) -> str:
    """Create a Naming-System-compatible Fractal registry key."""
    words = re.sub(r"[^a-z0-9]+", "-", external_identifier.lower()).strip("-")
    if not words:
        words = hashlib.sha256(external_identifier.encode()).hexdigest()[:12]
    return f"{kind}-{words}"


def projection_tree_sha256(root: Path) -> str:
    """Match the generated adapter's canonical tree-manifest digest."""
    manifest = {}
    for path in sorted(Path(root).rglob("*")):
        if is_transient_component_path(path.relative_to(root)):
            continue
        if path.is_symlink():
            raise ValueError(f"Projection source contains a symlink: {path}")
        if path.is_file():
            manifest[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return value_sha256(manifest)


def _frontmatter(path: Path) -> dict[str, str]:
    """Parse the bounded top-level YAML subset used by Skill frontmatter.

    Literal and folded scalars are handled explicitly so `|` and `>` can never
    leak into the user-visible description. Nested metadata remains out of scope.
    """
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return values
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                scalar_lines: list[str] = []
                index += 1
                while index < len(lines):
                    scalar_line = lines[index]
                    if scalar_line.strip() == "---":
                        break
                    if scalar_line and not scalar_line.startswith((" ", "\t")):
                        break
                    scalar_lines.append(scalar_line.lstrip())
                    index += 1
                parsed = (
                    "\n".join(scalar_lines).strip()
                    if value.startswith("|")
                    else " ".join(item.strip() for item in scalar_lines).strip()
                )
                if parsed:
                    values[key] = parsed
                continue
            parsed = value.strip('"').strip("'").strip()
            if parsed and parsed not in {"|", ">"}:
                values[key] = parsed
        index += 1
    return values


def _record(
    *,
    component_id: str,
    human_name: str,
    kind: str,
    disposition: str,
    external_identifier: str | None,
    owner_id: str,
    source_controlled_by_owner: bool,
    source_kind: str,
    source_locator: str,
    version: str | None,
    content_sha256: str | None,
    naming_control: str,
    permission_profile: str,
    operations: list[str],
    secret_boundary: str,
    trigger_mode: str,
    trigger_description: str,
    discoverable: bool,
    active: bool,
    execution: str,
    evidence_ids: list[str],
    platforms: list[str],
    projection_mode: str,
    projection_target: str | None,
    projection_sha256: str | None,
    overlap_decision: str,
    overlap_with: list[str],
    removal: str,
    restore: str,
    dependencies: list[str] | None = None,
    surface_audience: str | None = None,
    invocation: dict[str, bool] | None = None,
    job_contract: dict[str, Any] | None = None,
    claim_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    external = external_identifier is not None
    if surface_audience is None:
        if disposition == "inactive-quarantined":
            surface_audience = "technical-quarantine"
        elif disposition == "platform-managed-adapter":
            surface_audience = "platform-owned-external"
        else:
            surface_audience = "supporting-capability"
    invocation_value = (
        invocation
        if invocation is not None
        else {
            "automatic_matching": active and trigger_mode == "automatic",
            "explicit_invocation": active
            and trigger_mode in {"automatic", "explicit", "platform"},
        }
    )
    if claim_receipt is not None:
        try:
            claim_receipt = validate_claim_receipt(claim_receipt)
        except ReviewContractError as error:
            raise ValueError(f"Invalid component Claim Gate receipt: {component_id}") from error
        if (
            claim_receipt["subject_id"] != component_id
            or claim_receipt["asserted_state"] != "verified-live"
        ):
            raise ValueError(f"Component Claim Gate scope mismatch: {component_id}")
    elif execution == "verified-live":
        execution = "available-unverified"
        evidence_ids = [*evidence_ids, "claim-gate:verified-live-receipt-missing"]
    return {
        "component_id": component_id,
        "human_name": human_name,
        "kind": kind,
        "disposition": disposition,
        "external_identifier": external_identifier,
        "dependencies": sorted(set(dependencies or [])),
        "owner": {
            "owner_id": owner_id,
            "source_controlled_by_owner": source_controlled_by_owner,
        },
        "source": {
            "kind": source_kind,
            "locator": source_locator,
            "version": version,
            "content_sha256": content_sha256,
        },
        "naming": {
            "registry_key_status": "passed",
            "external_identifier_status": ("exempt-external" if external else "not-applicable"),
            "exemption_reason": (
                "External publisher or platform protocol controls this identifier."
                if external
                else None
            ),
        },
        "permissions": {
            "profile": permission_profile,
            "operations": sorted(set(operations)),
            "secret_boundary": secret_boundary,
        },
        "trigger": {"mode": trigger_mode, "description": trigger_description},
        "surface_audience": surface_audience,
        "invocation": invocation_value,
        "job_contract": job_contract,
        "status": {
            "discoverable": discoverable,
            "active": active,
            "execution": execution,
            "evidence_ids": sorted(set(evidence_ids)),
            "claim_receipt": claim_receipt,
        },
        "platforms": sorted(set(platforms)),
        "projection": {
            "mode": projection_mode,
            "target": projection_target,
            "expected_sha256": projection_sha256,
        },
        "verification_evidence": sorted(set(evidence_ids)),
        "overlap": {
            "decision": overlap_decision,
            "with": sorted(set(overlap_with)),
        },
        "recovery": {"removal": removal, "restore": restore},
    }


def _skill_record(
    definition: dict[str, Any],
    skill_path: Path,
    *,
    source_kind: str,
    source_locator: str,
) -> dict[str, Any]:
    metadata = _frontmatter(skill_path / "SKILL.md")
    external_identifier = definition.get("external_identifier") or metadata.get("name")
    naming_control = definition.get("naming_control", "external")
    component_id = definition.get("component_id") or (
        technical_id("skill", external_identifier)
        if naming_control == "external"
        else external_identifier
    )
    active = definition.get("active", True)
    disposition = definition["disposition"]
    projection_mode = definition["projection_mode"]
    projection_hash = (
        projection_tree_sha256(skill_path)
        if active and projection_mode == "generated-copy"
        else None
    )
    if active and projection_mode == "platform-reference":
        projection_hash = tree_sha256(skill_path)
    return _record(
        component_id=component_id,
        human_name=definition.get("human_name") or external_identifier.replace("-", " ").title(),
        kind="skill",
        disposition=disposition,
        external_identifier=external_identifier if naming_control == "external" else None,
        owner_id=definition["owner_id"],
        source_controlled_by_owner=definition.get("source_controlled_by_owner", False),
        source_kind=source_kind,
        source_locator=source_locator,
        version=definition.get("version") or metadata.get("version") or "unversioned",
        content_sha256=tree_sha256(skill_path),
        naming_control=naming_control,
        permission_profile=definition.get("permission_profile", "skill-declared-boundary"),
        operations=definition.get("operations", ["select", "read-instructions", "run-in-scope"]),
        secret_boundary=definition.get(
            "secret_boundary", "No secret values in registry; platform or environment owns secrets."
        ),
        trigger_mode=definition.get("trigger_mode", "explicit"),
        trigger_description=definition.get("trigger_description")
        or metadata.get("description")
        or f"Use {external_identifier} only for its registered bounded purpose.",
        discoverable=active,
        active=active,
        execution=definition.get("execution", "available-unverified"),
        evidence_ids=definition.get("evidence_ids", ["component-source-hash"]),
        platforms=definition["platforms"],
        projection_mode=projection_mode,
        projection_target=(
            definition.get("projection_target")
            or (
                f"skills/{external_identifier}"
                if projection_mode == "generated-copy"
                else str(skill_path)
            )
        ),
        projection_sha256=projection_hash,
        overlap_decision=definition.get(
            "overlap_decision",
            "Retained only for its registered trigger; Fractal canonical routing wins outside it.",
        ),
        overlap_with=definition.get("overlap_with", []),
        removal=definition.get(
            "removal", "Remove the generated projection after dependency audit."
        ),
        restore=definition.get("restore", "Restore from the pinned source locator and hash."),
        dependencies=definition.get("dependencies", []),
        surface_audience=definition.get("surface_audience"),
        invocation=definition.get("invocation"),
        job_contract=definition.get("job_contract"),
        claim_receipt=definition.get("claim_receipt"),
    )


def _tool_permissions(identifier: str) -> tuple[str, list[str]]:
    lowered = identifier.lower()
    external_write = re.search(
        r"(?:create|delete|remove|update|send|write|apply|install|archive|navigate|execute|exec)",
        lowered,
    )
    if external_write:
        return "state-changing-tool", ["read-as-needed", "change-state-with-task-authority"]
    return "read-or-compute-tool", ["read", "compute"]


def _tool_dependencies(tool: dict[str, Any]) -> list[str]:
    """Link a live Tool to the registered transport or Plugin that exposes it."""
    identifier = tool.get("name", "")
    source = tool.get("source", "")
    if identifier.startswith("mcp__computer_history__"):
        return ["mcp-computer-history"]
    if source.startswith("mcp:"):
        return [technical_id("mcp", source.removeprefix("mcp:"))]
    if source.startswith("plugin:"):
        plugin_name = source.removeprefix("plugin:")
        return [technical_id("plugin", f"{plugin_name}@openai-curated-remote")]
    if source == "codex-app":
        return [technical_id("plugin", "codex-app-tools@openai-bundled")]
    return []


def _surface_hash(platform_version: str, category: str, identifier: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "platform_version": platform_version,
                "category": category,
                "identifier": identifier,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _projected_names(
    components: list[dict[str, Any]], *, kind: str, platform: str
) -> set[str]:
    names: set[str] = set()
    for component in components:
        if component["kind"] != kind or platform not in component["platforms"]:
            continue
        if not component["status"]["active"]:
            continue
        target = component["projection"]["target"]
        if target and not target.startswith("platform:"):
            path = Path(target)
            names.add(path.stem if kind == "agent-role" else path.name)
        external = component.get("external_identifier")
        if external:
            names.add(external)
        names.add(component["component_id"])
    return names


def _surface_component_id(platform: str, category: str, identifier: str) -> str:
    prefixes = {
        "tools": f"tool-{platform}",
        "skills": f"skill-{platform}-platform",
        "agents": f"role-{platform}-platform",
        "slash_commands": f"tool-{platform}-command",
        "capabilities": f"platform-{platform}-capability",
        "mcp_servers": f"mcp-{platform}-platform",
        "plugins": f"plugin-{platform}-platform",
    }
    return technical_id(prefixes[category], identifier)


def _platform_surface_records(
    definition: dict[str, Any], components: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    platform = definition["platform"]
    snapshot = json.loads(Path(definition["snapshot"]).expanduser().read_text(encoding="utf-8"))
    version = snapshot.get("claude_code_version") or definition["platform_version"]
    owner_id = definition["owner_id"]
    skill_names = set(snapshot.get("skills", []))
    projected_skills = _projected_names(components, kind="skill", platform=platform)
    projected_roles = _projected_names(components, kind="agent-role", platform=platform)
    known_external = {
        (item["kind"], item.get("external_identifier"))
        for item in components
        if platform in item["platforms"] and item.get("external_identifier")
    }
    result: list[dict[str, Any]] = []
    categories = (
        ("tools", "tool"),
        ("skills", "skill"),
        ("agents", "agent-role"),
        ("slash_commands", "tool"),
        ("capabilities", "platform-capability"),
        ("mcp_servers", "mcp"),
        ("plugins", "plugin"),
    )
    for category, kind in categories:
        for identifier in sorted(set(snapshot.get(category, []))):
            if category == "skills" and identifier in projected_skills:
                continue
            if category == "agents" and identifier in projected_roles:
                continue
            if category == "slash_commands" and identifier in skill_names:
                continue
            if category in {"mcp_servers", "plugins"} and (kind, identifier) in known_external:
                continue
            digest = _surface_hash(version, category, identifier)
            permission_profile = {
                "tool": "platform-tool-boundary",
                "skill": "platform-skill-boundary",
                "agent-role": "bounded-platform-agent",
                "platform-capability": "platform-protocol",
                "mcp": "platform-mcp-boundary",
                "plugin": "plugin-declared-boundary",
            }[kind]
            operations = {
                "tool": list(_tool_permissions(identifier)[1]),
                "skill": ["select", "read-instructions", "run-in-scope"],
                "agent-role": ["run bounded delegated work"],
                "platform-capability": ["support registered platform messaging"],
                "mcp": ["expose registered platform Tools"],
                "plugin": ["provide registered platform components"],
            }[kind]
            overlap_with: list[str] = []
            overlap_decision = (
                "Platform item retained only for its exact external selector; Fractal "
                "capability routing and task authority still apply."
            )
            if category == "skills" and identifier == "deep-research":
                overlap_with = ["research", "skill-firecrawl-deep-research"]
                overlap_decision = (
                    "Claude's built-in deep-research workflow remains an explicit selector; "
                    "Fractal research owns ordinary evidence retrieval and Firecrawl deep "
                    "research owns the registered Firecrawl workflow."
                )
            elif category == "skills" and identifier == "verify":
                overlap_with = ["fable-judge", "fractal-verifier"]
                overlap_decision = (
                    "Claude's built-in verify selector may assist a bounded check; the "
                    "Fractal delivery gate and read-only verifier retain acceptance authority."
                )
            elif category == "skills" and identifier == "design-sync":
                overlap_with = ["interface-design"]
                overlap_decision = (
                    "Claude design-sync handles its platform workflow; interface-design owns "
                    "Fractal's general interface direction and evaluation."
                )
            result.append(
                _record(
                    component_id=_surface_component_id(platform, category, identifier),
                    human_name=(
                        f"/{identifier}"
                        if category == "slash_commands"
                        else identifier.replace("-", " ").title()
                    ),
                    kind=kind,
                    disposition="platform-managed-adapter",
                    external_identifier=(
                        f"/{identifier}" if category == "slash_commands" else identifier
                    ),
                    owner_id=owner_id,
                    source_controlled_by_owner=False,
                    source_kind="platform",
                    source_locator=f"{platform}-live-surface#{category}.{identifier}",
                    version=version,
                    content_sha256=digest,
                    naming_control="external",
                    permission_profile=permission_profile,
                    operations=operations,
                    secret_boundary=(
                        "Platform owns credentials and runtime state; Fractal stores only "
                        "registration metadata."
                    ),
                    trigger_mode="platform" if category == "capabilities" else "explicit",
                    trigger_description=(
                        f"Use {identifier} only through its exact registered {platform} "
                        "surface and within task authority."
                    ),
                    discoverable=True,
                    active=True,
                    execution=(
                        "verified-live" if category == "capabilities" else "available-unverified"
                    ),
                    evidence_ids=[f"{platform}-live-init-surface"],
                    platforms=[platform],
                    projection_mode="platform-reference",
                    projection_target=f"platform:{platform}:{category}:{identifier}",
                    projection_sha256=digest,
                    overlap_decision=overlap_decision,
                    overlap_with=overlap_with,
                    removal=(
                        "Disable through the platform adapter or platform settings; retain "
                        "uncertain platform material."
                    ),
                    restore=(
                        "Restore the exact platform version and verify the live init surface."
                    ),
                    dependencies=[],
                )
            )
    return result


def build_component_registry(policy_path: Path, output_path: Path) -> dict[str, Any]:
    """Build a complete registry from explicit roots, decisions, and live Tool snapshot."""
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []

    for source in policy.get("skill_sources", []):
        root = Path(source["root"]).expanduser()
        identifiers = source.get("skills")
        paths = (
            [root / identifier for identifier in identifiers]
            if identifiers is not None
            else [path.parent for path in sorted(root.glob("*/SKILL.md"))]
        )
        for skill_path in paths:
            identifier = skill_path.name
            overrides = source.get("overrides", {}).get(identifier, {})
            definition = {**source["defaults"], **overrides}
            definition.setdefault("external_identifier", identifier)
            locator_root = source.get("locator_root")
            locator = str(Path(locator_root) / identifier) if locator_root else str(skill_path)
            components.append(
                _skill_record(
                    definition,
                    skill_path,
                    source_kind=source["source_kind"],
                    source_locator=locator,
                )
            )

    for plugin in policy.get("plugins", []):
        root = Path(plugin["root"]).expanduser()
        plugin_identifier = plugin["external_identifier"]
        plugin_platforms = plugin.get("platforms", ["codex"])
        plugin_hash = tree_sha256(root)
        plugin_active = plugin.get("active", True)
        plugin_disposition = (
            plugin.get("disposition", "platform-managed-adapter")
            if plugin_active
            else "inactive-quarantined"
        )
        plugin_projection_mode = "platform-reference" if plugin_active else "quarantine"
        components.append(
            _record(
                component_id=technical_id("plugin", plugin_identifier),
                human_name=plugin.get("human_name", plugin_identifier),
                kind="plugin",
                disposition=plugin_disposition,
                external_identifier=plugin_identifier,
                owner_id=plugin["owner_id"],
                source_controlled_by_owner=False,
                source_kind="plugin-cache",
                source_locator=str(root),
                version=plugin["version"],
                content_sha256=plugin_hash,
                naming_control="external",
                permission_profile="plugin-declared-boundary",
                operations=["provide-registered-skills", "provide-registered-tools"],
                secret_boundary=(
                    "Plugin or connected app owns secrets; Fractal stores references only."
                ),
                trigger_mode="platform",
                trigger_description=(
                    "Available only through its registered platform Plugin surface."
                ),
                discoverable=plugin_active,
                active=plugin_active,
                execution=plugin.get("execution", "available-unverified"),
                evidence_ids=["live-plugin-inventory"],
                platforms=plugin_platforms,
                projection_mode=plugin_projection_mode,
                projection_target=str(root),
                projection_sha256=plugin_hash,
                overlap_decision=(
                    "Plugin remains platform-owned; each exposed Skill and Tool is "
                    "registered separately."
                ),
                overlap_with=[],
                removal="Disable the plugin through Codex, then retain or quarantine its cache.",
                restore="Re-enable the exact registered plugin version after permission review.",
                dependencies=[],
            )
        )
        plugin_skill_files = [
            *sorted((root / "skills").glob("*/SKILL.md")),
            *sorted((root / "workflow-skills").glob("*/SKILL.md")),
        ]
        for skill_file in plugin_skill_files:
            identifier = f"{plugin_identifier}:{skill_file.parent.name}"
            components.append(
                _skill_record(
                    {
                        "external_identifier": identifier,
                        "owner_id": plugin["owner_id"],
                        "version": plugin["version"],
                        "disposition": plugin_disposition,
                        "platforms": plugin_platforms,
                        "projection_mode": plugin_projection_mode,
                        "projection_target": str(skill_file.parent),
                        "active": plugin_active,
                        "execution": plugin.get("execution", "available-unverified"),
                        "evidence_ids": ["codex-live-skill-discovery"],
                        "trigger_mode": "platform",
                        "overlap_decision": (
                            "Plugin-prefixed selector retained; Fractal canonical routing "
                            "wins outside its explicit trigger."
                        ),
                        "dependencies": [technical_id("plugin", plugin_identifier)],
                    },
                    skill_file.parent,
                    source_kind="plugin-cache",
                    source_locator=str(skill_file.parent),
                )
            )

    for static in policy.get("static_components", []):
        components.append(_record(**static))

    for surface in policy.get("platform_surfaces", []):
        components.extend(_platform_surface_records(surface, components))

    tool_snapshot = json.loads(Path(policy["tool_snapshot"]).read_text(encoding="utf-8"))
    for tool in tool_snapshot["tools"]:
        identifier = tool["name"]
        profile, operations = _tool_permissions(identifier)
        owner_id = tool.get("owner_id", "OpenAI Codex platform")
        source_hash = hashlib.sha256(
            json.dumps(tool, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        components.append(
            _record(
                component_id=technical_id("tool", identifier),
                human_name=identifier,
                kind="tool",
                disposition="platform-managed-adapter",
                external_identifier=identifier,
                owner_id=owner_id,
                source_controlled_by_owner=False,
                source_kind="platform",
                source_locator=tool.get("source", "codex-live-tool-catalogue"),
                version=tool_snapshot["platform_version"],
                content_sha256=source_hash,
                naming_control="external",
                permission_profile=profile,
                operations=operations,
                secret_boundary="Tool host owns credentials; registry stores no secret values.",
                trigger_mode="platform",
                trigger_description=(
                    "Callable only when selected for a task within its registered "
                    "permission boundary."
                ),
                discoverable=True,
                active=True,
                execution="available-unverified",
                evidence_ids=["codex-live-tool-catalogue"],
                platforms=["codex"],
                projection_mode="platform-reference",
                projection_target=identifier,
                projection_sha256=source_hash,
                overlap_decision=(
                    "Individual Tool retained; capability-level routing does not grant "
                    "Tool authority."
                ),
                overlap_with=[],
                removal=(
                    "Remove or disable the owning platform, MCP, plugin, or adapter registration."
                ),
                restore="Restore the pinned owner registration and re-run a live Tool check.",
                dependencies=_tool_dependencies(tool),
            )
        )

    by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        component_id = component["component_id"]
        if component_id in by_id:
            suffix = hashlib.sha256(str(component["external_identifier"]).encode()).hexdigest()[:8]
            component["component_id"] = f"{component_id}-{suffix}"
        by_id[component["component_id"]] = component
    registry = {
        "record_type": "component-registry",
        "record_version": 3,
        "system_version": policy["system_version"],
        "candidate_status": policy["candidate_status"],
        "components": sorted(by_id.values(), key=lambda item: item["component_id"]),
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return load_component_registry(output)


def observe_platform_components(
    registry: dict[str, Any],
    *,
    platform: str,
    platform_home: Path,
    tool_snapshot_path: Path,
    configured_mcp: list[str],
    platform_surface_path: Path | None = None,
) -> dict[str, Any]:
    """Observe the registered live surface without reading secret values."""
    home = Path(platform_home).expanduser()
    tool_snapshot = json.loads(Path(tool_snapshot_path).read_text(encoding="utf-8"))
    tools_by_name = {item["name"]: item for item in tool_snapshot["tools"]}
    observed: list[dict[str, Any]] = []
    registered = registry["components"]
    active = active_components(registry, platform)
    surface = (
        json.loads(Path(platform_surface_path).read_text(encoding="utf-8"))
        if platform_surface_path is not None
        else None
    )
    surface_version = (
        surface.get("claude_code_version") or registry["system_version"] if surface else None
    )
    surface_values = {
        category: set(surface.get(category, [])) if surface else set()
        for category in (
            "tools",
            "skills",
            "agents",
            "slash_commands",
            "capabilities",
            "mcp_servers",
            "plugins",
        )
    }

    def add(component: dict[str, Any], content_sha256: str | None) -> None:
        observed.append(
            {
                "component_id": component["component_id"],
                "discoverable": True,
                "active": True,
                "content_sha256": content_sha256,
            }
        )

    for component in active:
        kind = component["kind"]
        target = component["projection"]["target"]
        expected_hash = component["projection"]["expected_sha256"]
        source_locator = component["source"]["locator"]
        if source_locator.startswith(f"{platform}-live-surface#"):
            surface_key = source_locator.split("#", 1)[1]
            category, identifier = surface_key.split(".", 1)
            external = component["external_identifier"]
            observed_identifier = external.removeprefix("/") if external else identifier
            if observed_identifier in surface_values.get(category, set()):
                add(
                    component,
                    _surface_hash(str(surface_version), category, observed_identifier),
                )
            continue
        if kind == "tool":
            external = component["external_identifier"]
            if external in tools_by_name:
                actual_hash = hashlib.sha256(
                    json.dumps(tools_by_name[external], ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                add(component, actual_hash)
            elif target and Path(target).expanduser().is_file():
                add(component, hashlib.sha256(Path(target).expanduser().read_bytes()).hexdigest())
            continue
        if kind == "mcp":
            if component["external_identifier"] in configured_mcp:
                source = Path(component["source"]["locator"]).expanduser()
                actual_hash = expected_hash
                if component["source"]["kind"] == "plugin-cache" and source.is_file():
                    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                add(component, actual_hash)
            continue
        if kind in {"plugin", "platform-capability"}:
            locator = (
                component["projection"]["target"]
                if kind == "platform-capability"
                else component["source"]["locator"]
            )
            source = Path(locator).expanduser()
            if source.exists():
                actual_hash = (
                    tree_sha256(source)
                    if source.is_dir()
                    else hashlib.sha256(source.read_bytes()).hexdigest()
                )
                add(component, actual_hash)
            continue
        if kind == "hook":
            hooks_path = home / ("hooks.json" if platform == "codex" else "settings.json")
            if hooks_path.is_file():
                hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
                if component["external_identifier"] in hooks.get("hooks", {}):
                    add(component, expected_hash)
            continue
        if kind == "adapter":
            root_name = {
                "codex": "AGENTS.md",
                "claude": "CLAUDE.md",
                "gemini": "GEMINI.md",
                "cowork": "PROJECT_INSTRUCTIONS.md",
            }[platform]
            if (home / root_name).exists():
                add(component, expected_hash)
            continue
        if kind == "agent-role":
            if component["disposition"] == "platform-managed-adapter":
                add(component, expected_hash)
                continue
            role_id = component["component_id"]
            suffix = ".toml" if platform == "codex" else ".md"
            if (home / "agents" / f"{role_id}{suffix}").exists():
                add(component, expected_hash)
            continue
        if kind == "skill" and target:
            path = Path(target).expanduser()
            if not path.is_absolute():
                if platform == "gemini" and path.parts[0] == "skills":
                    path = home / "config" / path
                else:
                    path = home / target
            if path.exists():
                if (
                    platform == "claude"
                    and component["source"]["kind"] == "local-snapshot"
                    and component["projection"]["mode"] == "platform-reference"
                ):
                    live_link = home / "skills" / path.name
                    if not live_link.exists() or live_link.resolve() != path.resolve():
                        continue
                resolved = path.resolve() if path.is_symlink() else path
                digest = (
                    projection_tree_sha256(resolved)
                    if component["projection"]["mode"] == "generated-copy"
                    else tree_sha256(resolved)
                )
                add(component, digest)

    skills_root = home / ("config/skills" if platform == "gemini" else "skills")
    if skills_root.is_dir():
        known_by_name = {
            Path(component["projection"]["target"]).name: component
            for component in registered
            if component["kind"] == "skill"
            and component["projection"]["target"]
            and component["projection"]["mode"]
            in {"generated-copy", "platform-reference", "quarantine"}
        }
        for path in sorted(skills_root.iterdir()):
            if path.name == ".system":
                continue
            component = known_by_name.get(path.name)
            if component is not None and not component["status"]["active"]:
                observed.append(
                    {
                        "component_id": component["component_id"],
                        "discoverable": True,
                        "active": True,
                        "content_sha256": None,
                    }
                )
            elif component is None:
                observed.append(
                    {
                        "component_id": technical_id("unmanaged-skill", path.name),
                        "discoverable": True,
                        "active": True,
                        "content_sha256": None,
                    }
                )
    if surface is not None:
        projected_skills = _projected_names(registered, kind="skill", platform=platform)
        projected_roles = _projected_names(registered, kind="agent-role", platform=platform)
        skill_names = surface_values["skills"]
        for category in surface_values:
            for identifier in sorted(surface_values[category]):
                if category == "skills" and identifier in projected_skills:
                    continue
                if category == "agents" and identifier in projected_roles:
                    continue
                if category == "slash_commands" and identifier in skill_names:
                    continue
                component_id = _surface_component_id(platform, category, identifier)
                if component_id not in {item["component_id"] for item in observed}:
                    observed.append(
                        {
                            "component_id": component_id,
                            "discoverable": True,
                            "active": True,
                            "content_sha256": _surface_hash(
                                str(surface_version), category, identifier
                            ),
                        }
                    )
    return {
        "record_type": "observed-component-set",
        "record_version": 1,
        "platform": platform,
        "components": sorted(observed, key=lambda item: item["component_id"]),
    }
