"""Typed canonical models used by the minimum Project recording core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def utc_now() -> str:
    """Return a canonical UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_lifecycle() -> dict[str, Any]:
    """Return the initial lifecycle state for a formal Project."""
    return {
        "brief": {
            "summary": "",
            "source": "",
            "recorded_at": None,
        },
        "direction": {
            "intended_outcome": "",
            "deliverable": "",
            "completion_standard": "",
            "exclusions": "",
            "status": "provisional",
            "version": 1,
            "confirmations": [],
            "material_change_reason": None,
        },
        "goal": {
            "statement": "",
            "status": "provisional",
            "version": 1,
            "approved_at": None,
        },
        "success_criteria": {
            "version": 1,
            "status": "candidate",
            "items": [],
            "pre_work_challenge": None,
            "post_work_challenges": [],
        },
        "priorities": [],
        "plan_history": [],
        "review_points": [],
        "deviations": [],
        "reviews": [],
        "unknowns": [],
        "biggest_remaining_concern": {
            "summary": "Not assessed",
            "evidence_ids": [],
        },
    }


def default_plan() -> dict[str, Any]:
    """Return a Project Plan with honest minimum resource states from the start."""
    return {
        "criteria_version": 1,
        "current_phase": None,
        "items": [],
        "resources": [
            {
                "dimension": dimension,
                "plan_state": "unknown-at-plan-time",
                "estimate": None,
                "unit": None,
                "reason": "No estimate was provided when the Project Plan was created.",
            }
            for dimension in ("time", "attention")
        ],
    }


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
    lifecycle: dict[str, Any] = field(default_factory=default_lifecycle)
    plan: dict[str, Any] = field(default_factory=default_plan)
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
    schema_version: str = "1.2"
    record_type: str = "project"

    def to_dict(self) -> dict[str, Any]:
        """Convert this record to its canonical JSON-compatible shape."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectRecord:
        """Build a typed record from validated canonical data."""
        return cls(**value)
