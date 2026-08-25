"""Read-only Human Control status derived from current Fractal/Workplace inputs.

The status surface is deliberately a projection.  It never edits a source record,
the live-state cache, a markdown file, or a generated adapter.  Callers may supply
already-loaded records (useful for an API boundary) or paths to canonical JSON
records.  Path inputs are read and, where the record carries an integrity digest,
verified before they are represented in the status model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

type JsonValue = Mapping[str, Any] | Sequence[Any]
type StatusInput = Mapping[str, Any] | Path | str

_UNSET = object()

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:token|password|secret|authorization|auth)\s*[:=])",
    re.IGNORECASE,
)

_ACTIVE_STATUSES = {
    "active",
    "activated",
    "awaiting-completion",
    "awaiting_completion",
    "blocked",
    "current",
    "in-progress",
    "in_progress",
    "open",
    "planning",
    "started",
    "promoted",
    "live",
}
_COMPLETED_STATUSES = {
    "archived",
    "cancelled",
    "canceled",
    "completed",
    "complete",
    "done",
    "rejected",
    "withdrawn",
}
_CANDIDATE_UNRESOLVED = {
    "awaiting-decision",
    "awaiting_decision",
    "candidate",
    "candidate-for-review",
    "candidate_for_review",
    "pending",
    "proposed",
    "ready",
    "staged",
    "staged-not-active",
    "staged_not_active",
}
_CANDIDATE_RESOLVED = {
    "activated",
    "active",
    "historical",
    "previously-active",
    "previously_active",
    "rejected",
    "superseded",
    "withdrawn",
}
_DECISION_STATUSES = {
    "awaiting-decision",
    "awaiting_decision",
    "candidate",
    "needs-decision",
    "needs_decision",
    "open",
    "pending",
    "proposed",
    "ready",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalise_status(value: Any) -> str | None:
    status = _text(value)
    return status.lower().replace(" ", "-") if status else None


def _version(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("version", "system_version", "active_version", "public_version"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    for key in ("system", "active", "system_version"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            candidate = _version(nested)
            if candidate:
                return candidate
    record_uri = _text(value.get("record_uri"))
    if record_uri:
        leaf = record_uri.rstrip("/").rsplit("/", 1)[-1]
        if leaf.endswith(".json"):
            leaf = leaf[:-5]
        if leaf:
            return leaf
    return None


def _source_path(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("source_path", "path", "record_path", "pointer_path"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    return None


def _source_digest(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("source_sha256", "sha256", "digest", "manifest_sha256"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    return None


def _safe_text(value: Any, *, maximum: int = 512) -> str | None:
    """Keep one bounded scalar, never a nested or binary payload."""

    candidate = _text(value)
    if (
        candidate is None
        or len(candidate) > maximum
        or "\x00" in candidate
        or _SENSITIVE_VALUE_PATTERN.search(candidate) is not None
    ):
        return None
    return candidate


def _safe_identifier(value: Any) -> str | None:
    candidate = _safe_text(value, maximum=128)
    if candidate is None or "@" in candidate:
        return None
    return candidate


def _safe_version_value(value: Any) -> str | None:
    candidate = _safe_text(value, maximum=128)
    if candidate is None or any(character.isspace() for character in candidate):
        return None
    return candidate


def _safe_status_value(value: Any) -> str | None:
    candidate = _normalise_status(value)
    if candidate is None or len(candidate) > 64 or "@" in candidate:
        return None
    return candidate


def _safe_path_value(value: Any) -> str | None:
    candidate = _safe_text(value, maximum=1_024)
    if candidate is None:
        return None
    # Evidence paths are useful, but a malformed source field must not become
    # a covert channel for credentials, emails, or arbitrary live payloads.
    if _SENSITIVE_VALUE_PATTERN.search(candidate):
        return None
    return candidate


def _safe_digest_value(value: Any) -> str | None:
    candidate = _safe_text(value, maximum=64)
    if candidate is None or len(candidate) != 64:
        return None
    if any(character not in "0123456789abcdef" for character in candidate.casefold()):
        return None
    return candidate


def _safe_issue_values(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for value in values:
        candidate = _safe_text(value, maximum=1_024)
        if candidate is None:
            continue
        # Do not echo a credential-shaped error or an email-shaped payload.
        if _SENSITIVE_VALUE_PATTERN.search(candidate):
            candidate = "Status input contains a sensitive value"
        if candidate not in result:
            result.append(candidate)
    return sorted(result)


def _safe_version_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    version = _safe_version_value(value.get("version"))
    status = _safe_status_value(value.get("status"))
    unresolved = value.get("unresolved")
    source_path = _safe_path_value(value.get("source_path"))
    source_sha256 = _safe_digest_value(value.get("source_sha256"))
    if version is not None:
        result["version"] = version
    if status is not None:
        result["status"] = status
    if isinstance(unresolved, bool):
        result["unresolved"] = unresolved
    if source_path is not None:
        result["source_path"] = source_path
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    return result or None


def _safe_decision_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key, normaliser in (
        ("id", _safe_identifier),
        ("summary", _safe_text),
        ("status", _safe_status_value),
        ("project_id", _safe_identifier),
        ("source_path", _safe_path_value),
    ):
        candidate = normaliser(value.get(key))
        if candidate is not None:
            result[key] = candidate
    return result or None


def _safe_project_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    project_id = _safe_identifier(value.get("project_id"))
    title = _safe_text(value.get("title"))
    status = _safe_status_value(value.get("status"))
    system_version = _safe_version_value(value.get("system_version"))
    source_path = _safe_path_value(value.get("source_path"))
    source_sha256 = _safe_digest_value(value.get("source_sha256"))
    if project_id is not None:
        result["project_id"] = project_id
    if title is not None:
        result["title"] = title
    if status is not None:
        result["status"] = status
    if isinstance(value.get("revision"), int) and not isinstance(value.get("revision"), bool):
        result["revision"] = value["revision"]
    if system_version is not None:
        result["system_version"] = system_version
    if isinstance(value.get("current_phase"), int) and not isinstance(
        value.get("current_phase"), bool
    ):
        result["current_phase"] = value["current_phase"]
    for key in ("decisions", "requests"):
        entries = value.get(key)
        if isinstance(entries, list):
            safe_entries = [
                summary
                for item in entries
                if (summary := _safe_decision_summary(item)) is not None
            ]
            if safe_entries:
                result[key] = safe_entries
    if source_path is not None:
        result["source_path"] = source_path
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    return result or None


def _safe_runtime_state(value: Any) -> dict[str, Any] | None:
    """Project only the small runtime identity needed by Human Control."""

    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    record_type = _safe_identifier(value.get("record_type"))
    if record_type is not None:
        result["record_type"] = record_type
    if isinstance(value.get("record_version"), int) and not isinstance(
        value.get("record_version"), bool
    ):
        result["record_version"] = value["record_version"]
    state_sha256 = _safe_digest_value(value.get("state_sha256"))
    if state_sha256 is not None:
        result["state_sha256"] = state_sha256
    live_project = value.get("project")
    if isinstance(live_project, Mapping):
        project: dict[str, Any] = {}
        project_id = _safe_identifier(live_project.get("project_id"))
        status = _safe_status_value(live_project.get("status"))
        if project_id is not None:
            project["project_id"] = project_id
        if isinstance(live_project.get("revision"), int) and not isinstance(
            live_project.get("revision"), bool
        ):
            project["revision"] = live_project["revision"]
        if status is not None:
            project["status"] = status
        if project:
            result["project"] = project
    live_system = value.get("system_version")
    if isinstance(live_system, Mapping):
        system: dict[str, Any] = {}
        version = _safe_version_value(_version(live_system))
        status = _safe_status_value(live_system.get("status"))
        if version is not None:
            system["version"] = version
        if status is not None:
            system["status"] = status
        if system:
            result["system_version"] = system
    return result or None


def _safe_component_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    component_id = _safe_identifier(value.get("component_id"))
    execution = _safe_status_value(value.get("execution"))
    if component_id is not None:
        result["component_id"] = component_id
    if isinstance(value.get("active"), bool):
        result["active"] = value["active"]
    if execution is not None:
        result["execution"] = execution
    source_path = _safe_path_value(value.get("source_path"))
    source_sha256 = _safe_digest_value(value.get("source_sha256"))
    if source_path is not None:
        result["source_path"] = source_path
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    return result or None


def _safe_status_model(value: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the output allowlist before any renderer or API caller sees data."""

    system = value.get("system") if isinstance(value.get("system"), Mapping) else {}
    workplace = value.get("workplace") if isinstance(value.get("workplace"), Mapping) else {}
    project = value.get("project") if isinstance(value.get("project"), Mapping) else {}
    runtime = value.get("runtime") if isinstance(value.get("runtime"), Mapping) else {}
    decisions = value.get("decisions") if isinstance(value.get("decisions"), Mapping) else {}
    evidence = value.get("evidence") if isinstance(value.get("evidence"), Mapping) else {}

    system_version = _safe_version_value(system.get("version"))
    system_state = _safe_status_value(system.get("state"))
    active = _safe_version_summary(workplace.get("active_system_version"))
    candidate = _safe_version_summary(workplace.get("unresolved_candidate"))
    current = _safe_project_summary(project.get("current"))
    projects = project.get("projects")
    safe_projects = (
        [summary for item in projects if (summary := _safe_project_summary(item)) is not None]
        if isinstance(projects, list)
        else []
    )
    next_decision = _safe_decision_summary(decisions.get("next"))
    decision_items = decisions.get("items")
    safe_decisions = (
        [
            summary
            for item in decision_items
            if (summary := _safe_decision_summary(item)) is not None
        ]
        if isinstance(decision_items, list)
        else []
    )
    evidence_paths = evidence.get("paths")
    safe_paths = sorted(
        {
            path
            for item in evidence_paths
            if (path := _safe_path_value(item)) is not None
        }
    ) if isinstance(evidence_paths, list) else []
    evidence_digests = evidence.get("digests")
    safe_digests = {}
    if isinstance(evidence_digests, Mapping):
        for key, digest in evidence_digests.items():
            safe_key = _safe_path_value(key)
            safe_digest = _safe_digest_value(digest)
            if safe_key is not None and safe_digest is not None:
                safe_digests[safe_key] = safe_digest
    components = evidence.get("components")
    safe_components = (
        [summary for item in components if (summary := _safe_component_summary(item)) is not None]
        if isinstance(components, list)
        else []
    )
    return {
        "record_type": "workplace-human-control-status",
        "record_version": 1,
        "system": {
            "name": "Fractal",
            "version": system_version,
            "state": system_state,
            "issues": _safe_issue_values(system.get("issues")),
        },
        "workplace": {
            "status": _safe_status_value(workplace.get("status")),
            "active_system_version": active,
            "unresolved_candidate": candidate,
            "issues": _safe_issue_values(workplace.get("issues")),
        },
        "project": {
            "current": current,
            "projects": safe_projects,
            "issues": _safe_issue_values(project.get("issues")),
        },
        "runtime": {
            "status": _safe_status_value(runtime.get("status")),
            "state": _safe_runtime_state(runtime.get("state")),
            "issues": _safe_issue_values(runtime.get("issues")),
        },
        "decisions": {
            "next": next_decision,
            "items": safe_decisions,
        },
        "evidence": {
            "paths": safe_paths,
            "digests": {key: safe_digests[key] for key in sorted(safe_digests)},
            "components": safe_components,
        },
        "issues": _safe_issue_values(value.get("issues")),
    }


def _read_json(path: Path, label: str) -> tuple[Any | None, str | None, str | None, str | None]:
    """Read JSON and return value, resolved path, file digest, and one issue."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return None, str(resolved), None, f"{label} is missing: {resolved}"
    try:
        raw = resolved.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, str(resolved), None, f"{label} is invalid: {resolved} ({error})"
    return value, str(resolved), hashlib.sha256(raw).hexdigest(), None


def _load_json_input(
    value: StatusInput | None,
    *,
    label: str,
) -> tuple[Any | None, str | None, str | None, list[str]]:
    if value is None:
        return None, None, None, []
    if isinstance(value, (Mapping, list)):
        return value, _source_path(value) if isinstance(value, Mapping) else None, None, []
    if isinstance(value, (Path, str)):
        parsed, path, digest, issue = _read_json(Path(value), label)
        return parsed, path, digest, [issue] if issue else []
    return None, None, None, [f"{label} has unsupported input type: {type(value).__name__}"]


def _version_matches(left: str | None, right: str | None) -> bool:
    """Match a public version to an optional commit-suffixed live version."""
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith(f"{right}-"):
        suffix = left[len(right) + 1 :]
        return len(suffix) >= 7 and all(character in "0123456789abcdef" for character in suffix)
    if right.startswith(f"{left}-"):
        suffix = right[len(left) + 1 :]
        return len(suffix) >= 7 and all(character in "0123456789abcdef" for character in suffix)
    return False


def _append_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _status_from_active_record(record: Mapping[str, Any]) -> str | None:
    for key in ("activation_status", "status", "state"):
        status = _normalise_status(record.get(key))
        if status:
            return status
    # VersionStore's active pointer is itself an explicit current pointer.
    if _text(record.get("manifest_sha256")) and _version(record):
        return "active"
    if _normalise_status(record.get("pointer_kind")) == "active" and _version(record):
        return "active"
    return None


def _status_from_candidate_record(record: Mapping[str, Any]) -> str | None:
    for key in ("candidate_status", "status", "state", "activation_status"):
        status = _normalise_status(record.get(key))
        if status:
            return status
    if _normalise_status(record.get("record_type")) == "candidate-system-version":
        return "candidate"
    if _normalise_status(record.get("pointer_kind")) == "candidate":
        return "candidate"
    return None


def _is_unresolved_candidate(record: Mapping[str, Any]) -> bool:
    status = _status_from_candidate_record(record)
    if status is None:
        return False
    if status in _CANDIDATE_RESOLVED:
        return False
    if status in _CANDIDATE_UNRESOLVED:
        return True
    # A candidate record with an unknown lifecycle value is not safe to call
    # resolved.  Keep it visible as an unresolved/needs-review candidate.
    return True


def _validate_sidecar_digest(path: Path, value: Any, label: str, issues: list[str]) -> str | None:
    """Validate the canonical ``record.sha256`` sidecar when present/required."""
    sidecar = path.with_name("record.sha256")
    if not sidecar.exists():
        return None
    try:
        expected = sidecar.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        _append_issue(issues, f"{label} digest is unreadable: {sidecar} ({error})")
        return None
    actual = _value_sha256(value)
    if expected != actual:
        _append_issue(issues, f"{label} digest mismatch: {path}")
    return actual


def _project_summary(
    record: Mapping[str, Any],
    *,
    source_path: str | None,
    source_file_sha256: str | None,
    issues: list[str],
) -> dict[str, Any] | None:
    project_id = _text(record.get("project_id"))
    status = _normalise_status(record.get("status"))
    if not project_id or not status:
        _append_issue(issues, "Project record is missing project_id or status")
        return None
    plan = record.get("plan")
    current_phase = plan.get("current_phase") if isinstance(plan, Mapping) else None
    digest = _source_digest(record) or source_file_sha256
    if source_path:
        canonical_path = Path(source_path)
        if canonical_path.name == "record.json":
            digest = (
                _validate_sidecar_digest(canonical_path, record, "Project record", issues) or digest
            )
    summary: dict[str, Any] = {
        "project_id": project_id,
        "title": _text(record.get("title")) or project_id,
        "status": status,
        "revision": record.get("revision"),
        "system_version": _text(record.get("system_version")),
        "current_phase": current_phase,
    }
    if isinstance(record.get("decisions"), list):
        summary["decisions"] = record["decisions"]
    if isinstance(record.get("requests"), list):
        summary["requests"] = record["requests"]
    if source_path:
        summary["source_path"] = source_path
    if digest:
        summary["source_sha256"] = digest
    return summary


def _project_inputs(
    values: Any,
    *,
    issues: list[str],
    evidence_paths: set[str],
    evidence_digests: dict[str, str],
) -> list[dict[str, Any]]:
    """Load Project records from records, files, or a neutral directory root."""
    if values is None:
        return []
    if isinstance(values, Mapping):
        nested = values.get("projects")
        if isinstance(nested, (list, tuple, set)):
            return _project_inputs(
                list(nested),
                issues=issues,
                evidence_paths=evidence_paths,
                evidence_digests=evidence_digests,
            )
        record = values.get("record") if isinstance(values.get("record"), Mapping) else values
        source_path = _source_path(record)
        summary = _project_summary(
            record,
            source_path=source_path,
            source_file_sha256=None,
            issues=issues,
        )
        if source_path:
            evidence_paths.add(source_path)
        if summary and summary.get("source_sha256") and source_path:
            evidence_digests[source_path] = summary["source_sha256"]
        return [summary] if summary else []
    if isinstance(values, (str, Path)):
        path = Path(values).expanduser()
        if path.is_dir():
            records = sorted(path.rglob("record.json"))
            if not records:
                _append_issue(issues, f"Project records are missing: {path.resolve()}")
            result: list[dict[str, Any]] = []
            for record_path in records:
                result.extend(
                    _project_inputs(
                        record_path,
                        issues=issues,
                        evidence_paths=evidence_paths,
                        evidence_digests=evidence_digests,
                    )
                )
            return result
        record, source_path, file_digest, read_issues = _load_json_input(
            path, label="Project record"
        )
        issues.extend(read_issues)
        if record is None:
            return []
        if isinstance(record, Mapping) and isinstance(record.get("projects"), list):
            return _project_inputs(
                record["projects"],
                issues=issues,
                evidence_paths=evidence_paths,
                evidence_digests=evidence_digests,
            )
        if not isinstance(record, Mapping):
            _append_issue(issues, f"Project record is not an object: {source_path or path}")
            return []
        summary = _project_summary(
            record,
            source_path=source_path,
            source_file_sha256=file_digest,
            issues=issues,
        )
        if source_path:
            evidence_paths.add(source_path)
        if summary and summary.get("source_sha256") and source_path:
            evidence_digests[source_path] = summary["source_sha256"]
        return [summary] if summary else []
    if isinstance(values, Iterable):
        result = []
        for item in values:
            result.extend(
                _project_inputs(
                    item,
                    issues=issues,
                    evidence_paths=evidence_paths,
                    evidence_digests=evidence_digests,
                )
            )
        return result
    _append_issue(issues, f"Project records have unsupported input type: {type(values).__name__}")
    return []


def _active_record_summary(
    value: StatusInput | None,
    *,
    label: str,
    issues: list[str],
    evidence_paths: set[str],
    evidence_digests: dict[str, str],
) -> tuple[dict[str, Any] | None, Mapping[str, Any] | None]:
    record, source_path, file_digest, read_issues = _load_json_input(value, label=label)
    issues.extend(read_issues)
    if value is not None and record is None:
        return None, None
    if not isinstance(record, Mapping):
        if value is not None:
            _append_issue(issues, f"{label} is not an object")
        return None, None
    if source_path:
        evidence_paths.add(source_path)
    version = _version(record)
    status = _status_from_active_record(record)
    if not version:
        _append_issue(issues, f"{label} is missing a System Version")
    if status not in {"active", "activated", "current", "promoted", "live"}:
        _append_issue(issues, f"{label} does not explicitly identify an active System Version")
    digest = _source_digest(record) or file_digest
    if source_path and digest:
        evidence_digests[source_path] = digest
    summary = {
        "version": version,
        "status": status,
    }
    if source_path:
        summary["source_path"] = source_path
    if digest:
        summary["source_sha256"] = digest
    return summary, record


def _candidate_summary(
    value: StatusInput | None,
    *,
    label: str,
    issues: list[str],
    evidence_paths: set[str],
    evidence_digests: dict[str, str],
) -> tuple[dict[str, Any] | None, Mapping[str, Any] | None]:
    record, source_path, file_digest, read_issues = _load_json_input(value, label=label)
    issues.extend(read_issues)
    if value is not None and record is None:
        return None, None
    if not isinstance(record, Mapping):
        if value is not None:
            _append_issue(issues, f"{label} is not an object")
        return None, None
    if source_path:
        evidence_paths.add(source_path)
    version = _version(record)
    status = _status_from_candidate_record(record)
    if not version:
        _append_issue(issues, f"{label} is missing a System Version")
    if status is None:
        _append_issue(issues, f"{label} is missing a candidate lifecycle status")
    unresolved = _is_unresolved_candidate(record)
    digest = _source_digest(record) or file_digest
    if source_path and digest:
        evidence_digests[source_path] = digest
    summary = {
        "version": version,
        "status": status,
        "unresolved": unresolved,
    }
    if source_path:
        summary["source_path"] = source_path
    if digest:
        summary["source_sha256"] = digest
    return summary, record


def _record_status_is_active(status: str | None) -> bool:
    return status in _ACTIVE_STATUSES and status not in _COMPLETED_STATUSES


def _decision_item(
    item: Mapping[str, Any],
    *,
    project_id: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any] | None:
    status = _normalise_status(item.get("status"))
    if status not in _DECISION_STATUSES:
        return None
    decision_id = _text(item.get("id")) or _text(item.get("decision_id"))
    summary = (
        _text(item.get("subject"))
        or _text(item.get("summary"))
        or _text(item.get("decision"))
        or _text(item.get("title"))
        or decision_id
        or "Decision requires your input"
    )
    result: dict[str, Any] = {
        "id": decision_id,
        "summary": summary,
        "status": status,
    }
    if project_id:
        result["project_id"] = project_id
    if source_path:
        result["source_path"] = source_path
    return result


def _collect_decisions(
    projects: Sequence[Mapping[str, Any]],
    explicit: Any,
    candidate: Mapping[str, Any] | None,
    *,
    issues: list[str],
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for project in projects:
        project_id = _text(project.get("project_id"))
        source_path = _text(project.get("source_path"))
        # Project summaries intentionally omit large canonical arrays.  Explicit
        # records passed through this route are handled below; this loop is for
        # summaries that retain a small ``decisions`` field from API callers.
        project_decisions = (
            project.get("decisions", []) if isinstance(project.get("decisions"), list) else []
        )
        for item in project_decisions:
            if isinstance(item, Mapping):
                decision = _decision_item(item, project_id=project_id, source_path=source_path)
                if decision:
                    decisions.append(decision)
        project_requests = (
            project.get("requests", []) if isinstance(project.get("requests"), list) else []
        )
        for item in project_requests:
            if isinstance(item, Mapping):
                decision = _decision_item(item, project_id=project_id, source_path=source_path)
                if decision:
                    decisions.append(decision)
    if explicit is not None:
        explicit_values: list[tuple[Any, str | None]] = []
        if isinstance(explicit, (Path, str)) and Path(explicit).expanduser().is_dir():
            decision_paths = sorted(Path(explicit).expanduser().rglob("*.json"))
            for decision_path in decision_paths:
                loaded, source_path, _, read_issues = _load_json_input(
                    decision_path,
                    label="Decision records",
                )
                issues.extend(read_issues)
                explicit_values.append((loaded, source_path))
        else:
            loaded, source_path, _, read_issues = _load_json_input(
                explicit,
                label="Decision records",
            )
            issues.extend(read_issues)
            explicit_values.append((loaded, source_path))
        for loaded, source_path in explicit_values:
            values: Any = loaded
            if isinstance(loaded, Mapping):
                values = loaded.get("decisions", loaded.get("items", []))
            if isinstance(values, Mapping):
                values = [values]
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, Mapping):
                        decision = _decision_item(item, source_path=source_path)
                        if decision:
                            decisions.append(decision)
            elif loaded is not None:
                _append_issue(issues, "Decision records are not a list or object")
    if candidate and candidate.get("unresolved"):
        candidate_version = candidate.get("version") or "unknown System Version"
        decisions.append(
            {
                "id": "system-version-candidate",
                "summary": f"Resolve System Version candidate {candidate_version}",
                "status": "candidate",
                "source_path": candidate.get("source_path"),
            }
        )
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in decisions:
        key = (item.get("id"), item.get("summary"), item.get("project_id"))
        unique.setdefault(key, item)
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("project_id") or ""),
            str(item.get("id") or ""),
            str(item.get("summary") or ""),
        ),
    )


def _component_summaries(
    value: Any,
    *,
    evidence_paths: set[str],
    evidence_digests: dict[str, str],
    issues: list[str],
) -> list[dict[str, Any]]:
    if value is None:
        return []
    loaded, source_path, file_digest, read_issues = _load_json_input(
        value, label="Component registry"
    )
    issues.extend(read_issues)
    if loaded is None:
        return []
    if source_path:
        evidence_paths.add(source_path)
        evidence_digests[source_path] = file_digest or _value_sha256(loaded)
    values: Any = loaded
    if isinstance(loaded, Mapping):
        values = loaded.get("components", loaded.get("items", []))
    if isinstance(values, Mapping):
        values = [values]
    if not isinstance(values, list):
        _append_issue(issues, "Component registry is not a list or object")
        return []
    result: list[dict[str, Any]] = []
    for component in values:
        if not isinstance(component, Mapping):
            _append_issue(issues, "Component registry contains a non-object entry")
            continue
        component_id = _text(component.get("component_id")) or _text(component.get("id"))
        if not component_id:
            _append_issue(issues, "Component registry entry is missing component_id")
            continue
        state = component.get("status")
        if isinstance(state, Mapping):
            execution = _text(state.get("execution"))
            active = state.get("active")
        else:
            execution = _text(component.get("execution"))
            active = component.get("active")
        item: dict[str, Any] = {
            "component_id": component_id,
            "active": active if isinstance(active, bool) else None,
            "execution": execution,
        }
        source = component.get("source")
        if isinstance(source, Mapping):
            digest = _source_digest(source)
            locator = _text(source.get("locator"))
            if digest:
                item["source_sha256"] = digest
                evidence_digests[f"component:{component_id}"] = digest
            if locator:
                item["source_path"] = locator
                evidence_paths.add(locator)
        result.append(item)
    return sorted(result, key=lambda item: item["component_id"])


def _validate_live_sources(
    live: Mapping[str, Any],
    *,
    active: Mapping[str, Any] | None,
    projects: Sequence[Mapping[str, Any]],
    issues: list[str],
) -> None:
    expected_state_sha = _text(live.get("state_sha256"))
    if expected_state_sha:
        unsigned = {key: item for key, item in live.items() if key != "state_sha256"}
        if _value_sha256(unsigned) != expected_state_sha:
            _append_issue(issues, "Live runtime state integrity failure")
    live_project = live.get("project")
    if not isinstance(live_project, Mapping):
        _append_issue(issues, "Live runtime state is missing its Project summary")
    else:
        live_id = _text(live_project.get("project_id"))
        live_revision = live_project.get("revision")
        source_path = _source_path(live_project)
        source_digest = _source_digest(live_project)
        if source_path:
            path = Path(source_path).expanduser()
            if not path.is_file():
                _append_issue(issues, f"Live Project source is missing: {path.resolve()}")
            elif source_digest:
                try:
                    source_record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    _append_issue(
                        issues,
                        f"Live Project source is invalid: {path} ({error})",
                    )
                else:
                    raw_digest = _file_sha256(path)
                    value_digest = _value_sha256(source_record)
                    if source_digest not in {raw_digest, value_digest}:
                        _append_issue(issues, f"Live Project source is stale: {path.resolve()}")
        matching = [item for item in projects if item.get("project_id") == live_id]
        if projects and not matching:
            _append_issue(issues, f"Live Project is stale or unknown: {live_id or 'missing id'}")
        if (
            matching
            and live_revision is not None
            and matching[0].get("revision") is not None
            and matching[0].get("revision") != live_revision
        ):
            _append_issue(issues, f"Live Project revision is stale: {live_id}")
        if matching and source_digest and matching[0].get("source_sha256"):
            canonical_digest = matching[0]["source_sha256"]
            if source_digest not in {canonical_digest, matching[0].get("source_file_sha256")}:
                _append_issue(issues, f"Live Project digest is stale: {live_id}")
    live_system = live.get("system_version")
    if not isinstance(live_system, Mapping):
        _append_issue(issues, "Live runtime state is missing its System Version summary")
        return
    live_version = _version(live_system)
    live_status = _normalise_status(live_system.get("status"))
    if not live_version:
        _append_issue(issues, "Live runtime System Version is missing a version")
    if live_status not in {"active", "activated"}:
        _append_issue(issues, "Live runtime System Version is not explicitly active")
    if (
        active
        and active.get("version")
        and live_version
        and not _version_matches(active["version"], live_version)
    ):
        _append_issue(
            issues,
            "Live runtime System Version does not match Workplace active System Version",
        )
    source_path = _source_path(live_system)
    source_digest = _source_digest(live_system)
    if source_path:
        pointer_path = Path(source_path).expanduser()
        if not pointer_path.is_file():
            _append_issue(
                issues,
                f"Live System Version source is missing: {pointer_path.resolve()}",
            )
        else:
            if source_digest and _file_sha256(pointer_path) != source_digest:
                _append_issue(
                    issues,
                    f"Live System Version source is stale: {pointer_path.resolve()}",
                )
            try:
                pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                _append_issue(
                    issues,
                    f"Live System Version source is invalid: {pointer_path} ({error})",
                )
            else:
                pointer_version = _version(pointer)
                if (
                    live_version
                    and pointer_version
                    and not _version_matches(live_version, pointer_version)
                ):
                    _append_issue(issues, f"Live System Version pointer is stale: {pointer_path}")
                manifest_sha = _text(pointer.get("manifest_sha256"))
                if manifest_sha and pointer_version:
                    manifest_path = pointer_path.parent / "versions" / f"{pointer_version}.json"
                    if not manifest_path.is_file():
                        _append_issue(
                            issues,
                            f"Live System Version manifest is missing: {manifest_path}",
                        )
                    else:
                        try:
                            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                            _append_issue(
                                issues,
                                "Live System Version manifest is invalid: "
                                f"{manifest_path} ({error})",
                            )
                        else:
                            stored = _text(manifest.get("manifest_sha256"))
                            unsigned = {
                                key: item
                                for key, item in manifest.items()
                                if key != "manifest_sha256"
                            }
                            if stored != manifest_sha or _value_sha256(unsigned) != stored:
                                _append_issue(
                                    issues,
                                    "Live System Version manifest integrity failure: "
                                    f"{manifest_path}",
                                )


def _default_public_version(public_system: Any) -> str | None:
    if isinstance(public_system, str) and not Path(public_system).expanduser().exists():
        return _text(public_system)
    if isinstance(public_system, Mapping):
        return _version(public_system)
    return None


def build_workplace_status(
    public_system: StatusInput | None = None,
    workplace_active: StatusInput | None = None,
    workplace_candidate: StatusInput | None = None,
    projects: Any = None,
    live_state: StatusInput | None = None,
    components: Any = None,
    decisions: Any = None,
    **aliases: Any,
) -> dict[str, Any]:
    """Build the current Human Control status without mutating any input.

    Common path-oriented aliases (``*_path`` and ``*_record``) are accepted to
    keep callers explicit while allowing gradual migration from Workplace's
    legacy names.  A supplied path is always read; an omitted optional candidate
    or project collection means that no such record is currently known.
    """
    public_system = aliases.pop("public_version", public_system)
    public_system = aliases.pop("public_system_version", public_system)
    public_system = aliases.pop("public_system_path", public_system)
    workplace_active = aliases.pop("active_system", workplace_active)
    workplace_active = aliases.pop("active_system_record", workplace_active)
    workplace_active = aliases.pop("active_system_path", workplace_active)
    workplace_active = aliases.pop("active_path", workplace_active)
    workplace_candidate = aliases.pop("candidate_system", workplace_candidate)
    workplace_candidate = aliases.pop("candidate_system_record", workplace_candidate)
    workplace_candidate = aliases.pop("candidate_system_path", workplace_candidate)
    workplace_candidate = aliases.pop("candidate_path", workplace_candidate)
    projects = aliases.pop("project_records", projects)
    projects = aliases.pop("project_paths", projects)
    projects = aliases.pop("project_root", projects)
    projects = aliases.pop("projects_path", projects)
    live_state = aliases.pop("runtime_state", live_state)
    live_state = aliases.pop("live_runtime_state", live_state)
    live_state = aliases.pop("live_state_path", live_state)
    live_state = aliases.pop("runtime_path", live_state)
    components = aliases.pop("component_registry", components)
    components = aliases.pop("component_path", components)
    components = aliases.pop("components_path", components)
    decisions = aliases.pop("decision_records", decisions)
    decisions = aliases.pop("decisions_path", decisions)
    workplace_root = aliases.pop("workplace_root", aliases.pop("root", None))
    if aliases:
        unknown = ", ".join(sorted(aliases))
        raise TypeError(f"Unknown workplace status inputs: {unknown}")

    # A Workplace root is a convenience only: deriving these paths remains
    # read-only and does not invoke migration/bootstrap.  Explicit arguments
    # always win over the derived locations.
    if workplace_root is not None:
        root = Path(workplace_root).expanduser()
        active_path = root / "system" / "active-version.json"
        candidate_path = root / "system" / "candidate-version.json"
        legacy_path = root / "workspace.json"
        if legacy_path.is_file() and not active_path.is_file():
            legacy_value, legacy_source, _, legacy_issues = _load_json_input(
                legacy_path,
                label="Legacy Workplace record",
            )
            # The legacy record is retained as evidence, but its active and
            # candidate fields are projected into the same status shape as the
            # newer pointer records.
            if isinstance(legacy_value, Mapping):
                legacy_system = legacy_value.get("system")
                if isinstance(legacy_system, Mapping):
                    if workplace_active is None:
                        workplace_active = {
                            "record_type": "active-system-version",
                            "system_version": legacy_system.get("active_version"),
                            "activation_status": "active",
                            "source_path": legacy_source,
                        }
                    if workplace_candidate is None and legacy_system.get("candidate_version"):
                        workplace_candidate = {
                            "record_type": "candidate-system-version",
                            "system_version": legacy_system.get("candidate_version"),
                            "candidate_status": "candidate",
                            "source_path": legacy_source,
                        }
            elif legacy_issues and workplace_active is None:
                workplace_active = legacy_path
        if workplace_active is None and active_path.is_file():
            workplace_active = active_path
        if workplace_candidate is None and candidate_path.is_file():
            workplace_candidate = candidate_path
        if projects is None:
            projects = root / "projects"
        if decisions is None:
            for decision_root in (root / "decisions", root / "system" / "decisions"):
                if decision_root.exists():
                    decisions = decision_root
                    break
        if components is None:
            component_registry = root / "system" / "components" / "registry.json"
            if component_registry.is_file():
                components = component_registry

    issues: list[str] = []
    system_issues: list[str] = []
    workplace_issues: list[str] = []
    runtime_issues: list[str] = []
    evidence_paths: set[str] = set()
    evidence_digests: dict[str, str] = {}

    # Public System identity is intentionally not treated as activation proof.
    public_version = _default_public_version(public_system)
    public_record: Mapping[str, Any] | None = None
    if isinstance(public_system, Mapping):
        public_record = public_system
    elif isinstance(public_system, (Path, str)) and not (
        isinstance(public_system, str) and not Path(public_system).expanduser().exists()
    ):
        loaded, source_path, file_digest, read_issues = _load_json_input(
            public_system, label="Public System record"
        )
        system_issues.extend(read_issues)
        if isinstance(loaded, Mapping):
            public_record = loaded
            public_version = _version(loaded)
            if source_path:
                evidence_paths.add(source_path)
                evidence_digests[source_path] = file_digest or _value_sha256(loaded)
        elif loaded is not None:
            _append_issue(system_issues, "Public System record is not an object")
    if not public_version:
        _append_issue(system_issues, "Public Fractal System version is missing")

    active_summary, active_record = _active_record_summary(
        workplace_active,
        label="Workplace active System record",
        issues=workplace_issues,
        evidence_paths=evidence_paths,
        evidence_digests=evidence_digests,
    )
    candidate_summary, candidate_record = _candidate_summary(
        workplace_candidate,
        label="Workplace candidate System record",
        issues=workplace_issues,
        evidence_paths=evidence_paths,
        evidence_digests=evidence_digests,
    )
    if workplace_active is None:
        _append_issue(workplace_issues, "Workplace active System record is missing")
    if (
        active_summary
        and active_summary.get("version")
        and public_version
        and not _version_matches(active_summary["version"], public_version)
    ):
        _append_issue(
            workplace_issues,
            "Workplace active System Version does not match public Fractal System",
        )
    if (
        active_summary
        and candidate_summary
        and candidate_summary.get("unresolved")
        and active_summary.get("version")
        and candidate_summary.get("version")
        and _version_matches(active_summary["version"], candidate_summary["version"])
    ):
        _append_issue(
            workplace_issues,
            "Ambiguous Workplace state: active System Version also appears "
            "as an unresolved candidate",
        )

    project_issues: list[str] = []
    project_summaries = _project_inputs(
        projects,
        issues=project_issues,
        evidence_paths=evidence_paths,
        evidence_digests=evidence_digests,
    )
    project_summaries = sorted(
        project_summaries,
        key=lambda item: str(item.get("project_id") or ""),
    )
    active_projects = [
        item for item in project_summaries if _record_status_is_active(item.get("status"))
    ]
    current_project: dict[str, Any] | None
    if len(active_projects) == 1:
        current_project = active_projects[0]
    elif len(active_projects) > 1:
        current_project = None
        _append_issue(project_issues, "Multiple active Project records require your decision")
    else:
        current_project = None

    live_record, live_source_path, live_file_digest, live_read_issues = _load_json_input(
        live_state,
        label="Live runtime state",
    )
    runtime_issues.extend(live_read_issues)
    if live_state is None:
        _append_issue(runtime_issues, "Live runtime state is missing")
    elif not isinstance(live_record, Mapping):
        _append_issue(runtime_issues, "Live runtime state is not an object")
    else:
        if live_source_path:
            evidence_paths.add(live_source_path)
            evidence_digests[live_source_path] = live_file_digest or _value_sha256(live_record)
        _validate_live_sources(
            live_record,
            active=active_summary,
            projects=project_summaries,
            issues=runtime_issues,
        )

    if public_version and isinstance(live_record, Mapping):
        live_system = live_record.get("system_version")
        live_version = _version(live_system) if isinstance(live_system, Mapping) else None
        live_status = (
            _normalise_status(live_system.get("status"))
            if isinstance(live_system, Mapping)
            else None
        )
        live_matches_public = (
            live_status in {"active", "activated"}
            and live_version is not None
            and _version_matches(public_version, live_version)
        )
        if live_status in {"active", "activated"} and live_version and not live_matches_public:
            _append_issue(
                system_issues,
                "Public Fractal System does not match verified live System Version",
            )
        system_state = "active" if live_matches_public else (
            "mismatch" if live_status in {"active", "activated"} else "unknown"
        )
    else:
        system_state = "unknown"
    if public_record:
        explicit_state = _normalise_status(
            public_record.get(
                "state",
                public_record.get("status", public_record.get("activation_status")),
            )
        )
        if (
            explicit_state in {"active", "candidate", "unknown", "mismatch"}
            and not isinstance(live_record, Mapping)
        ):
            system_state = explicit_state

    decisions_list = _collect_decisions(
        project_summaries,
        decisions,
        candidate_summary,
        issues=issues,
    )
    # A current Project's canonical record may be supplied as a mapping with a
    # decisions list.  Keep the project summary compact, but retain those items
    # for callers that provide this API-level record shape.
    if isinstance(projects, Mapping) and isinstance(projects.get("record"), Mapping):
        record_decisions = projects["record"].get("decisions")
        if isinstance(record_decisions, list):
            for item in record_decisions:
                if isinstance(item, Mapping):
                    decision = _decision_item(
                        item,
                        project_id=_text(projects["record"].get("project_id")),
                        source_path=_source_path(projects["record"]),
                    )
                    if decision:
                        decisions_list.append(decision)
    unique_decisions: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in decisions_list:
        unique_decisions[(item.get("id"), item.get("summary"), item.get("project_id"))] = item
    decisions_list = sorted(
        unique_decisions.values(),
        key=lambda item: (str(item.get("project_id") or ""), str(item.get("id") or "")),
    )
    component_issues: list[str] = []
    component_summaries = _component_summaries(
        components,
        evidence_paths=evidence_paths,
        evidence_digests=evidence_digests,
        issues=component_issues,
    )
    workplace_issues.extend(component_issues)
    for issue in system_issues + workplace_issues + project_issues + runtime_issues:
        _append_issue(issues, issue)
    workplace_status = (
        "healthy"
        if not system_issues and not workplace_issues and not project_issues
        else "issue"
    )
    runtime_status = "healthy" if not runtime_issues else "issue"
    next_decision = decisions_list[0] if decisions_list else None

    return _safe_status_model({
        "record_type": "workplace-human-control-status",
        "record_version": 1,
        "system": {
            "name": "Fractal",
            "version": public_version,
            "state": system_state,
            "issues": sorted(system_issues),
        },
        "workplace": {
            "status": workplace_status,
            "active_system_version": active_summary,
            "unresolved_candidate": (
                candidate_summary
                if candidate_summary and candidate_summary.get("unresolved")
                else None
            ),
            "issues": sorted(workplace_issues),
        },
        "project": {
            "current": current_project,
            "projects": project_summaries,
            "issues": sorted(project_issues),
        },
        "runtime": {
            "status": runtime_status,
            "state": live_record if isinstance(live_record, Mapping) else None,
            "issues": sorted(runtime_issues),
        },
        "decisions": {
            "next": next_decision,
            "items": decisions_list,
        },
        "evidence": {
            "paths": sorted(evidence_paths),
            "digests": {key: evidence_digests[key] for key in sorted(evidence_digests)},
            "components": component_summaries,
        },
        "issues": sorted(issues),
    })


def render_workplace_status(
    status: Mapping[str, Any],
    *,
    details: bool = False,
    format: str = "text",
    mode: str | None = None,
) -> str:
    """Render concise, detailed, or JSON status from a model."""
    status = _safe_status_model(status) if isinstance(status, Mapping) else _safe_status_model({})
    selected_format = (mode or format).lower()
    if selected_format in {"json", "application/json"}:
        return render_workplace_status_json(status)
    if selected_format in {"detailed", "details"}:
        details = True
    elif selected_format not in {"text", "default", "concise"}:
        raise ValueError(f"Unsupported workplace status format: {selected_format}")
    system = status.get("system", {}) if isinstance(status, Mapping) else {}
    workplace = status.get("workplace", {}) if isinstance(status, Mapping) else {}
    project = status.get("project", {}) if isinstance(status, Mapping) else {}
    runtime = status.get("runtime", {}) if isinstance(status, Mapping) else {}
    decisions = status.get("decisions", {}) if isinstance(status, Mapping) else {}
    version = system.get("version") or "unknown"
    state = system.get("state") or "unknown"
    workplace_label = (
        "Healthy"
        if workplace.get("status") == "healthy"
        else "issue: " + ("; ".join(workplace.get("issues", [])) or "unknown")
    )
    runtime_label = (
        "Healthy"
        if runtime.get("status") == "healthy"
        else "issue: " + ("; ".join(runtime.get("issues", [])) or "unknown")
    )
    current = project.get("current")
    project_label = current.get("project_id") if isinstance(current, Mapping) else "None"
    next_decision = decisions.get("next") if isinstance(decisions, Mapping) else None
    decision_label = next_decision.get("summary") if isinstance(next_decision, Mapping) else "None"
    lines = [
        "Fractal",
        f"System {version} · {state}",
        f"Workplace {workplace_label}",
        f"Project {project_label}",
        f"Runtime {runtime_label}",
        f"Needs your decision {decision_label}",
    ]
    if not details:
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "",
            "Evidence",
            f"- Paths: {', '.join(status.get('evidence', {}).get('paths', [])) or 'None'}",
            "- Digests: "
            + json.dumps(status.get("evidence", {}).get("digests", {}), sort_keys=True),
            "",
            "Workplace details",
            "- Active System: "
            + json.dumps(workplace.get("active_system_version"), sort_keys=True),
            "- Unresolved candidate: "
            + json.dumps(workplace.get("unresolved_candidate"), sort_keys=True),
            "- Projects: " + json.dumps(project.get("projects", []), sort_keys=True),
            "- Decisions: " + json.dumps(decisions.get("items", []), sort_keys=True),
            "- Components: "
            + json.dumps(status.get("evidence", {}).get("components", []), sort_keys=True),
            "",
            "Issues",
            f"- System: {', '.join(system.get('issues', [])) or 'None'}",
            f"- Workplace: {', '.join(workplace.get('issues', [])) or 'None'}",
            f"- Project: {', '.join(project.get('issues', [])) or 'None'}",
            f"- Runtime: {', '.join(runtime.get('issues', [])) or 'None'}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_workplace_status_json(status: Mapping[str, Any], *, indent: int | None = 2) -> str:
    """Render a stable JSON status representation."""
    safe_status = (
        _safe_status_model(status) if isinstance(status, Mapping) else _safe_status_model({})
    )
    return json.dumps(safe_status, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


def render_workplace_status_details(status: Mapping[str, Any]) -> str:
    """Explicit detailed-renderer alias."""
    return render_workplace_status(status, details=True)


# Short aliases make the model convenient for callers that already use the
# project/component ``render_*`` naming convention.
build_status = build_workplace_status
build_human_control_status = build_workplace_status
render_status = render_workplace_status
render_human_control_status = render_workplace_status
render_default_status = render_workplace_status
render_workplace_status_default = render_workplace_status
render_detailed_status = render_workplace_status_details
render_workplace_status_detail = render_workplace_status_details
render_status_json = render_workplace_status_json
render_human_control_status_json = render_workplace_status_json
to_json = render_workplace_status_json


__all__ = [
    "build_status",
    "build_human_control_status",
    "build_workplace_status",
    "render_default_status",
    "render_detailed_status",
    "render_human_control_status",
    "render_human_control_status_json",
    "render_status",
    "render_status_json",
    "render_workplace_status_default",
    "render_workplace_status_detail",
    "render_workplace_status",
    "render_workplace_status_details",
    "render_workplace_status_json",
    "to_json",
]
