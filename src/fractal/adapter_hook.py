"""Portable SessionStart and protected-legacy guard hook."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def handle_hook(event: str, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Return a typed hook result without granting new authority."""
    if event == "session-start":
        project = context["active_project"]
        summary = (
            f"Fractal {context['system_version']}; active Project {project['project_id']} "
            f"is {project['status']} at revision {project['revision']} and Phase "
            f"{project['current_phase']}. Use canonical state and the stated authority policy."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": summary,
            }
        }
    if event != "pre-tool-use":
        raise ValueError(f"Unsupported adapter hook event: {event}")
    serialized = json.dumps(payload.get("tool_input", {}), ensure_ascii=False)
    destructive = re.search(
        r"\b(?:rm|rmdir|unlink|trash|mv|delete|overwrite)\b",
        serialized,
        flags=re.IGNORECASE,
    )
    protected = any(root in serialized for root in context["protected_legacy_roots"])
    if destructive and protected and not context["authority"]["legacy_removal_enabled"]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Protected legacy material cannot be removed before the verified "
                    "cutover state enables it."
                ),
            }
        }
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=["session-start", "pre-tool-use"], required=True)
    parser.add_argument("--context", type=Path, required=True)
    arguments = parser.parse_args(argv)
    context = json.loads(arguments.context.expanduser().read_text(encoding="utf-8"))
    payload = json.load(sys.stdin)
    print(json.dumps(handle_hook(arguments.event, context, payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
