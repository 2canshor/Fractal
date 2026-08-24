"""Stage privacy-bounded session signals adapted from Hermes Dojo.

Donor: Hermes Dojo, commit ee114e72e18b13d3aeb4b76a8d1ade0916972248.
Original files: scripts/monitor.py and scripts/analyzer.py.
Licence: MIT, Copyright (c) 2026 Yonkoo11. See THIRD_PARTY_NOTICES.md.

This adaptation retains bounded detection ideas and removes Hermes storage,
skill mutation, GEPA, cron, reporting delivery and promotion authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Literal

ERROR_PATTERNS = {
    "authentication": re.compile(r"\b(?:unauthori[sz]ed|forbidden|invalid credentials?)\b", re.I),
    "connection": re.compile(r"\b(?:connection refused|unreachable|no such host)\b", re.I),
    "permission": re.compile(r"\b(?:permission denied|access denied|eacces)\b", re.I),
    "rate-limit": re.compile(r"\b(?:rate limit|too many requests|http 429)\b", re.I),
    "timeout": re.compile(r"\b(?:timed? ?out|etimedout)\b", re.I),
    "tool-error": re.compile(r"\b(?:traceback|exception|command not found|enoent)\b", re.I),
}
CORRECTION_PATTERNS = {
    "explicit-correction": re.compile(
        r"(?:^|\b)(?:no[,.: ]|wrong|incorrect|i meant|you misunderstood|not what i)(?:\b|$)",
        re.I,
    ),
    "recovery-request": re.compile(r"\b(?:undo|revert|try again|that broke)\b", re.I),
}


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """A typed donor-neutral event; raw messages are never returned as signals."""

    event_id: str
    session_id: str
    timestamp: float
    role: Literal["assistant", "tool", "user"]
    content: str = ""
    tool_name: str | None = None
    tool_calls: tuple[str, ...] = ()
    explicit_error: bool | None = None


@dataclass(frozen=True, slots=True)
class SessionSignal:
    """One bounded observation that cannot approve or prescribe a change."""

    signal_id: str
    signal_type: Literal["possible-user-correction", "rapid-retry", "tool-failure"]
    category: str
    session_id: str
    event_ids: tuple[str, ...]
    tool_name: str | None
    content_sha256: str | None
    confidence: Literal["high", "medium"]
    route: Literal["find-problems"] = "find-problems"
    automatic_change: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["event_ids"] = list(self.event_ids)
        return value


def _content_digest(content: str) -> str | None:
    if not content:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _classify_tool_failure(event: SessionEvent) -> tuple[str, str] | None:
    if event.role != "tool":
        return None
    if event.explicit_error is False:
        return None
    if event.explicit_error is True:
        return "explicit-error", "high"
    for category, pattern in ERROR_PATTERNS.items():
        if pattern.search(event.content):
            return category, "medium"
    return None


def extract_session_signals(events: list[SessionEvent]) -> list[SessionSignal]:
    """Extract evidence signals without making weakness, cause or mutation decisions."""
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("Session events require unique ids")
    signals: list[SessionSignal] = []
    for event in sorted(events, key=lambda item: (item.session_id, item.timestamp, item.event_id)):
        failure = _classify_tool_failure(event)
        if failure is not None:
            category, confidence = failure
            signals.append(
                SessionSignal(
                    signal_id=f"signal-tool-failure-{event.event_id}",
                    signal_type="tool-failure",
                    category=category,
                    session_id=event.session_id,
                    event_ids=(event.event_id,),
                    tool_name=event.tool_name,
                    content_sha256=_content_digest(event.content),
                    confidence=confidence,
                )
            )
        if event.role == "user" and event.content:
            for category, pattern in CORRECTION_PATTERNS.items():
                if pattern.search(event.content):
                    signals.append(
                        SessionSignal(
                            signal_id=f"signal-user-correction-{event.event_id}",
                            signal_type="possible-user-correction",
                            category=category,
                            session_id=event.session_id,
                            event_ids=(event.event_id,),
                            tool_name=None,
                            content_sha256=_content_digest(event.content),
                            confidence="medium",
                        )
                    )
                    break

    calls_by_session: dict[str, list[tuple[float, str, str]]] = {}
    for event in events:
        if event.role != "assistant":
            continue
        for tool_name in event.tool_calls:
            calls_by_session.setdefault(event.session_id, []).append(
                (event.timestamp, event.event_id, tool_name)
            )
    for session_id, calls in calls_by_session.items():
        ordered = sorted(calls)
        run: list[tuple[float, str, str]] = []
        for call in ordered:
            if run and (call[2] != run[-1][2] or call[0] - run[-1][0] >= 30):
                _append_retry_signal(signals, session_id, run)
                run = []
            run.append(call)
        _append_retry_signal(signals, session_id, run)
    return sorted(signals, key=lambda item: item.signal_id)


def _append_retry_signal(
    signals: list[SessionSignal],
    session_id: str,
    run: list[tuple[float, str, str]],
) -> None:
    if len(run) < 3:
        return
    event_ids = tuple(item[1] for item in run)
    tool_name = run[0][2]
    signals.append(
        SessionSignal(
            signal_id=f"signal-rapid-retry-{session_id}-{event_ids[0]}",
            signal_type="rapid-retry",
            category="same-tool-within-30-seconds",
            session_id=session_id,
            event_ids=event_ids,
            tool_name=tool_name,
            content_sha256=None,
            confidence="high",
        )
    )
