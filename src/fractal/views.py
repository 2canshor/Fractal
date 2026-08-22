"""Human Control views derived from canonical Project state."""

from __future__ import annotations

from collections import Counter

from fractal.models import ProjectRecord


def render_project_summary(record: ProjectRecord) -> str:
    """Render a compact truthful status view without changing canonical state."""
    decisions = Counter(item["status"] for item in record.decisions)
    pending_requests = sum(item["status"] == "pending" for item in record.requests)
    phase = record.plan["current_phase"]
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
    ]
    return "\n".join(lines) + "\n"
