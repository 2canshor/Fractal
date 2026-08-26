"""Candidate-only projections of canonical System Actions.

The canonical capability graph is owned by Fractal's System contracts.  A
platform Skill is only a transport representation of one Action: it gives a
platform a name, description, and job contract, while retaining references to
the canonical Action and its Workflows.  Dots, Implementations, providers,
and raw Sources remain below that boundary.

This module deliberately has no install, activation, Workplace, or live
adapter operation.  ``project_candidate_graph`` writes only to the empty path
that its caller supplies.  The returned manifest is a staged candidate and
keeps the current active surface as recovery metadata when one is supplied.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fractal.capability_action import ActionValidationError, validate_action_graph
from fractal.capability_dot import CapabilityDotError, validate_capability_dot
from fractal.capability_workflow import WorkflowValidationError, validate_workflow

PROJECTION_RECORD_TYPE = "capability-platform-projection"
PROJECTION_RECORD_VERSION = 1
SKILL_RECORD_TYPE = "generated-platform-skill"
SKILL_RECORD_VERSION = 1
AUDIT_RECORD_TYPE = "capability-platform-projection-audit"
AUDIT_RECORD_VERSION = 1

STAGED_NOT_ACTIVE = "staged-not-active"
PROJECTION_STATUS = STAGED_NOT_ACTIVE

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PLATFORM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SKILL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RESERVED_SKILL_NAMES = frozenset({".", "..", "manifest", "skills"})

# These fields are authority-bearing at lower layers and must not occur in a
# generated Skill transport record.  ``skill_name`` is intentionally absent:
# the output itself is a Skill projection and has one derived name.
_LEAK_KEYS = frozenset(
    {
        "source",
        "source_id",
        "source_ref",
        "source_refs",
        "sources",
        "dot",
        "dot_id",
        "dot_ref",
        "dot_refs",
        "dots",
        "implementation",
        "implementation_id",
        "implementation_ref",
        "implementation_refs",
        "implementations",
        "provider",
        "provider_id",
        "provider_ref",
        "provider_refs",
        "provider_selection",
        "provider_implementation",
        "provider_specific",
        "platform_skill",
        "platform_skill_id",
        "platform_skill_ref",
        "action_authority",
        "workflow_authority",
    }
)


class CapabilityProjectionError(ValueError):
    """Base error for a malformed candidate projection."""


class ProjectionInputError(CapabilityProjectionError):
    """Raised when a candidate graph or platform request is malformed."""


class ProjectionBoundaryError(CapabilityProjectionError):
    """Raised when canonical or lower-layer authority crosses the boundary."""


class ProjectionValidationError(CapabilityProjectionError):
    """Raised when candidate records or generated files fail validation."""


class ProjectionCollisionError(ProjectionValidationError):
    """Raised when two Actions map to one platform-visible name."""


class ProjectionPathError(CapabilityProjectionError):
    """Raised when an output path is unsafe or is not an empty candidate root."""


class ProjectionAuditError(ProjectionValidationError):
    """Raised when read-back projection audit fails."""


# Compatibility spellings keep the contract easy to find for callers that use
# the neighbouring capability modules' naming conventions.
CapabilityPlatformProjectionError = CapabilityProjectionError
CapabilityProjectionValidationError = ProjectionValidationError
PlatformProjectionError = CapabilityProjectionError
PlatformProjectionValidationError = ProjectionValidationError


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionInputError(f"{label} must be a non-empty string")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProjectionInputError("projection values must be portable JSON") from error


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normal_key(value: Any) -> str:
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectionInputError(f"{label} must be an object")
    return dict(value)


def _records(value: Any, identity: str, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if value.get(identity) is not None:
            return [_copy(dict(value))]
        result: list[dict[str, Any]] = []
        for key, raw in value.items():
            if not isinstance(raw, Mapping):
                raise ProjectionInputError(f"{label} entries must be objects")
            item = _copy(dict(raw))
            if item.get(identity) is None and isinstance(key, str):
                item[identity] = key
            result.append(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise ProjectionInputError(f"{label}[{index}] must be an object")
            result.append(_copy(dict(raw)))
        return result
    raise ProjectionInputError(f"{label} must be an object, list, or id-indexed mapping")


def _section(
    graph: Mapping[str, Any], aliases: Sequence[str], identity: str
) -> list[dict[str, Any]]:
    supplied = [graph[name] for name in aliases if name in graph]
    if not supplied:
        return []
    if len(supplied) > 1 and _sha256(supplied[0]) != _sha256(supplied[1]):
        raise ProjectionInputError(
            f"candidate graph has conflicting {identity} section aliases"
        )
    return _records(supplied[0], identity, identity)


def _status(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        state = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(state, str):
            return state
    state = record.get("status", record.get("state"))
    return state if isinstance(state, str) else None


def _assert_candidate_lifecycle(record: Mapping[str, Any], kind: str) -> None:
    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise ProjectionBoundaryError(f"Candidate {kind} requires a lifecycle object")
    if _status(record) != "candidate":
        raise ProjectionBoundaryError(f"Candidate {kind} must have candidate lifecycle")
    if lifecycle.get("active") is True or lifecycle.get("active_surface") is True:
        raise ProjectionBoundaryError(f"Candidate {kind} cannot be active")
    if lifecycle.get("candidate") is not None and lifecycle.get("candidate") is not True:
        raise ProjectionBoundaryError(f"Candidate {kind} must attest candidate=true")
    if kind in {"Action", "Dot"}:
        activation = record.get("activation")
        if not isinstance(activation, Mapping) or activation.get("status") != "inactive":
            raise ProjectionBoundaryError(
                f"Candidate {kind} must have inactive activation"
            )


def _reject_legacy_graph_fields(graph: Mapping[str, Any]) -> None:
    legacy_keys = {
        "legacy",
        "legacy_actions",
        "legacy_workflows",
        "old_actions",
        "old_workflows",
        "fallback_actions",
        "active_actions",
        "active_surface",
    }
    present = sorted(key for key in graph if _normal_key(key) in legacy_keys)
    if present:
        raise ProjectionBoundaryError(
            "legacy or active surface records must be supplied only as fallback metadata: "
            + ", ".join(present)
        )


def _reject_raw_sources(graph: Mapping[str, Any]) -> None:
    """Keep raw Sources outside the projected Action/Workflow boundary.

    A migration audit may retain complete Source records for provenance.  They
    are accepted as graph evidence, but a raw Source nested in an Action,
    Workflow, or Dot is rejected by the canonical validators and must never be
    rendered into a platform Skill.
    """

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            record_type = value.get("record_type")
            if record_type == "capability-source" and path.startswith(
                (
                    "$.actions",
                    "$.candidate_actions",
                    "$.workflows",
                    "$.candidate_workflows",
                    "$.dots",
                    "$.candidate_dots",
                )
            ):
                raise ProjectionBoundaryError(
                    f"raw Source content cannot sit in a projected contract ({path})"
                )
            for key, child in value.items():
                if (
                    _normal_key(key) in {"source", "source_record", "source_definition"}
                    and isinstance(child, Mapping)
                    and child.get("record_type") == "capability-source"
                    and path.startswith(
                        (
                            "$.actions",
                            "$.candidate_actions",
                            "$.workflows",
                            "$.candidate_workflows",
                            "$.dots",
                            "$.candidate_dots",
                        )
                    )
                ):
                    raise ProjectionBoundaryError(
                        f"raw Source content is not allowed ({path}.{key})"
                    )
                walk(child, f"{path}.{key}")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(graph)


def _candidate_sections(
    candidate_graph: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    graph = _as_mapping(candidate_graph, "candidate_graph")
    record_type = graph.get("record_type")
    if record_type is not None and record_type != "capability-candidate-graph":
        raise ProjectionInputError(
            "candidate_graph must be a canonical capability-candidate-graph"
        )
    if graph.get("candidate_only") is False:
        raise ProjectionBoundaryError("candidate graph must be candidate-only")
    _reject_legacy_graph_fields(graph)
    _reject_raw_sources(graph)
    actions = _section(graph, ("actions", "candidate_actions"), "action_id")
    workflows = _section(graph, ("workflows", "candidate_workflows"), "workflow_id")
    dots = _section(graph, ("dots", "candidate_dots"), "dot_id")
    if not actions:
        raise ProjectionInputError("candidate_graph must contain at least one Candidate Action")
    if not workflows:
        raise ProjectionInputError("candidate_graph must contain Candidate Workflows")
    if not dots:
        raise ProjectionInputError("candidate_graph must contain Candidate Dots")

    for dot in dots:
        _assert_candidate_lifecycle(dot, "Dot")
        try:
            validate_capability_dot(dot, require_active=False)
        except (CapabilityDotError, ValueError, TypeError) as error:
            raise ProjectionValidationError(
                "candidate graph contains an invalid Candidate Dot"
            ) from error
    for workflow in workflows:
        _assert_candidate_lifecycle(workflow, "Workflow")
        try:
            validate_workflow(workflow, dot_records=dots)
        except (WorkflowValidationError, ValueError, TypeError) as error:
            raise ProjectionValidationError(
                "candidate graph contains an invalid Candidate Workflow"
            ) from error
    for action in actions:
        _assert_candidate_lifecycle(action, "Action")
        if "platform_projections" in action or "projections" in action:
            raise ProjectionBoundaryError(
                "Candidate Actions cannot contain a platform projection"
            )
    try:
        actions = validate_action_graph(actions, workflow_records=workflows)
    except (ActionValidationError, ValueError, TypeError) as error:
        raise ProjectionValidationError(
            "candidate graph contains an invalid Candidate Action or reference"
        ) from error
    seen_action_ids: set[str] = set()
    for action in actions:
        action_id = action.get("action_id")
        if action_id in seen_action_ids:
            raise ProjectionValidationError(f"duplicate Candidate Action: {action_id}")
        seen_action_ids.add(action_id)
    return graph, actions, workflows, dots


def _platform(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("platform", value.get("platform_id", value.get("id")))
    text = _nonblank(value, "platform").strip().casefold()
    if _CONTROL_PATTERN.search(text) or _PLATFORM_PATTERN.fullmatch(text) is None:
        raise ProjectionInputError(
            "platform must be a lowercase stable identifier without path separators"
        )
    return text


def _display_text(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _CONTROL_PATTERN.search(text):
        raise ProjectionValidationError(f"{label} contains control characters")
    return " ".join(text.split())


def _name_key(value: str) -> str:
    normal = unicodedata.normalize("NFKC", value).casefold()
    normal = re.sub(r"[^\w]+", " ", normal, flags=re.UNICODE)
    return " ".join(normal.split())


def _skill_name(human_name: str, action_id: str) -> str:
    normal = unicodedata.normalize("NFKD", human_name).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", normal.casefold())
    if not tokens:
        tokens = re.findall(r"[a-z0-9]+", action_id.casefold())
    if not tokens:
        raise ProjectionCollisionError(
            f"Action {action_id!r} has no deterministic platform-safe name"
        )
    slug = "-".join(tokens)
    if len(slug) > 64:
        suffix = hashlib.sha256(human_name.encode("utf-8")).hexdigest()[:11]
        slug = f"{slug[:52].rstrip('-')}-{suffix}"
    if slug in _RESERVED_SKILL_NAMES or _SKILL_PATTERN.fullmatch(slug) is None:
        raise ProjectionValidationError(f"invalid generated Skill name: {slug!r}")
    return slug


def _check_name_collisions(actions: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    slugs: dict[str, str] = {}
    for action in actions:
        action_id = _nonblank(action.get("action_id"), "Action action_id")
        human_name = _display_text(action.get("human_name"), f"Action {action_id}.human_name")
        semantic_name = _name_key(human_name)
        if semantic_name in names:
            raise ProjectionCollisionError(
                "Candidate Actions with the same human name cannot share a projection: "
                f"{names[semantic_name]!r} and {action_id!r}"
            )
        names[semantic_name] = action_id
        slug = _skill_name(human_name, action_id)
        if slug in slugs:
            raise ProjectionCollisionError(
                f"Candidate Actions collide under platform name {slug!r}: "
                f"{slugs[slug]!r} and {action_id!r}"
            )
        slugs[slug] = action_id
    return {action_id: _skill_name(_display_text(action["human_name"], "human_name"), action_id)
            for action_id, action in ((item["action_id"], item) for item in actions)}


def _evidence_refs(value: Any) -> list[str]:
    """Collect evidence ids without carrying evidence records or prose."""

    found: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normal = _normal_key(key)
                if normal in {"evidence_ids", "evidence_refs"} or normal.endswith(
                    "_evidence_ids"
                ):
                    if isinstance(child, Sequence) and not isinstance(
                        child, (str, bytes, bytearray)
                    ):
                        found.update(
                            str(entry)
                            for entry in child
                            if isinstance(entry, str) and entry.strip()
                        )
                    continue
                walk(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                walk(child)

    walk(value)
    return sorted(found)


def _action_workflow_refs(action: Mapping[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for ref in action.get("workflow_refs", []):
        if not isinstance(ref, Mapping):
            raise ProjectionValidationError("Action workflow_refs must contain objects")
        refs.append(
            {
                "workflow_id": str(ref["workflow_id"]),
                "version": str(ref["version"]),
            }
        )
    return refs


def _success_text(value: Any) -> Any:
    # Action validation already rejects lower-layer identities.  Retain the
    # canonical success contract, but do not add any transport-only fields.
    return _copy(value)


def _skill_payload(action: Mapping[str, Any], platform: str, skill_name: str) -> dict[str, Any]:
    action_id = str(action["action_id"])
    version = str(action["version"])
    human_name = _display_text(action["human_name"], f"Action {action_id}.human_name")
    intent = action.get("human_intent")
    if not isinstance(intent, Mapping):
        raise ProjectionValidationError(f"Action {action_id} has no human intent")
    description = _display_text(
        intent.get("statement", intent.get("familiar")),
        f"Action {action_id}.human_intent.statement",
    )
    action_ref = {"action_id": action_id, "version": version}
    workflow_refs = _action_workflow_refs(action)
    evidence_refs = _evidence_refs(
        {
            "verification": action.get("verification"),
            "system_review": action.get("system_review"),
            "human_decision": action.get("human_decision"),
            "recovery": action.get("recovery"),
            "induction_evidence": action.get("induction_evidence"),
        }
    )
    return {
        "record_type": SKILL_RECORD_TYPE,
        "record_version": SKILL_RECORD_VERSION,
        "transport_only": True,
        "authority": "derived",
        "canonical": False,
        "platform": platform,
        "name": human_name,
        "skill_name": skill_name,
        "description": description,
        "action_ref": action_ref,
        "workflow_refs": workflow_refs,
        "evidence_refs": evidence_refs,
        "job_contract": {
            "action_ref": _copy(action_ref),
            "workflow_refs": _copy(workflow_refs),
            "outcome": _success_text(action.get("success_family")),
            "inputs": _copy(action.get("inputs", [])),
            "outputs": _copy(action.get("outputs", [])),
            "completion": _success_text(action.get("success_family")),
            "authority_boundary": (
                "This platform representation cannot install, activate, or replace "
                "the canonical Action."
            ),
        },
    }


def _skill_text(payload: Mapping[str, Any]) -> str:
    projection_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (
        "---\n"
        + "name: "
        + str(payload["skill_name"])
        + "\n"
        + "description: "
        + json.dumps(str(payload["description"]), ensure_ascii=False)
        + "\n"
        + "metadata:\n"
        + "  fractal_projection: "
        + projection_json
        + "\n---\n\n# "
        + str(payload["name"])
        + "\n\n"
        + str(payload["description"])
        + "\n"
    )


def _parse_skill_text(value: str, label: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.startswith("---\n"):
        raise ProjectionAuditError(f"{label} is missing Skill frontmatter")
    marker = "\n---\n"
    end = value.find(marker, 4)
    if end < 0:
        raise ProjectionAuditError(f"{label} has an unterminated Skill frontmatter block")
    lines = value[4:end].splitlines()
    if len(lines) < 4 or not lines[0].startswith("name: "):
        raise ProjectionAuditError(f"{label} frontmatter has no Skill name")
    if not lines[1].startswith("description: ") or lines[2] != "metadata:":
        raise ProjectionAuditError(f"{label} frontmatter has no description metadata")
    projection_line = "  fractal_projection: "
    raw = next(
        (
            line[len(projection_line) :]
            for line in lines
            if line.startswith(projection_line)
        ),
        None,
    )
    if raw is None:
        raise ProjectionAuditError(f"{label} frontmatter has no Fractal projection metadata")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProjectionAuditError(f"{label} frontmatter is not JSON") from error
    if not isinstance(parsed, Mapping):
        raise ProjectionAuditError(f"{label} frontmatter must be an object")
    return dict(parsed)


def _leak_keys(value: Any, *, path: str = "$") -> list[str]:
    leaks: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _normal_key(raw_key)
            if key in _LEAK_KEYS or key.startswith("provider_") or key.startswith("source_"):
                leaks.append(f"{path}.{raw_key}")
            leaks.extend(_leak_keys(child, path=f"{path}.{raw_key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            leaks.extend(_leak_keys(child, path=f"{path}[{index}]"))
    return leaks


def _safe_root(
    output_root: os.PathLike[str] | str,
    *,
    active_roots: Sequence[os.PathLike[str] | str] | None = None,
    require_empty: bool,
) -> Path:
    if output_root is None:
        raise ProjectionPathError("output_root is required and must be caller-supplied")
    raw = Path(output_root).expanduser()
    if not raw.is_absolute():
        raise ProjectionPathError("output_root must be an absolute path")
    raw_root = Path(os.path.abspath(os.fspath(raw)))

    # Check every existing path component before resolving anything.  A
    # resolved path alone would silently follow a symlink and could put a
    # candidate under a live adapter root.  macOS exposes its system temporary
    # directory through /var and /tmp symlinks; those two OS aliases are
    # harmless, while caller-created symlink components remain forbidden.
    trusted_system_aliases = {Path("/var"), Path("/tmp")}
    current = Path(raw_root.anchor)
    for part in raw_root.parts[1:]:
        current = current / part
        if current.is_symlink() and current not in trusted_system_aliases:
            raise ProjectionPathError(f"output_root cannot contain symlinks: {current}")
    root = raw_root.resolve(strict=False)

    codex_root = Path(os.path.abspath(os.fspath(Path.home() / ".codex")))
    if root == codex_root or codex_root in root.parents:
        raise ProjectionPathError("output_root cannot be ~/.codex or one of its descendants")
    for active in active_roots or ():
        active_path = Path(active).expanduser()
        if not active_path.is_absolute():
            raise ProjectionPathError("active roots must be absolute paths")
        active_path = Path(os.path.abspath(os.fspath(active_path)))
        if root == active_path or active_path in root.parents:
            raise ProjectionPathError("output_root cannot be a current active adapter root")

    if root.exists():
        if not root.is_dir():
            raise ProjectionPathError("output_root must be a directory")
        if require_empty and any(root.iterdir()):
            raise ProjectionPathError("output_root must be empty before candidate projection")
    else:
        parent = root.parent
        if not parent.exists() or not parent.is_dir():
            raise ProjectionPathError("output_root parent must already exist")
        if parent.is_symlink():
            raise ProjectionPathError("output_root parent cannot be a symlink")
        root.mkdir()
    return root


def _root_sequence(
    value: Sequence[os.PathLike[str] | str] | os.PathLike[str] | str | None,
) -> tuple[os.PathLike[str] | str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, os.PathLike)):
        return (value,)
    return tuple(value)


def _safe_child(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectionPathError(f"generated path is not confined: {relative!r}")
    target = root / path
    if target.is_symlink():
        raise ProjectionPathError(f"generated path cannot be a symlink: {relative}")
    resolved = Path(os.path.abspath(os.fspath(target)))
    if resolved != root and root not in resolved.parents:
        raise ProjectionPathError(f"generated path escapes output_root: {relative!r}")
    for parent in [root, *target.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ProjectionPathError(f"generated path contains a symlink: {parent}")
    return target


def _fallback_recovery(fallback: Any) -> dict[str, Any]:
    if fallback is None:
        return {
            "fallback_retained": True,
            "fallback_only": True,
            "available": False,
            "restore": "Keep the current active surface until a future authorised cutover.",
        }
    if not isinstance(fallback, Mapping):
        raise ProjectionInputError("fallback metadata must be an object")
    copied = _copy(dict(fallback))
    return {
        "fallback_retained": True,
        "fallback_only": True,
        "available": True,
        "surface": copied,
        "surface_digest": _sha256(copied),
        "restore": "Restore the supplied current active surface if the candidate is rejected.",
    }


def _tree_entries(root: Path, expected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in expected:
        relative = str(item["path"])
        target = _safe_child(root, relative)
        if not target.exists() or not target.is_file():
            raise ProjectionAuditError(f"generated file is missing: {relative}")
        data = target.read_bytes()
        entries.append(
            {
                "path": relative,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return sorted(entries, key=lambda item: item["path"])


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    # Recovery metadata is intentionally omitted: it can change while the
    # candidate remains byte-for-byte identical and must never seed it.
    material = {
        key: manifest.get(key)
        for key in (
            "record_type",
            "record_version",
            "platform",
            "status",
            "candidate_only",
            "install_authority",
            "activation_authority",
            "candidate_digest",
            "tree_sha256",
            "actions",
        )
    }
    return _sha256(material)


def _audit_projection(
    candidate_graph: Mapping[str, Any],
    manifest: Mapping[str, Any],
    root: Path | None,
    *,
    platform: str | None = None,
    require_manifest_file: bool = True,
) -> dict[str, Any]:
    graph, actions, _workflows, _dots = _candidate_sections(candidate_graph)
    expected_platform = _platform(platform if platform is not None else manifest.get("platform"))
    if manifest.get("record_type") != PROJECTION_RECORD_TYPE:
        raise ProjectionAuditError("projection manifest record type is invalid")
    if manifest.get("record_version") != PROJECTION_RECORD_VERSION:
        raise ProjectionAuditError("projection manifest record version is invalid")
    if manifest.get("platform") != expected_platform:
        raise ProjectionAuditError("projection platform does not match the request")
    required_flags = {
        "status": STAGED_NOT_ACTIVE,
        "candidate_only": True,
        "install_authority": False,
        "activation_authority": False,
    }
    for key, expected in required_flags.items():
        if manifest.get(key) != expected:
            raise ProjectionAuditError(f"projection manifest {key} must be {expected!r}")

    skill_names = _check_name_collisions(actions)
    expected_payloads = {
        (str(action["action_id"]), str(action["version"])): _skill_payload(
            action, expected_platform, skill_names[str(action["action_id"])]
        )
        for action in actions
    }
    expected_keys = sorted(expected_payloads)
    entries = manifest.get("actions")
    if not isinstance(entries, list):
        raise ProjectionAuditError("projection manifest actions must be a list")
    observed_keys: list[tuple[str, str]] = []
    expected_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ProjectionAuditError("projection manifest action entries must be objects")
        ref = entry.get("action_ref")
        if not isinstance(ref, Mapping):
            raise ProjectionAuditError("projection manifest action entry lacks action_ref")
        key = (str(ref.get("action_id")), str(ref.get("version")))
        observed_keys.append(key)
        expected_paths.append(str(entry.get("path")))
    if sorted(observed_keys) != expected_keys or len(observed_keys) != len(set(observed_keys)):
        raise ProjectionAuditError("each Candidate Action must be projected exactly once")
    if manifest.get("action_count") != len(actions):
        raise ProjectionAuditError("projection action_count does not match Candidate Actions")

    files = manifest.get("files", manifest.get("tree_manifest"))
    if not isinstance(files, list):
        raise ProjectionAuditError("projection tree manifest must be a list")
    if "tree_manifest" in manifest and manifest["tree_manifest"] != files:
        raise ProjectionAuditError("projection tree manifest aliases disagree")
    file_paths = [str(item.get("path")) for item in files if isinstance(item, Mapping)]
    if (
        len(file_paths) != len(files)
        or file_paths != sorted(file_paths)
        or len(file_paths) != len(set(file_paths))
    ):
        raise ProjectionAuditError("projection tree manifest paths must be unique and sorted")
    if sorted(file_paths) != sorted(expected_paths):
        raise ProjectionAuditError("projection tree manifest has extra or missing Skill files")
    expected_file_entries: list[dict[str, Any]] = []
    for entry in entries:
        ref = entry["action_ref"]
        key = (str(ref["action_id"]), str(ref["version"]))
        text = _skill_text(expected_payloads[key])
        encoded = text.encode("utf-8")
        expected_file_entries.append(
            {
                "path": str(entry["path"]),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "size": len(encoded),
            }
        )
    expected_file_entries.sort(key=lambda item: item["path"])
    if files != expected_file_entries:
        raise ProjectionAuditError(
            "projection tree manifest does not match deterministic generated Skill bytes"
        )
    if manifest.get("tree_sha256") != _sha256(files):
        raise ProjectionAuditError("projection tree manifest hash is not deterministic")
    if manifest.get("candidate_digest") != _sha256(
        [expected_payloads[key] for key in expected_keys]
    ):
        raise ProjectionAuditError("projection candidate digest does not match Action transport")
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        raise ProjectionAuditError("projection manifest digest is invalid")

    source_leaks: list[str] = []
    provider_leaks: list[str] = []
    read_back = root is None
    smoke = root is None
    if root is not None:
        _safe_root(root, require_empty=False)
        manifest_path = _safe_child(root, "manifest.json")
        if require_manifest_file and (
            not manifest_path.exists() or not manifest_path.is_file()
        ):
            raise ProjectionAuditError("projection manifest.json is missing")
        if manifest_path.exists():
            try:
                disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ProjectionAuditError("projection manifest.json cannot be read") from error
            if disk_manifest != dict(manifest):
                raise ProjectionAuditError(
                    "projection manifest read-back differs from returned manifest"
                )
        expected_files = {*expected_paths}
        if manifest_path.exists():
            expected_files.add("manifest.json")
        actual_files: set[str] = set()
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ProjectionAuditError(f"projection tree cannot contain symlinks: {relative}")
            if path.is_file():
                actual_files.add(relative)
            elif not path.is_dir():
                raise ProjectionAuditError(f"projection tree contains a non-file entry: {relative}")
        if actual_files != expected_files:
            raise ProjectionAuditError("projection tree contains extra or missing entries")
        tree = _tree_entries(root, files)
        if tree != files:
            raise ProjectionAuditError("generated file hashes do not match tree manifest")
        for key, payload in expected_payloads.items():
            action_id, version = key
            entry = next(
                item
                for item in entries
                if item["action_ref"] == {"action_id": action_id, "version": version}
            )
            path = str(entry["path"])
            text = _safe_child(root, path).read_text(encoding="utf-8")
            observed = _parse_skill_text(text, path)
            if observed != payload:
                raise ProjectionAuditError(f"generated Skill read-back differs: {path}")
            leaks = _leak_keys(observed, path=f"$.{path}")
            source_leaks.extend(item for item in leaks if "source" in item.casefold())
            provider_leaks.extend(item for item in leaks if "provider" in item.casefold())
            if leaks:
                raise ProjectionAuditError(
                    f"generated Skill leaks lower-layer authority: {', '.join(leaks)}"
                )
        read_back = True
        smoke = True

    checks = {
        "candidate_actions_exactly_once": True,
        "no_extra_user_entries": True,
        "source_leak_free": not source_leaks,
        "provider_leak_free": not provider_leaks,
        "workflow_refs_resolve": True,
        "generated_files_read_back": read_back,
        "smoke_validated": smoke,
        "fallback_is_recovery_only": bool(
            isinstance(manifest.get("recovery"), Mapping)
            and manifest["recovery"].get("fallback_only") is True
        ),
    }
    return {
        "record_type": AUDIT_RECORD_TYPE,
        "record_version": AUDIT_RECORD_VERSION,
        "status": "passed",
        "candidate_only": True,
        "platform": expected_platform,
        "action_count": len(actions),
        "checks": checks,
        "source_leaks": source_leaks,
        "provider_leaks": provider_leaks,
        "candidate_graph_digest": _sha256(
            [
                {"action_id": key[0], "version": key[1]}
                for key in expected_keys
            ]
        ),
        "graph_record_type": graph.get("record_type"),
    }


def audit_candidate_projection(
    candidate_graph: Mapping[str, Any] | os.PathLike[str] | str,
    projection: Mapping[str, Any] | os.PathLike[str] | str | None = None,
    output_root: os.PathLike[str] | str | None = None,
    *,
    platform: str | None = None,
) -> dict[str, Any]:
    """Audit a staged projection and, when supplied, its generated files.

    The normal call is ``audit_candidate_projection(graph, manifest, root)``.
    For convenience, ``audit_candidate_projection(graph, root)`` reads the
    manifest from ``root``.  The first two mappings may be reversed when a
    caller already has a manifest.
    """

    if (
        isinstance(candidate_graph, Mapping)
        and candidate_graph.get("record_type") == PROJECTION_RECORD_TYPE
    ):
        if not isinstance(projection, Mapping):
            raise ProjectionInputError("candidate graph is required for projection audit")
        candidate_graph, projection = projection, candidate_graph
    if not isinstance(candidate_graph, Mapping):
        raise ProjectionInputError("candidate_graph must be an object")
    root: Path | None = None
    manifest: Mapping[str, Any] | None = projection if isinstance(projection, Mapping) else None
    if projection is not None and not isinstance(projection, Mapping):
        root = _safe_root(projection, require_empty=False)
    if output_root is not None:
        if root is not None and Path(output_root) != root:
            raise ProjectionInputError("projection root aliases disagree")
        root = _safe_root(output_root, require_empty=False)
    if manifest is None:
        if root is None:
            raise ProjectionInputError("projection manifest or output_root is required")
        path = _safe_child(root, "manifest.json")
        if not path.exists():
            raise ProjectionAuditError("projection manifest.json is missing")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectionAuditError("projection manifest.json is not readable") from error
        if not isinstance(value, Mapping):
            raise ProjectionAuditError("projection manifest.json must contain an object")
        manifest = value
    return _audit_projection(candidate_graph, manifest, root, platform=platform)


def project_candidate_graph(
    candidate_graph: Mapping[str, Any],
    platform: str | Mapping[str, Any],
    output_root: os.PathLike[str] | str,
    *,
    fallback: Mapping[str, Any] | None = None,
    fallback_metadata: Mapping[str, Any] | None = None,
    recovery: Mapping[str, Any] | None = None,
    legacy_fallback: Mapping[str, Any] | None = None,
    current_active_surface: Mapping[str, Any] | None = None,
    active_roots: Sequence[os.PathLike[str] | str] | None = None,
    active_adapter_roots: Sequence[os.PathLike[str] | str] | None = None,
) -> dict[str, Any]:
    """Build and stage one deterministic platform projection.

    ``output_root`` must be an empty caller-owned temporary/candidate path.
    The candidate graph is validated bottom-up, and only curated Action data
    is rendered into generated Skill files.  ``fallback`` and its aliases are
    copied to recovery metadata after candidate content is built; they are not
    consulted for names, counts, matches, or any candidate digest.
    """

    fallback_values = [
        value
        for value in (
            fallback,
            fallback_metadata,
            recovery,
            legacy_fallback,
            current_active_surface,
        )
        if value is not None
    ]
    if fallback_values:
        first_fallback_digest = _sha256(fallback_values[0])
        if any(_sha256(value) != first_fallback_digest for value in fallback_values[1:]):
            raise ProjectionInputError("fallback metadata aliases disagree")
    supplied_fallback = fallback_values[0] if fallback_values else None
    platform_id = _platform(platform)
    graph, actions, _workflows, _dots = _candidate_sections(candidate_graph)
    skill_names = _check_name_collisions(actions)
    root = _safe_root(
        output_root,
        active_roots=_root_sequence(active_roots) + _root_sequence(active_adapter_roots),
        require_empty=True,
    )

    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    contents: dict[str, str] = {}
    action_entries: list[dict[str, Any]] = []
    for action in sorted(actions, key=lambda item: (str(item["action_id"]), str(item["version"]))):
        key = (str(action["action_id"]), str(action["version"]))
        payload = _skill_payload(action, platform_id, skill_names[key[0]])
        relative = f"skills/{skill_names[key[0]]}/SKILL.md"
        payloads[key] = payload
        contents[relative] = _skill_text(payload)
        data = contents[relative].encode("utf-8")
        action_entries.append(
            {
                "action_ref": {"action_id": key[0], "version": key[1]},
                "human_name": str(action["human_name"]),
                "skill_name": skill_names[key[0]],
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    tree_manifest = sorted(
        [
            {
                "path": path,
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "size": len(value.encode("utf-8")),
            }
            for path, value in contents.items()
        ],
        key=lambda item: item["path"],
    )
    candidate_digest = _sha256([payloads[key] for key in sorted(payloads)])
    tree_sha256 = _sha256(tree_manifest)
    manifest: dict[str, Any] = {
        "record_type": PROJECTION_RECORD_TYPE,
        "record_version": PROJECTION_RECORD_VERSION,
        "platform": platform_id,
        "status": STAGED_NOT_ACTIVE,
        "candidate_only": True,
        "lifecycle": {"status": "candidate", "active": False, "active_surface": False},
        "install_authority": False,
        "activation_authority": False,
        "authority": "derived",
        "canonical_action_authority": "system-owned",
        "candidate_graph_record_type": graph.get("record_type"),
        "candidate_digest": candidate_digest,
        "action_count": len(actions),
        "actions": action_entries,
        "files": tree_manifest,
        "tree_manifest": _copy(tree_manifest),
        "tree_sha256": tree_sha256,
        "manifest_path": "manifest.json",
        "recovery": _fallback_recovery(supplied_fallback),
    }
    manifest["manifest_digest"] = _manifest_digest(manifest)

    # The root was proved empty and confined before any write.  All generated
    # paths are derived from the validated platform-safe names.
    for relative, text in contents.items():
        target = _safe_child(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink() or target.exists():
            raise ProjectionPathError(f"generated target is not a fresh regular file: {relative}")
        target.write_text(text, encoding="utf-8", newline="\n")

    audit = _audit_projection(
        candidate_graph,
        manifest,
        root,
        platform=platform_id,
        require_manifest_file=False,
    )
    manifest["audit"] = audit
    manifest_path = _safe_child(root, "manifest.json")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ProjectionPathError("manifest target is not fresh")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # Read the final manifest and generated Skills once more.  This catches a
    # partial write or an encoding/path surprise before returning the staged
    # receipt to the caller.
    final_audit = audit_candidate_projection(
        candidate_graph, output_root=root, platform=platform_id
    )
    if final_audit != audit:
        raise ProjectionAuditError("projection audit changed during final read-back")
    manifest_readback = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_readback, Mapping):
        raise ProjectionAuditError("projection manifest read-back is not an object")
    return _copy(dict(manifest_readback))


# Descriptive aliases make the one candidate-only implementation discoverable
# without creating alternate mutation paths.
build_candidate_projection = project_candidate_graph
compile_platform_projection = project_candidate_graph
generate_platform_projection = project_candidate_graph
project_capability_graph = project_candidate_graph
project_candidate_actions = project_candidate_graph
project_candidate_skills = project_candidate_graph
project_platform_capabilities = project_candidate_graph
build_platform_projection = project_candidate_graph
generate_candidate_projection = project_candidate_graph
stage_candidate_projection = project_candidate_graph
stage_platform_projection = project_candidate_graph
audit_projection = audit_candidate_projection
audit_staged_projection = audit_candidate_projection
audit_platform_projection = audit_candidate_projection
validate_projection = audit_candidate_projection
validate_platform_projection = audit_candidate_projection


__all__ = [
    "AUDIT_RECORD_TYPE",
    "AUDIT_RECORD_VERSION",
    "CapabilityPlatformProjectionError",
    "CapabilityProjectionError",
    "CapabilityProjectionValidationError",
    "PlatformProjectionError",
    "PlatformProjectionValidationError",
    "PROJECTION_RECORD_TYPE",
    "PROJECTION_RECORD_VERSION",
    "PROJECTION_STATUS",
    "ProjectionAuditError",
    "ProjectionBoundaryError",
    "ProjectionCollisionError",
    "ProjectionInputError",
    "ProjectionPathError",
    "ProjectionValidationError",
    "SKILL_RECORD_TYPE",
    "SKILL_RECORD_VERSION",
    "STAGED_NOT_ACTIVE",
    "audit_candidate_projection",
    "audit_platform_projection",
    "audit_projection",
    "audit_staged_projection",
    "build_candidate_projection",
    "build_platform_projection",
    "compile_platform_projection",
    "generate_candidate_projection",
    "generate_platform_projection",
    "project_candidate_actions",
    "project_candidate_graph",
    "project_candidate_skills",
    "project_capability_graph",
    "project_platform_capabilities",
    "stage_candidate_projection",
    "stage_platform_projection",
    "validate_platform_projection",
    "validate_projection",
]
