"""Typed canonical models used by the minimum Project recording core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def utc_now() -> str:
    """Return a canonical UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Change:
    """A conflict-aware change against a canonical Project record."""

    operation: Literal["set", "append"]
    path: str
    value: Any
    base_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Observed outcome of a canonical write attempt."""

    applied: bool
    merged: bool
    revision: int
    conflict_request_id: str | None = None


@dataclass(slots=True)
class ProjectRecord:
    """Minimum typed Project record for durable recording and read-back."""

    project_id: str
    title: str
    system_version: str
    status: str = "in_progress"
    revision: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    direction: dict[str, Any] = field(
        default_factory=lambda: {
            "summary": "",
            "status": "provisional",
            "confirmed_at": None,
        }
    )
    plan: dict[str, Any] = field(
        default_factory=lambda: {
            "criteria_version": 1,
            "current_phase": None,
            "items": [],
        }
    )
    decisions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    progress: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    completion: dict[str, Any] = field(
        default_factory=lambda: {
            "requested_at": None,
            "completed_at": None,
            "completed_by": None,
        }
    )
    schema_version: str = "1.0"
    record_type: str = "project"

    def to_dict(self) -> dict[str, Any]:
        """Convert this record to its canonical JSON-compatible shape."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectRecord:
        """Build a typed record from validated canonical data."""
        return cls(**value)
