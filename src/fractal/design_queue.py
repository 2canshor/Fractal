"""Single-question queue for unresolved implementation design."""

from __future__ import annotations

import copy
from typing import Any


class OpenDesignQueueError(RuntimeError):
    """Raised when queue order or status would become ambiguous."""


def activate_next(queue: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Activate at most one waiting design question."""
    updated = copy.deepcopy(queue)
    active = [item for item in updated["questions"] if item["status"] == "active"]
    if len(active) > 1:
        raise OpenDesignQueueError("Only one design question may be active")
    if active:
        return updated, active[0]
    next_item = next(
        (item for item in updated["questions"] if item["status"] == "waiting"),
        None,
    )
    if next_item is not None:
        next_item["status"] = "active"
    return updated, next_item


def resolve_active(
    queue: dict[str, Any],
    *,
    question_id: str,
    outcome_status: str,
    outcome_summary: str,
) -> dict[str, Any]:
    """Resolve the active question before another can begin."""
    if outcome_status not in {"approved", "rejected", "deferred", "open"}:
        raise OpenDesignQueueError(f"Invalid design outcome: {outcome_status}")
    updated = copy.deepcopy(queue)
    active = [item for item in updated["questions"] if item["status"] == "active"]
    if len(active) != 1 or active[0]["id"] != question_id:
        raise OpenDesignQueueError("The requested design question is not the single active item")
    active[0]["status"] = "resolved"
    active[0]["outcome_status"] = outcome_status
    active[0]["outcome_summary"] = outcome_summary
    return updated
