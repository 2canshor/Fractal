"""Deterministic placement and authority checks at the System/Workplace boundary.

The boundary is deliberately a read-only projection.  A Workplace record can
describe, test, or propose a change to Fractal, but it cannot become a System
definition merely by using a familiar name (for example ``fatigue``) or by
claiming that a person approved it.  A reusable definition becomes a System
*candidate* only when a primary-user approval and an authorised ``/version``
promotion receipt are both present.

This module does not migrate, delete, rewrite, activate, publish, or otherwise
persist anything.  Callers receive structured findings and may decide how to
present or record them in the Workplace.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any


class OwnershipCategory(StrEnum):
    """The only placement domains recognised by the boundary.

    ``FRACTAL_SYSTEM`` includes a candidate only when ``lifecycle`` on the
    result is ``candidate``.  Candidate, active, and public versions therefore
    remain distinct lifecycle states even though they share the System domain.
    """

    FRACTAL_SYSTEM = "fractal-system"
    SYSTEM = "fractal-system"
    WORKPLACE_DURABLE = "workplace-durable"
    DURABLE = "workplace-durable"
    WORKPLACE_EPHEMERAL = "workplace-ephemeral"
    EPHEMERAL = "workplace-ephemeral"
    HISTORICAL_WORKPLACE_EVIDENCE = "historical-workplace-evidence"
    HISTORICAL = "historical-workplace-evidence"
    NEEDS_REVIEW = "needs-review"
    WORKPLACE_NEEDS_REVIEW = "needs-review"

    def __str__(self) -> str:
        """Preserve the legacy ``str, Enum`` representation."""

        return Enum.__str__(self)


# A short alias is useful to callers that refer to the contract as an
# Ownership enum rather than an OwnershipCategory enum.
Ownership = OwnershipCategory
PlacementCategory = OwnershipCategory


class VersionState(StrEnum):
    """Version lifecycle is separate from ownership."""

    UNVERSIONED = "unversioned"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    PUBLIC = "public"
    HISTORICAL = "historical"

    def __str__(self) -> str:
        """Preserve the legacy ``str, Enum`` representation."""

        return Enum.__str__(self)


@dataclass(frozen=True, slots=True)
class BoundaryFinding:
    """One actionable, deterministic boundary finding.

    ``category`` describes the check that produced the finding (placement,
    authority, privacy, and so on), while ``ownership`` describes the safe
    placement selected by the classifier.  ``to_dict`` makes findings suitable
    for a Project record without requiring callers to know the dataclass.
    """

    path: str
    category: str
    reason: str
    code: str
    ownership: OwnershipCategory | None = None
    severity: str = "error"
    action: str = "stop"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "reason": self.reason,
            "code": self.code,
            "ownership": self.ownership.value if self.ownership else None,
            "severity": self.severity,
            "action": self.action,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


Finding = BoundaryFinding


@dataclass(frozen=True, slots=True)
class PlacementResult:
    """Read-only result of classifying one record or file."""

    path: str
    ownership: OwnershipCategory
    reason: str
    record_type: str | None = None
    lifecycle: str | None = None
    mixed: bool = False
    findings: tuple[BoundaryFinding, ...] = field(default_factory=tuple)
    provenance: OwnershipCategory | None = None
    safe_for_active_system: bool = False

    @property
    def category(self) -> OwnershipCategory:
        """Compatibility spelling for callers that call placement ownership category."""

        return self.ownership

    @property
    def allowed(self) -> bool:
        """Whether no blocking finding prevents the selected placement."""

        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[BoundaryFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ownership": self.ownership.value,
            "category": self.ownership.value,
            "reason": self.reason,
            "record_type": self.record_type,
            "lifecycle": self.lifecycle,
            "mixed": self.mixed,
            "findings": [finding.to_dict() for finding in self.findings],
            "provenance": self.provenance.value if self.provenance else None,
            "safe_for_active_system": self.safe_for_active_system,
            "allowed": self.allowed,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """Validation result for a proposed reusable System definition."""

    allowed: bool
    classification: PlacementResult
    provenance: PlacementResult
    findings: tuple[BoundaryFinding, ...] = field(default_factory=tuple)

    @property
    def category(self) -> OwnershipCategory:
        return self.classification.ownership

    @property
    def ownership(self) -> OwnershipCategory:
        return self.classification.ownership

    @property
    def lifecycle(self) -> str | None:
        return self.classification.lifecycle

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "category": self.category.value,
            "ownership": self.ownership.value,
            "lifecycle": self.lifecycle,
            "classification": self.classification.to_dict(),
            "provenance": self.provenance.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


# Explicit record metadata takes precedence over guesses from body language.
# The sets intentionally include both the canonical names in this repository
# and the durable Workplace records that are routinely observed beside them.
SYSTEM_RECORD_TYPES = frozenset(
    {
        "fractal-blueprint",
        "system-definition",
        "fractal-system-definition",
        "system-policy",
        "system-method-definition",
        "system-version",
        "system-version-manifest",
        "system-version-pointer",
        "method-registry",
        "adapter-registry",
        "capability-registry",
        "blueprint-implementation-map",
        "node-implementation-map",
        "agentic-element-map",
    }
)

WORKPLACE_DURABLE_RECORD_TYPES = frozenset(
    {
        "project",
        "project-record",
        "profile",
        "user-profile",
        "authority-binding",
        "authority-bindings",
        "memory",
        "decision",
        "human-decision",
        "system-review",
        "system-review-record",
        "change-proposal",
        "proposal",
        "research",
        "research-record",
        "hypothesis",
        "experiment",
        "experiment-record",
        "issue",
        "issue-record",
        "intervention",
        "outcome",
        "component-selection",
        "component-status",
        "component-record",
        "system-version-history",
        "system-version-evidence",
        "version-history",
        "version-evidence",
        "evidence",
        "project-evidence",
        "decision-batch",
        "request-decision",
    }
)

WORKPLACE_EPHEMERAL_RECORD_TYPES = frozenset(
    {
        "runtime-state",
        "live-runtime-state",
        "machine-state",
        "local-runtime-state",
        "cache",
        "index",
        "context-index",
        "session-state",
        "socket-state",
        "temporary-state",
    }
)

HISTORICAL_RECORD_TYPES = frozenset(
    {
        "historical-evidence",
        "historical-record",
        "historical-system-evidence",
        "legacy-record",
        "archive-record",
        "system-review-history",
        "system-version-history",
        "version-history",
        "event-chain",
        "audit-history",
    }
)

DEFINITION_RECORD_TYPES = frozenset(
    {
        "definition",
        "system-definition",
        "fractal-system-definition",
        "system-policy",
        "value-definition",
        "method-definition",
        "rule-definition",
    }
)

# This is the small, identity-sensitive subset needed to reject a fake value
# definition without rewriting historical evidence.  The full Blueprint
# remains canonical in src/fractal/data/blueprint.json.
CANONICAL_DEFINITIONS = {
    "continuous-improvement": (
        "Use completed work and real outcomes to improve how future work is handled."
    ),
    "system-review": (
        "The sole protagonist that turns completed Project evidence into governed improvement "
        "of Fractal."
    ),
    "fatigue": (
        "Treat repeated effort and diminishing return as pressure to investigate a better way."
    ),
    "curiosity": (
        "Search beyond the obvious current approach for relevant adjacent and structurally "
        "similar ideas."
    ),
    "greed": (
        "After genuine success, keep testing whether a materially stronger global outcome is "
        "achievable."
    ),
    "deterministic-over-probabilistic": (
        "Move repeatable and unambiguous work toward deterministic, testable execution."
    ),
    "quantity-over-quality": (
        "Protect high-recall discovery before later reasoning narrows the evidence."
    ),
    "subtraction-first": (
        "Reduce context and system growth by preferring removal, reuse and simplification "
        "before addition."
    ),
    "global-outcome-over-local-optimisation": (
        "Judge improvement by its global effect instead of a convenient local win."
    ),
}

KNOWN_SYSTEM_ELEMENT_IDS = frozenset(CANONICAL_DEFINITIONS)
_CANONICAL_SOURCE_RECORD_TYPES = SYSTEM_RECORD_TYPES.difference(
    {
        "system-definition",
        "fractal-system-definition",
        "system-policy",
        "system-method-definition",
        "system-version",
        "system-version-manifest",
        "system-version-pointer",
    }
)

# Path rules are intentionally small and conservative.  A path alone never
# grants System authority; it only provides a deterministic placement signal.
_WORKPLACE_DURABLE_PARTS = frozenset(
    {
        "workplace",
        "projects",
        "project",
        "profile",
        "profiles",
        "memory",
        "memories",
        "decisions",
        "decision",
        "system-review",
        "system-reviews",
        "proposals",
        "proposal",
        "research",
        "experiments",
        "experiment",
        "issues",
        "issue",
        "authority",
        "components",
        "component",
        "evidence",
        "history",
        "historical",
        "archive",
        "archives",
        "records",
        "versions",
    }
)
_WORKPLACE_EPHEMERAL_PARTS = frozenset(
    {
        ".codex",
        ".fractal",
        ".cache",
        ".local",
        "runtime",
        "runtime-state",
        "live-state",
        "logs",
        "log",
        "tmp",
        "temp",
        "cache",
        "build",
        "dist",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
    }
)
_HISTORICAL_PARTS = frozenset({"historical", "history", "archive", "archives", "events"})

_PERSONAL_KEY_PARTS = frozenset(
    {
        "personal",
        "personal_data",
        "personaldata",
        "profile",
        "email",
        "phone",
        "telephone",
        "mobile",
        "address",
        "home_address",
        "postal_address",
        "date_of_birth",
        "dob",
        "biometric",
        "health",
        "medical",
        "private_note",
        "private_notes",
        "user_id",
        "account_id",
        "real_name",
        "full_name",
        "personal_name",
        "display_name",
        "user_name",
        "username",
        "emergency_contact",
    }
)
_RAW_LIVE_KEY_PARTS = frozenset(
    {
        "raw_live",
        "raw_live_state",
        "live_state",
        "runtime_state",
        "machine_state",
        "socket",
        "socket_path",
        "pid",
        "process_id",
        "database_path",
        "db_path",
        "session_state",
        "credential",
        "credentials",
        "secret",
        "token",
        "api_key",
        "private_key",
        "local_ip",
        "private_ip",
    }
)
_ACTIVE_ENDPOINT_KEY_PARTS = frozenset(
    {
        "adapter_url",
        "adapter_endpoint",
        "base_url",
        "endpoint",
        "endpoint_url",
        "url",
        "uri",
    }
)
_IMMUTABLE_REFERENCE_ROOTS = frozenset(
    {
        "evidence",
        "event_chain",
        "event_history",
        "history",
        "provenance",
        "review_history",
    }
)
_IMMUTABLE_REFERENCE_FIELDS = frozenset(
    {
        "attachment",
        "attachments",
        "attachment_path",
        "attachment_paths",
        "source",
        "source_path",
        "source_paths",
        "sources",
        "url",
        "urls",
        "uri",
        "uris",
    }
)
_IMMUTABLE_REFERENCE_RECORD_TYPES = frozenset(
    {
        "audit-history",
        "event-chain",
        "evidence",
        "historical-evidence",
        "historical-record",
        "historical-system-evidence",
        "project",
        "project-evidence",
        "project-record",
        "system-review",
        "system-review-history",
        "system-review-record",
        "system-version-history",
        "version-history",
    }
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s'\"])(?:/(?:Users|home|private|tmp|var|etc|opt|Volumes|Applications|System)/|"
    r"[A-Za-z]:[\\/])"
)
_IP_RE = re.compile(
    r"(?<![\d.])(?:127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|"
    r"192\.168\.(?:\d{1,3}\.)?\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})"
    r"(?![\d.])"
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _normalise_path(path: str | Path | None) -> str:
    if path is None:
        return "<record>"
    text = str(path).replace("\\", "/")
    text = re.sub(r"/{2,}", "/", text)
    return text or "<record>"


def _path_parts(path: str | Path | None) -> tuple[str, ...]:
    normalised = _normalise_path(path)
    return tuple(part.casefold() for part in normalised.split("/") if part and part != ".")


def _has_sequence(parts: tuple[str, ...], *sequence: str) -> bool:
    needle = tuple(item.casefold() for item in sequence)
    return any(parts[index : index + len(needle)] == needle for index in range(len(parts)))


def _path_signal(path: str | Path | None) -> str:
    """Return ``system``, ``durable``, ``ephemeral``, ``historical``, or ``unknown``."""

    parts = _path_parts(path)
    if not parts or parts == ("<record>",):
        return "unknown"
    if any(part in _WORKPLACE_EPHEMERAL_PARTS for part in parts):
        return "ephemeral"
    # ``src/fractal`` is the exact source route; an isolated ``fractal`` folder
    # is not enough because a Workplace may quite legitimately use that word.
    if _has_sequence(parts, "src", "fractal") or _has_sequence(parts, "fractal", "src", "fractal"):
        return "system"
    if any(part in _WORKPLACE_DURABLE_PARTS for part in parts):
        if any(part in _HISTORICAL_PARTS for part in parts):
            return "historical"
        return "durable"
    if any(part in _HISTORICAL_PARTS for part in parts):
        return "historical"
    return "unknown"


def _record_type(record: Mapping[str, Any]) -> str | None:
    value = record.get("record_type")
    if value is None and isinstance(record.get("type"), str):
        candidate = str(record["type"]).casefold()
        known = (
            SYSTEM_RECORD_TYPES
            | WORKPLACE_DURABLE_RECORD_TYPES
            | WORKPLACE_EPHEMERAL_RECORD_TYPES
            | HISTORICAL_RECORD_TYPES
        )
        if candidate in known:
            value = candidate
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _normalise_marker(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "-").replace(" ", "-")


def _ownership_marker(record: Mapping[str, Any]) -> str | None:
    ownership = record.get("ownership")
    if isinstance(ownership, Mapping):
        for key in ("category", "domain", "owner", "placement"):
            if ownership.get(key) is not None:
                return _normalise_marker(ownership[key])
    for key in ("ownership", "ownership_category", "domain", "owner", "placement"):
        if record.get(key) is not None:
            return _normalise_marker(record[key])
    return None


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() in {"true", "yes"})


def _is_historical(record: Mapping[str, Any], signal: str) -> bool:
    record_type = _record_type(record)
    return bool(
        record_type in HISTORICAL_RECORD_TYPES
        or _is_true(record.get("historical"))
        or _is_true(record.get("historical_evidence"))
        or _is_true(record.get("legacy"))
        or signal == "historical"
    )


def _has_active_authority_claim(record: Mapping[str, Any]) -> bool:
    lifecycle = record.get("lifecycle")
    lifecycle_status = lifecycle.get("status") if isinstance(lifecycle, Mapping) else None
    return bool(
        _is_true(record.get("active"))
        or _is_true(record.get("instruction_effect"))
        or _normalise_marker(record.get("authority")) in {"system", "fractal-system"}
        or _normalise_marker(record.get("owner")) in {"fractal", "fractal-system"}
        or _normalise_marker(lifecycle_status) in {"active", "canonical", "public"}
    )


def _version_state(record: Mapping[str, Any]) -> str:
    lifecycle = record.get("lifecycle")
    status = record.get("status")
    if isinstance(lifecycle, Mapping):
        status = lifecycle.get("status", status)
    marker = _normalise_marker(status)
    if marker in {"candidate", "staged", "proposed", "approved-for-version"}:
        return VersionState.CANDIDATE.value
    if marker in {"active", "activated", "live"}:
        return VersionState.ACTIVE.value
    if marker in {"public", "published"}:
        return VersionState.PUBLIC.value
    return VersionState.UNVERSIONED.value


def _iter_values(value: Any, *, key_path: str = "") -> Iterable[tuple[str, Any]]:
    """Yield scalar and mapping values with deterministic JSON-style paths."""

    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            child_path = f"{key_path}/{key}" if key_path else f"/{key}"
            yield from _iter_values(value[key], key_path=child_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_values(item, key_path=f"{key_path}/{index}")
        return
    yield key_path or "/", value


def _key_name(path: str) -> str:
    return path.rsplit("/", 1)[-1].casefold().replace("-", "_")


def _contains_personal_data(record: Mapping[str, Any]) -> tuple[str, str] | None:
    for path, value in _iter_values(record):
        key = _key_name(path)
        if key in _PERSONAL_KEY_PARTS:
            return path, f"field '{key}' is personal data"
        if isinstance(value, str) and _EMAIL_RE.search(value):
            return path, "value contains an email address"
    return None


def _is_immutable_reference_path(record: Mapping[str, Any], value_path: str) -> bool:
    """Return whether a scalar is a retained Project evidence reference.

    The exemption is intentionally structural.  It recognises only direct
    reference fields below known immutable evidence/provenance/history roots;
    a field merely named ``source`` elsewhere remains subject to the normal
    machine-state checks.  A missing record type is tolerated for small
    evidence fixtures, but an explicit incompatible type is not.
    """

    record_type = _record_type(record)
    if record_type is not None and record_type not in _IMMUTABLE_REFERENCE_RECORD_TYPES:
        return False
    parts = tuple(
        part.casefold().replace("-", "_")
        for part in value_path.split("/")
        if part
    )
    if not parts or parts[0] not in _IMMUTABLE_REFERENCE_ROOTS:
        return False
    relative = parts[1:]
    while relative and relative[0].isdigit():
        relative = relative[1:]
    return bool(relative and relative[0] in _IMMUTABLE_REFERENCE_FIELDS)


def _contains_machine_state(record: Mapping[str, Any], path: str) -> tuple[str, str] | None:
    normalised = _normalise_path(path)
    if normalised.startswith(("/tmp/", "/private/tmp/", "/var/run/")):
        return normalised, "path points at machine-local runtime state"
    for value_path, value in _iter_values(record):
        key = _key_name(value_path)
        if key in _RAW_LIVE_KEY_PARTS:
            return value_path, f"field '{key}' contains raw machine or live-runtime state"
        if isinstance(value, str):
            if _is_immutable_reference_path(record, value_path):
                continue
            if _ABSOLUTE_PATH_RE.search(value):
                return value_path, "value contains a machine absolute path"
            if _IP_RE.search(value) or "localhost" in value.casefold():
                return value_path, "value contains a private or loopback address"
            if key in _ACTIVE_ENDPOINT_KEY_PARTS and re.search(
                r"\b[a-z][a-z0-9+.-]*://", value, re.I
            ):
                return value_path, f"field '{key}' contains a live endpoint URL"
    return None


def _definition_candidate(record: Mapping[str, Any]) -> tuple[str | None, Any, bool]:
    element_id = record.get("element_id") or record.get("system_element_id") or record.get("name")
    if not isinstance(element_id, str):
        element_id = None
    element_key = element_id.casefold() if element_id else None
    definition_value: Any = None
    for key in ("core_concept", "definition", "intent", "rule", "description"):
        if key in record:
            definition_value = record[key]
            break
    record_type = _record_type(record)
    definition_like = bool(
        record_type in DEFINITION_RECORD_TYPES
        or (element_key in KNOWN_SYSTEM_ELEMENT_IDS and definition_value is not None)
        or "system_definition" in record
        or "reusable_definition" in record
    )
    if definition_value is None and isinstance(record.get("system_definition"), Mapping):
        definition_value = record["system_definition"].get("core_concept") or record[
            "system_definition"
        ].get("definition")
    return element_key, definition_value, definition_like


def _is_authorised_system_metadata(record: Mapping[str, Any], signal: str) -> bool:
    record_type = _record_type(record)
    marker = _ownership_marker(record)
    lifecycle = record.get("lifecycle")
    lifecycle_status = lifecycle.get("status") if isinstance(lifecycle, Mapping) else None
    explicit_canonical = (
        record_type == "fractal-blueprint"
        and _normalise_marker(lifecycle_status) == "canonical"
        and _normalise_marker(record.get("active_state_source")) in {"", "system-version-pointer"}
    )
    explicit_source = signal == "system" and (
        # The canonical source route plus an explicit known System record type
        # is sufficient.  A copied record_type in Workplace is handled by the
        # path branch in ``_base_category`` and never receives this authority.
        record_type in _CANONICAL_SOURCE_RECORD_TYPES
        or marker in {"fractal-system", "system", "fractal"}
        or _normalise_marker(record.get("authority")) in {"fractal-system", "system"}
        or _normalise_marker(record.get("owner")) == "fractal"
    )
    generic_definition_types = {
        "system-definition",
        "fractal-system-definition",
        "system-policy",
        "system-method-definition",
        "system-version",
        "system-version-manifest",
        "system-version-pointer",
    }
    version_bound = bool(
        record.get("version")
        or record.get("system_version")
        or record.get("promotion_evidence")
        or record.get("version_promotion")
        or _normalise_marker(lifecycle_status) in {"candidate", "active", "public", "canonical"}
    )
    if (
        record_type in generic_definition_types
        and not explicit_canonical
        and not version_bound
    ):
        return False
    version_evidence_source = False
    if record_type in {
        "system-version",
        "system-version-manifest",
        "system-version-pointer",
    }:
        evidence = record.get("promotion_evidence") or record.get("version_promotion")
        route = (
            _normalise_marker(evidence.get("route"))
            if isinstance(evidence, Mapping)
            else ""
        )
        if (
            signal == "system"
            and (
                not isinstance(evidence, Mapping)
                or route not in {"/version", "version", "governed-version"}
            )
        ):
            return False
        if isinstance(evidence, Mapping):
            actor = _normalise_marker(
                evidence.get("actor")
                or evidence.get("issued_by")
                or evidence.get("authorized_by")
            )
            receipt = evidence.get("receipt_id") or evidence.get("authority_receipt_id")
            authorised = _is_true(evidence.get("authorized")) or (
                actor == "primary-user" and bool(receipt)
            )
            if not authorised or not record.get("version"):
                return False
            version_evidence_source = True
    # Registries in the Fractal source tree are canonical only when the path
    # confirms that they are the source tree.  A copied registry in Workplace
    # is a proposal or record, never active authority.
    return bool(explicit_canonical or explicit_source or version_evidence_source)


def _finding(
    path: str,
    category: str,
    reason: str,
    code: str,
    *,
    ownership: OwnershipCategory | None = None,
    severity: str = "error",
    action: str = "stop",
) -> BoundaryFinding:
    return BoundaryFinding(
        path=path,
        category=category,
        reason=reason,
        code=code,
        ownership=ownership,
        severity=severity,
        action=action,
    )


def _nested_units(record: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Return competing records in an explicit collection, excluding provenance."""

    units: list[tuple[str, Mapping[str, Any]]] = []
    for key in ("records", "items", "entries", "artifacts", "contents"):
        value = record.get(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, Mapping) and (
                    _record_type(item) is not None or "ownership" in item
                ):
                    units.append((f"/{key}/{index}", item))
    return units


def _record_provenance(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("provenance", "source_record", "source", "origin"):
        value = record.get(key)
        if isinstance(value, Mapping) and (
            _record_type(value) is not None or "path" in value or "record" in value
        ):
            nested = value.get("record")
            if isinstance(nested, Mapping):
                return nested
            return value
    return None


def _base_category(
    record: Mapping[str, Any], signal: str
) -> tuple[OwnershipCategory, str, str | None]:
    record_type = _record_type(record)
    version_state = _version_state(record)
    if _is_historical(record, signal):
        return (
            OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE,
            "Historical Workplace evidence is retained verbatim and cannot control the System.",
            VersionState.HISTORICAL.value,
        )
    if record_type in WORKPLACE_EPHEMERAL_RECORD_TYPES or signal == "ephemeral":
        return (
            OwnershipCategory.WORKPLACE_EPHEMERAL,
            "Machine-local or rebuildable state belongs to ignored local Workplace "
            "implementation state.",
            VersionState.UNVERSIONED.value,
        )
    if record_type in WORKPLACE_DURABLE_RECORD_TYPES:
        return (
            OwnershipCategory.WORKPLACE_DURABLE,
            "Projects, decisions, evidence, status, and authority records remain durable "
            "Workplace work.",
            version_state,
        )
    if record_type in SYSTEM_RECORD_TYPES:
        if signal in {"durable", "historical", "ephemeral"}:
            return (
                OwnershipCategory.WORKPLACE_DURABLE
                if signal != "ephemeral"
                else OwnershipCategory.WORKPLACE_EPHEMERAL,
                "System-shaped material outside the canonical source route remains Workplace "
                "until promoted.",
                version_state,
            )
        if _is_authorised_system_metadata(record, signal):
            return (
                OwnershipCategory.FRACTAL_SYSTEM,
                "Explicit canonical Fractal metadata and the canonical source route identify "
                "reusable System behaviour.",
                (
                    version_state
                    if version_state != VersionState.UNVERSIONED.value
                    else VersionState.ACTIVE.value
                ),
            )
        return (
            OwnershipCategory.NEEDS_REVIEW,
            "A System-shaped record lacks enough canonical metadata to control Fractal safely.",
            version_state,
        )
    marker = _ownership_marker(record)
    if marker in {"workplace", "workplace-durable", "durable", "personal", "user"}:
        return (
            OwnershipCategory.WORKPLACE_DURABLE,
            "Explicit Workplace ownership keeps the record out of active System authority.",
            version_state,
        )
    if marker in {"workplace-ephemeral", "ephemeral", "local", "machine-local"}:
        return (
            OwnershipCategory.WORKPLACE_EPHEMERAL,
            "Explicit local ownership marks rebuildable Workplace implementation state.",
            version_state,
        )
    if marker in {"historical", "historical-workplace-evidence", "archive"}:
        return (
            OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE,
            "Explicit historical ownership preserves evidence without granting instruction "
            "authority.",
            VersionState.HISTORICAL.value,
        )
    if signal == "durable":
        return (
            OwnershipCategory.WORKPLACE_DURABLE,
            "A conservative Workplace path keeps unversioned material durable but "
            "non-authoritative.",
            version_state,
        )
    if signal == "historical":
        return (
            OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE,
            "Historical path evidence is retained as Workplace history, not rewritten as "
            "current System state.",
            VersionState.HISTORICAL.value,
        )
    if signal == "ephemeral":
        return (
            OwnershipCategory.WORKPLACE_EPHEMERAL,
            "Local rebuildable state is ignored Workplace implementation state.",
            version_state,
        )
    return (
        OwnershipCategory.NEEDS_REVIEW,
        "No explicit record metadata or conservative path rule identifies a safe owner.",
        version_state,
    )


def classify_record(
    record: Mapping[str, Any] | str | Path,
    path: str | Path | None = None,
    *,
    _nested: bool = False,
) -> PlacementResult:
    """Classify a record without writing or normalising its contents.

    Explicit record metadata, known record types, and conservative path rules
    are considered in a fixed order.  Unknown records and contradictory
    collections become ``needs-review``.  The optional private ``_nested``
    flag is used only while inspecting a mixed-file collection.
    """

    if isinstance(record, (str, Path)) and isinstance(path, Mapping):
        record, path = path, record
    location = _normalise_path(path)
    if not isinstance(record, Mapping):
        finding = _finding(
            location,
            "placement",
            "A boundary record must be a mapping with explicit metadata.",
            "invalid-record",
            ownership=OwnershipCategory.NEEDS_REVIEW,
        )
        return PlacementResult(
            location,
            OwnershipCategory.NEEDS_REVIEW,
            "Invalid record shape is unsafe to place.",
            findings=(finding,),
        )

    signal = _path_signal(location)
    record_type = _record_type(record)
    ownership, reason, lifecycle = _base_category(record, signal)
    findings: list[BoundaryFinding] = []

    if record_type is None and not _nested and signal == "unknown":
        findings.append(
            _finding(
                location,
                "placement",
                "Unknown content without record_type and without a governed path defaults "
                "to needs-review.",
                "missing-record-metadata",
                ownership=OwnershipCategory.NEEDS_REVIEW,
            )
        )

    # A workplace proposal/research/experiment/decision can mention System
    # names, but none of those records can override the active System.
    if record_type in {
        "change-proposal",
        "proposal",
        "research",
        "research-record",
        "experiment",
        "experiment-record",
        "issue",
        "issue-record",
        "decision",
        "human-decision",
        "system-review",
        "system-review-record",
    }:
        if ownership not in {
            OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE,
            OwnershipCategory.WORKPLACE_EPHEMERAL,
        }:
            ownership = OwnershipCategory.WORKPLACE_DURABLE
            reason = (
                "Workplace work, evidence, proposals, and human decisions cannot override "
                "active System behaviour."
            )
        if _has_active_authority_claim(record):
            findings.append(
                _finding(
                    location,
                    "authority",
                    "A Workplace proposal/research/experiment/decision claims System authority; "
                    "the claim is ignored until /version promotion.",
                    "workplace-cannot-override-system",
                    ownership=OwnershipCategory.WORKPLACE_DURABLE,
                )
            )

    element_id, definition_value, definition_like = _definition_candidate(record)
    if not _is_historical(record, signal) and definition_like:
        if element_id in KNOWN_SYSTEM_ELEMENT_IDS and definition_value is not None:
            expected = CANONICAL_DEFINITIONS[element_id]
            if str(definition_value).strip() != expected:
                ownership = OwnershipCategory.NEEDS_REVIEW
                reason = (
                    "A reusable System element has a non-canonical definition and cannot "
                    "enter active authority."
                )
                findings.append(
                    _finding(
                        location,
                        "definition",
                        f"Definition for '{element_id}' does not match the canonical Blueprint; "
                        "preserve it as a Workplace proposal or evidence.",
                        "fake-system-definition",
                        ownership=OwnershipCategory.NEEDS_REVIEW,
                    )
                )
        workplace_like = signal in {"durable", "ephemeral", "historical"} or ownership in {
            OwnershipCategory.WORKPLACE_DURABLE,
            OwnershipCategory.WORKPLACE_EPHEMERAL,
            OwnershipCategory.NEEDS_REVIEW,
        }
        authoritative_definition = _has_active_authority_claim(record) or bool(
            record.get("reusable_definition")
        )
        if (
            workplace_like
            and authoritative_definition
            and ownership != OwnershipCategory.FRACTAL_SYSTEM
        ):
            ownership = OwnershipCategory.NEEDS_REVIEW
            reason = (
                "Reusable definitions duplicated inside Workplace cannot become hidden "
                "System authority."
            )
            findings.append(
                _finding(
                    location,
                    "authority",
                    "Workplace contains a reusable System definition with an authority/active "
                    "claim; keep the definition as a proposal and use /version for promotion.",
                    "duplicate-reusable-definition",
                    ownership=OwnershipCategory.NEEDS_REVIEW,
                )
            )

    # Explicit System metadata in a Workplace path is a conflict, not an
    # instruction.  Unversioned System work remains Workplace as required.
    if (
        signal in {"durable", "ephemeral"}
        and record_type in SYSTEM_RECORD_TYPES
        and ownership != OwnershipCategory.NEEDS_REVIEW
    ):
        findings.append(
            _finding(
                location,
                "placement",
                "System-shaped material is outside the canonical source route; unversioned "
                "System work remains Workplace.",
                "unversioned-system-work-in-workplace",
                ownership=ownership,
            )
        )
    if signal == "system" and record_type in WORKPLACE_DURABLE_RECORD_TYPES:
        findings.append(
            _finding(
                location,
                "placement",
                "A Workplace record inside a source-looking path does not acquire System "
                "authority from its location.",
                "workplace-record-in-system-path",
                ownership=OwnershipCategory.WORKPLACE_DURABLE,
            )
        )

    # A file containing competing records must be split by its owner.  The
    # provenance/evidence fields of one candidate are intentionally excluded so
    # a candidate can retain Workplace provenance without becoming mixed.
    nested = _nested_units(record)
    nested_results: list[PlacementResult] = []
    for relative, item in nested:
        nested_results.append(classify_record(item, f"{location}{relative}", _nested=True))
    categories = {result.ownership for result in nested_results}
    declared_categories = {
        marker
        for _, item in nested
        for marker in (_ownership_marker(item),)
        if marker in {
            "fractal-system",
            "system",
            "fractal",
            "workplace",
            "workplace-durable",
            "durable",
        }
    }
    mixed = len(categories) > 1 or (
        any(marker in {"fractal-system", "system", "fractal"} for marker in declared_categories)
        and any(
            marker in {"workplace", "workplace-durable", "durable"}
            for marker in declared_categories
        )
    )
    if mixed:
        ownership = OwnershipCategory.NEEDS_REVIEW
        reason = (
            "One file contains records with different owners; split the records before any "
            "promotion or use."
        )
        findings.append(
            _finding(
                location,
                "mixed-file",
                "Mixed System and Workplace records cannot be safely placed together; split "
                "the file and retain each record's provenance.",
                "mixed-ownership-file",
                ownership=OwnershipCategory.NEEDS_REVIEW,
            )
        )

    if (
        ownership == OwnershipCategory.FRACTAL_SYSTEM
        or (
            signal == "system"
            and record_type in SYSTEM_RECORD_TYPES
            and not _is_historical(record, signal)
        )
    ):
        personal = _contains_personal_data(record)
        if personal is not None:
            personal_path, personal_reason = personal
            ownership = OwnershipCategory.NEEDS_REVIEW
            reason = "Personal data cannot enter reusable Fractal System definitions."
            findings.append(
                _finding(
                    f"{location}{personal_path}",
                    "privacy",
                    personal_reason + "; remove or retain it in the Workplace.",
                    "personal-data-in-system",
                    ownership=OwnershipCategory.NEEDS_REVIEW,
                )
            )

    if ownership == OwnershipCategory.WORKPLACE_DURABLE and not _is_historical(record, signal):
        machine_state = _contains_machine_state(record, location)
        if machine_state is not None:
            state_path, state_reason = machine_state
            ownership = OwnershipCategory.NEEDS_REVIEW
            reason = (
                "Machine-local absolute paths, private addresses, and raw live state cannot "
                "enter durable Workplace records."
            )
            findings.append(
                _finding(
                    state_path,
                    "privacy/path",
                    state_reason
                    + "; keep it in ignored local Workplace implementation state or redact it.",
                    "machine-state-in-durable-workplace",
                    ownership=OwnershipCategory.NEEDS_REVIEW,
                )
            )

    provenance_result = None
    provenance_record = _record_provenance(record)
    if provenance_record is not None:
        provenance_result = classify_record(
            provenance_record, f"{location}/provenance", _nested=True
        )

    if findings:
        # Findings produced by an ordinary Workplace record are warnings about
        # authority unless the classifier was explicitly forced into review.
        pass

    safe_for_active_system = ownership == OwnershipCategory.FRACTAL_SYSTEM and lifecycle in {
        VersionState.ACTIVE.value,
        VersionState.PUBLIC.value,
    }
    return PlacementResult(
        path=location,
        ownership=ownership,
        reason=reason,
        record_type=record_type,
        lifecycle=lifecycle,
        mixed=mixed,
        findings=tuple(findings),
        provenance=provenance_result.ownership if provenance_result else None,
        safe_for_active_system=safe_for_active_system,
    )


def classify_file(
    path: str | Path,
    record: Mapping[str, Any] | None = None,
    *,
    content: str | bytes | None = None,
) -> PlacementResult:
    """Classify one file path and optional JSON record without writing it."""

    location = _normalise_path(path)
    if record is None:
        try:
            raw = content if content is not None else Path(path).read_bytes()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            record = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            finding = _finding(
                location,
                "placement",
                f"File is not readable as a deterministic JSON record: {type(error).__name__}.",
                "unreadable-record",
                ownership=OwnershipCategory.NEEDS_REVIEW,
            )
            return PlacementResult(
                location,
                OwnershipCategory.NEEDS_REVIEW,
                "Unreadable content is unsafe to place.",
                findings=(finding,),
            )
    return classify_record(record, location)


def classify_path(path: str | Path) -> PlacementResult:
    """Classify a path without opening it; unknown paths fail closed."""

    location = _normalise_path(path)
    signal = _path_signal(location)
    empty: dict[str, Any] = {}
    ownership, reason, lifecycle = _base_category(empty, signal)
    findings: tuple[BoundaryFinding, ...] = ()
    if signal == "unknown":
        findings = (
            _finding(
                location,
                "path",
                "Path does not identify a canonical System or Workplace route; inspect "
                "explicit record metadata.",
                "unknown-path",
                ownership=OwnershipCategory.NEEDS_REVIEW,
            ),
        )
        ownership = OwnershipCategory.NEEDS_REVIEW
    return PlacementResult(location, ownership, reason, lifecycle=lifecycle, findings=findings)


def detect_mixed_file(
    record: Mapping[str, Any] | str | Path,
    path: str | Path | None = None,
    *,
    content: str | bytes | None = None,
) -> bool:
    """Return whether an explicit record collection contains competing owners."""

    if isinstance(record, Mapping):
        return classify_record(record, path).mixed
    return classify_file(record, content=content).mixed


def _first_mapping(value: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any] | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _human_approval_valid(evidence: Mapping[str, Any] | None) -> tuple[bool, str]:
    if evidence is None:
        return False, "No human approval evidence was supplied."
    approved = evidence.get("approved")
    status = _normalise_marker(evidence.get("status") or evidence.get("decision_status"))
    approval_marker = _is_true(approved) or status in {
        "approved",
        "approved-for-version",
        "primary-user-approved",
        "accepted",
    }
    actor = _normalise_marker(
        evidence.get("actor")
        or evidence.get("approved_by")
        or evidence.get("decided_by")
        or evidence.get("issued_by")
    )
    if not approval_marker:
        return False, "Human approval evidence does not explicitly record approval."
    if actor != "primary-user":
        return False, "Only a primary-user human approval can authorise a System promotion."
    if evidence.get("human_action") is False or evidence.get("automated") is True:
        return False, "Automated or non-human approval cannot authorise a System promotion."
    return True, "Primary-user human approval evidence is present."


def _version_promotion_valid(evidence: Mapping[str, Any] | None) -> tuple[bool, str, str]:
    if evidence is None:
        return False, "No /version promotion evidence was supplied.", VersionState.UNVERSIONED.value
    route = _normalise_marker(
        evidence.get("route") or evidence.get("command") or evidence.get("source_route")
    )
    nested_event = evidence.get("event") if isinstance(evidence.get("event"), Mapping) else {}
    event_action = _normalise_marker(nested_event.get("action"))
    action = _normalise_marker(evidence.get("action") or event_action)
    authorised = _is_true(evidence.get("authorized")) or _is_true(evidence.get("authorised"))
    actor = _normalise_marker(
        evidence.get("actor")
        or evidence.get("issued_by")
        or nested_event.get("actor")
    )
    receipt = (
        evidence.get("authority_receipt_id")
        or evidence.get("receipt_id")
        or evidence.get("promotion_receipt_id")
        or nested_event.get("authority_receipt_id")
    )
    route_ok = route in {"/version", "version", "governed-version", "governed-version-command"}
    action_ok = action in {
        "build-candidate",
        "promote",
        "promote-candidate",
        "version",
        "activate",
        "publish",
    }
    # A receipt plus primary-user actor is sufficient for the deterministic
    # simulation used by tests; a bare string saying '/version' is not.
    authorised = authorised or (actor == "primary-user" and bool(receipt))
    version_value = (
        evidence.get("version")
        or evidence.get("version_id")
        or nested_event.get("version")
    )
    status = _normalise_marker(evidence.get("status") or evidence.get("version_state"))
    state = (
        VersionState.PUBLIC.value
        if status in {"public", "published"}
        else VersionState.ACTIVE.value
        if status in {"active", "activated", "live"}
        else VersionState.CANDIDATE.value
        if status in {"candidate", "staged", "approved-for-version", "proposed"}
        else VersionState.CANDIDATE.value
    )
    if not route_ok:
        return False, "Promotion evidence must identify the governed /version route.", state
    if not authorised:
        return False, "The /version promotion is not authorised by a primary-user receipt.", state
    if not action_ok and not bool(version_value):
        return False, "Promotion evidence does not identify a version promotion action.", state
    if not isinstance(version_value, str) or not version_value.strip():
        return False, "Promotion evidence must identify the exact candidate version.", state
    return True, "Authorised /version promotion evidence is present.", state


def validate_system_promotion(
    proposed: Mapping[str, Any],
    human_approval: Mapping[str, Any] | None = None,
    version_promotion: Mapping[str, Any] | None = None,
    *,
    path: str | Path | None = None,
    provenance: Mapping[str, Any] | None = None,
    provenance_path: str | Path | None = None,
    **evidence_aliases: Any,
) -> PromotionResult:
    """Validate a proposed reusable System result without promoting it.

    Both independent gates are mandatory.  A human approval without
    ``/version`` leaves the proposal in Workplace.  A valid pair classifies
    only the reusable result as a System ``candidate``; its provenance is
    separately classified and remains Workplace evidence.
    """

    if not isinstance(proposed, Mapping):
        proposed = {}
    proposed_path = _normalise_path(path)
    human_approval = human_approval or evidence_aliases.get("approval_evidence")
    version_promotion = version_promotion or evidence_aliases.get("version_evidence")
    embedded_human = human_approval or _first_mapping(
        proposed, ("human_approval", "approval", "decision", "authority_evidence")
    )
    embedded_version = version_promotion or _first_mapping(
        proposed, ("version_promotion", "promotion_evidence", "version_evidence", "version_receipt")
    )
    if embedded_version is None and _record_type(proposed) in {
        "system-version",
        "system-version-manifest",
        "system-version-pointer",
    }:
        embedded_version = proposed
    human_ok, human_reason = _human_approval_valid(embedded_human)
    version_ok, version_reason, version_state = _version_promotion_valid(embedded_version)
    findings: list[BoundaryFinding] = []
    if not human_ok:
        findings.append(
            _finding(
                proposed_path,
                "authority",
                human_reason,
                "human-approval-required",
                ownership=OwnershipCategory.WORKPLACE_DURABLE,
            )
        )
    if not version_ok:
        findings.append(
            _finding(
                proposed_path,
                "authority",
                version_reason,
                "version-promotion-required",
                ownership=OwnershipCategory.WORKPLACE_DURABLE,
            )
        )

    proposed_placement = classify_record(proposed, proposed_path)
    if proposed_placement.mixed:
        findings.extend(proposed_placement.findings)
    if proposed_placement.ownership == OwnershipCategory.NEEDS_REVIEW:
        findings.extend(proposed_placement.findings)
    personal = _contains_personal_data(proposed)
    if personal is not None:
        personal_path, personal_reason = personal
        findings.append(
            _finding(
                f"{proposed_path}{personal_path}",
                "privacy",
                personal_reason
                + "; personal data cannot be promoted into Fractal System definitions.",
                "personal-data-in-system",
                ownership=OwnershipCategory.WORKPLACE_DURABLE,
            )
        )

    candidate_allowed = human_ok and version_ok and not any(
        finding.code in {
            "mixed-ownership-file",
            "fake-system-definition",
            "duplicate-reusable-definition",
            "personal-data-in-system",
            "unreadable-record",
            "invalid-record",
        }
        for finding in findings
    )
    if candidate_allowed:
        candidate_classification = PlacementResult(
            path=proposed_path,
            ownership=OwnershipCategory.FRACTAL_SYSTEM,
            reason=(
                "Reusable result is a System candidate only after both human approval and "
                "authorised /version promotion evidence."
            ),
            record_type=_record_type(proposed) or "system-definition",
            lifecycle=VersionState.CANDIDATE.value,
            mixed=False,
            findings=tuple(findings),
            provenance=OwnershipCategory.WORKPLACE_DURABLE,
            safe_for_active_system=False,
        )
    else:
        candidate_classification = PlacementResult(
            path=proposed_path,
            ownership=OwnershipCategory.WORKPLACE_DURABLE,
            reason=(
                "The proposed reusable result remains Workplace and cannot control active "
                "System behaviour."
            ),
            record_type=_record_type(proposed),
            lifecycle=version_state if version_ok else VersionState.UNVERSIONED.value,
            mixed=proposed_placement.mixed,
            findings=tuple(findings),
            provenance=OwnershipCategory.WORKPLACE_DURABLE,
            safe_for_active_system=False,
        )

    provenance_record = provenance or _record_provenance(proposed)
    if provenance_record is None:
        provenance_record = {
            "record_type": "system-review-evidence",
            "ownership": "workplace-durable",
            "evidence": "provenance-not-supplied",
        }
    provenance_result = classify_record(
        provenance_record, provenance_path or f"{proposed_path}/provenance"
    )
    if provenance_result.ownership == OwnershipCategory.FRACTAL_SYSTEM:
        # Provenance is never allowed to inherit candidate authority from its
        # parent; it remains durable Workplace evidence by contract.
        provenance_result = PlacementResult(
            path=provenance_result.path,
            ownership=OwnershipCategory.WORKPLACE_DURABLE,
            reason=(
                "Promotion provenance remains Workplace evidence even when the reusable "
                "result is a System candidate."
            ),
            record_type=provenance_result.record_type,
            lifecycle=VersionState.UNVERSIONED.value,
            mixed=provenance_result.mixed,
            findings=provenance_result.findings,
            provenance=provenance_result.provenance,
            safe_for_active_system=False,
        )

    return PromotionResult(
        allowed=candidate_allowed,
        classification=candidate_classification,
        provenance=provenance_result,
        findings=tuple(findings),
    )


def validate_promotion(*args: Any, **kwargs: Any) -> PromotionResult:
    """Alias for callers that use the shorter boundary vocabulary."""

    return validate_system_promotion(*args, **kwargs)


def validate_record_placement(
    record: Mapping[str, Any], path: str | Path | None = None
) -> tuple[BoundaryFinding, ...]:
    """Return only actionable findings for a record placement check."""

    return classify_record(record, path).findings


def check_placement(record: Mapping[str, Any], path: str | Path | None = None) -> PlacementResult:
    """Alias for ``classify_record`` used by boundary callers."""

    return classify_record(record, path)


def classify(
    value: Mapping[str, Any] | str | Path,
    path: str | Path | None = None,
    *,
    content: str | bytes | None = None,
) -> PlacementResult:
    """Classify either a record or a file using one stable entry point."""

    if isinstance(value, Mapping):
        return classify_record(value, path)
    return classify_file(value, content=content)


__all__ = [
    "BoundaryFinding",
    "CANONICAL_DEFINITIONS",
    "Finding",
    "HISTORICAL_RECORD_TYPES",
    "Ownership",
    "OwnershipCategory",
    "PlacementCategory",
    "PlacementResult",
    "PromotionResult",
    "SYSTEM_RECORD_TYPES",
    "VersionState",
    "WORKPLACE_DURABLE_RECORD_TYPES",
    "WORKPLACE_EPHEMERAL_RECORD_TYPES",
    "check_placement",
    "classify",
    "classify_file",
    "classify_path",
    "classify_record",
    "detect_mixed_file",
    "validate_promotion",
    "validate_record_placement",
    "validate_system_promotion",
]
