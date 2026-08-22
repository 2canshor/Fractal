"""Human Control views derived from canonical Project state."""

from __future__ import annotations

from collections import Counter

from fractal.models import ProjectRecord


def render_project_summary(record: ProjectRecord, *, details: bool = False) -> str:
    """Render a compact truthful status view without changing canonical state."""
    decisions = Counter(item["status"] for item in record.decisions)
    pending_requests = sum(item["status"] == "pending" for item in record.requests)
    phase = record.plan["current_phase"]
    lifecycle = record.lifecycle
    criteria = lifecycle["success_criteria"]
    achieved_criteria = sum(item["achieved"] for item in criteria["items"])
    open_unknowns = sum(item["status"] == "open" for item in lifecycle["unknowns"])
    lines = [
        f"# {record.title}",
        "",
        f"- Project ID: `{record.project_id}`",
        f"- Status: `{record.status}`",
        f"- System Version: `{record.system_version}`",
        f"- Record Revision: `{record.revision}`",
        f"- Current Phase: `{phase if phase is not None else 'not-set'}`",
        f"- Evidence Records: `{len(record.evidence)}`",
        f"- Progress Records: `{len(record.progress)}`",
        f"- Pending Decisions: `{pending_requests}`",
        f"- Approved Decisions: `{decisions['approved']}`",
        f"- Proposed Decisions: `{decisions['proposed']}`",
        f"- Open Decisions: `{decisions['open']}`",
        f"- Direction: `{lifecycle['direction']['status']}`",
        f"- Goal: `{lifecycle['goal']['status']}`",
        f"- Success Criteria: `{achieved_criteria}/{len(criteria['items'])}` achieved",
        f"- Project Reviews: `{len(lifecycle['reviews'])}`",
        f"- Open Unknowns: `{open_unknowns}`",
        f"- Biggest Remaining Concern: {lifecycle['biggest_remaining_concern']['summary']}",
    ]
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
