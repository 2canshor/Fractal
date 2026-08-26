"""Human Control views derived from canonical Project state."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from fractal.models import ProjectRecord

_FEEDBACK_STATE_LABELS = {
    "ready": "Ready",
    "in_progress": "In progress",
    "blocked": "Blocked",
    "empty": "Nothing here yet",
    "error": "Could not finish",
    "unknown": "Not confirmed",
    "completed": "Completed",
}
_HELP_REQUIRED_STATES = frozenset({"blocked", "empty", "error", "unknown"})


def render_project_summary(record: ProjectRecord, *, details: bool = False) -> str:
    """Render a compact truthful status view without changing canonical state."""
    decisions = Counter(item["status"] for item in record.decisions)
    pending_requests = sum(item["status"] == "pending" for item in record.requests)
    phase = record.plan["current_phase"]
    lifecycle = record.lifecycle
    criteria = lifecycle["success_criteria"]
    achieved_criteria = sum(item["achieved"] for item in criteria["items"])
    open_unknowns = sum(item["status"] == "open" for item in lifecycle["unknowns"])
    concern = lifecycle["biggest_remaining_concern"]["summary"]
    next_action = _project_next_action(record, pending_requests, concern)
    lines = [
        f"# {record.title}",
        "",
        "## Current status",
        "",
        f"- Status: `{record.status}`",
        f"- Current Phase: `{phase if phase is not None else 'not-set'}`",
        f"- Direction: `{lifecycle['direction']['status']}`",
        f"- Goal: `{lifecycle['goal']['status']}`",
        f"- Success Criteria: `{achieved_criteria}/{len(criteria['items'])}` achieved",
        "",
        "## What needs attention",
        "",
        f"- Biggest Remaining Concern: {concern}",
        f"- Pending Requests: `{pending_requests}`",
        f"- Open Decisions: `{decisions['open']}`",
        f"- Open Unknowns: `{open_unknowns}`",
        "",
        "## Next action",
        "",
        next_action,
        "",
        "## Record details",
        "",
        f"- Project ID: `{record.project_id}`",
        f"- System Version: `{record.system_version}`",
        f"- Record Revision: `{record.revision}`",
        f"- Evidence Records: `{len(record.evidence)}`",
        f"- Progress Records: `{len(record.progress)}`",
        f"- Approved Decisions: `{decisions['approved']}`",
        f"- Proposed Decisions: `{decisions['proposed']}`",
        f"- Perspectives: `{len(lifecycle['reviews'])}`",
        "- Recovery: This is a read-only view; the canonical Project record is unchanged.",
    ]
    if pending_requests:
        lines.append("- Authority: An authorised user must resolve each pending request.")
    if details:
        direction = lifecycle["direction"]
        lines.extend(
            [
                "",
                "## Project Direction",
                "",
                f"- Intended Outcome: {direction['intended_outcome'] or 'Not set'}",
                f"- Deliverable: {direction['deliverable'] or 'Not set'}",
                f"- Completion Standard: {direction['completion_standard'] or 'Not set'}",
                f"- Exclusions: {direction['exclusions'] or 'Not set'}",
            ]
        )
    return "\n".join(lines) + "\n"


def render_user_feedback(
    title: str,
    *,
    state: str,
    summary: str,
    reason: str | None = None,
    next_action: str | None = None,
    authority: str | None = None,
    recovery: str | None = None,
    ai_assistance: Mapping[str, str] | None = None,
) -> str:
    """Render one semantic, recoverable text view for status and failure feedback."""
    if state not in _FEEDBACK_STATE_LABELS:
        raise ValueError(f"Unsupported feedback state: {state}")
    _require_text("title", title)
    _require_text("summary", summary)
    if state in _HELP_REQUIRED_STATES:
        _require_text("reason", reason)
        _require_text("next_action", next_action)
    if (authority is None) != (recovery is None):
        raise ValueError("Significant feedback requires both authority and recovery")
    if ai_assistance is not None:
        missing = sorted({"limits", "retry", "revert"}.difference(ai_assistance))
        if missing:
            raise ValueError(f"AI assistance metadata is missing: {missing}")
        for key in ("limits", "retry", "revert"):
            _require_text(f"ai_assistance.{key}", ai_assistance[key])

    lines = [
        f"# {title.strip()}",
        "",
        "## Current status",
        "",
        f"- Status: {_FEEDBACK_STATE_LABELS[state]}",
        f"- Summary: {summary.strip()}",
    ]
    if reason is not None:
        lines.append(f"- Reason: {reason.strip()}")
    if next_action is not None:
        lines.append(f"- Next action: {next_action.strip()}")
    if authority is not None and recovery is not None:
        lines.extend(
            [
                "",
                "## Before you decide",
                "",
                f"- Authority: {authority.strip()}",
                f"- Recovery: {recovery.strip()}",
            ]
        )
    if ai_assistance is not None:
        lines.extend(
            [
                "",
                "## AI assistance",
                "",
                f"- Use: {ai_assistance.get('use', 'AI helped prepare this result.').strip()}",
                f"- Limits: {ai_assistance['limits'].strip()}",
                f"- Retry: {ai_assistance['retry'].strip()}",
                f"- Revert: {ai_assistance['revert'].strip()}",
            ]
        )
    return "\n".join(lines) + "\n"


def _project_next_action(record: ProjectRecord, pending_requests: int, concern: str) -> str:
    if record.status == "completed":
        return "No further Project action is required while this completed state remains current."
    if pending_requests:
        return "Review the oldest pending request and record the authorised decision."
    if record.plan["current_phase"] is None:
        return "Set the current phase, then record the first verified result."
    if not concern.strip() or concern.strip().casefold() == "not assessed":
        return "Assess the biggest remaining concern, then record evidence for the current phase."
    return f"Continue Phase {record.plan['current_phase']} and record the next verified result."


def _require_text(field: str, value: str | None) -> None:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} must be specific and non-empty")
