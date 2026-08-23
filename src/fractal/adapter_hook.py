"""Portable Fractal context, component guard, and work-completed hooks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from fractal.improvement import WorkSignature, WorkSignatureStore, recognise_repetition
from fractal.live_state import LiveRuntimeStateError, LiveRuntimeStateStore
from fractal.models import utc_now


def resolve_session_state(context: dict[str, Any]) -> dict[str, Any] | None:
    """Verify current Project and System Version state against canonical sources."""
    route = context.get("live_runtime")
    if route is None:
        return None
    if not isinstance(route, dict):
        raise LiveRuntimeStateError("Live runtime route is invalid")
    required = {"state_path"}
    if not required.issubset(route):
        raise LiveRuntimeStateError("Live runtime route is incomplete")
    state_path = Path(route["state_path"]).expanduser()
    store = LiveRuntimeStateStore(state_path.parent.parent, state_path=state_path)
    return store.verify_current()


def handle_hook(
    event: str,
    context: dict[str, Any],
    payload: dict[str, Any],
    *,
    live_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a typed hook result without granting new authority."""
    if event == "session-start":
        project = live_state["project"] if live_state is not None else context["active_project"]
        system_version = (
            live_state["system_version"]["version"]
            if live_state is not None
            else context["system_version"]
        )
        summary = (
            f"Fractal {system_version}; active Project {project['project_id']} "
            f"is {project['status']} at revision {project['revision']} and Phase "
            f"{project['current_phase']}. Legacy removal is "
            f"{'enabled' if context['authority']['legacy_removal_enabled'] else 'disabled'} "
            f"with {len(context['protected_legacy_roots'])} protected legacy roots. "
            "Use canonical state and the stated authority policy."
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
    tool_name = str(payload.get("tool_name") or "")
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
    governance = context.get("component_governance", {})
    managed_roots = [str(Path(root).expanduser()) for root in governance.get("managed_roots", [])]
    component_mutation_tool = re.search(
        r"(?i)(?:plugin|skill|hook|mcp|agent).*(?:install|uninstall|create|update|remove|delete)"
        r"|(?:install|uninstall|create|update|remove|delete).*(?:plugin|skill|hook|mcp|agent)",
        tool_name,
    )
    write_like = (
        component_mutation_tool
        or tool_name in {"apply_patch", "Edit", "Write"}
        or re.search(
            r"\b(?:cp|install|ln|mkdir|mv|rm|rmdir|touch|unlink|trash|delete|edit|write|overwrite)\b",
            serialized,
            flags=re.IGNORECASE,
        )
    )
    targets_managed_root = any(
        root in serialized or _home_abbreviation(root) in serialized for root in managed_roots
    )
    supported_route = re.search(
        r"(?:^|[\s\"'])(?:(?:[^\"']*/)?fractal|python(?:3)?\s+-m\s+fractal\.cli)"
        r"[\"']?\s+(?:components\s+install-candidate|codex\s+(?:config-apply|trust-hooks))"
        r"(?:\s|$)",
        serialized,
    )
    if write_like and (targets_managed_root or component_mutation_tool) and not supported_route:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "This is a Fractal-managed component surface. Use the governed route: "
                    "request, source and overlap check, Naming System, permissions and evaluation, "
                    "registration, candidate build, adapter projection, then verified install."
                ),
            }
        }
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}


def _home_abbreviation(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path


def _is_primary_user_record(value: Any) -> bool:
    """Return whether a transcript record represents a real user turn."""
    if not isinstance(value, dict):
        return False
    if value.get("isSidechain") is True or value.get("isMeta") is True:
        return False
    if value.get("isCompactSummary") is True or "toolUseResult" in value:
        return False
    if value.get("type") == "user":
        return True
    message = value.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        return True
    payload = value.get("payload")
    return isinstance(payload, dict) and payload.get("role") == "user"


def _record_identifier(value: dict[str, Any]) -> str:
    for key in ("uuid", "turn_id", "turnId", "promptId", "id"):
        identifier = value.get(key)
        if isinstance(identifier, str) and identifier:
            return identifier
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:24]


def _transcript_work_context(path: str | None) -> tuple[str | None, tuple[str, ...]]:
    """Extract the latest user-turn key and its Tool identifiers."""
    if not path:
        return None, ()
    transcript = Path(path).expanduser()
    try:
        usable = transcript.is_file() and transcript.stat().st_size <= 5_000_000
    except OSError:
        usable = False
    if not usable:
        return None, ()
    records: list[Any] = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    user_indexes = [index for index, value in enumerate(records) if _is_primary_user_record(value)]
    start = user_indexes[-1] if user_indexes else 0
    turn_key = None
    if user_indexes:
        turn_key = _record_identifier(records[start])
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value_type = value.get("type")
            name = value.get("name") or value.get("tool_name")
            if value_type in {
                "function_call",
                "tool_call",
                "custom_tool_call",
            } and isinstance(name, str):
                found.add(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for record in records[start:]:
        visit(record)
    return turn_key, tuple(sorted(found))


def _signature_from_dict(value: dict[str, Any]) -> WorkSignature:
    return WorkSignature(
        work_id=value["work_id"],
        project_id=value["project_id"],
        work_type=value["work_type"],
        input_shape=value["input_shape"],
        steps=tuple(value["steps"]),
        tools=tuple(value["tools"]),
        outcome_category=value["outcome_category"],
        purpose_class=value["purpose_class"],
        elapsed_seconds=value["elapsed_seconds"],
        token_usage=value["token_usage"],
        completed_at=value["completed_at"],
        thread_id=value.get("thread_id"),
        turn_id=value.get("turn_id"),
        tool_evidence=tuple(value.get("tool_evidence", ())),
        evidence_state=value.get("evidence_state", "stop-captured"),
    )


def capture_work_completion(
    context: dict[str, Any],
    payload: dict[str, Any],
    *,
    journal_path: Path,
    evaluations_path: Path,
) -> dict[str, Any]:
    """Capture one compact Work Signature from a real platform Stop event."""
    project = context["active_project"]
    platform = context.get("platform", "unknown")
    thread_id = payload.get("thread_id") or payload.get("threadId")
    turn_id = payload.get("turn_id") or payload.get("turnId")
    if thread_id is None and platform == "codex":
        thread_id = payload.get("session_id")
    session_id = str(thread_id or payload.get("session_id") or "unknown-session")
    turn_key, tools = _transcript_work_context(payload.get("transcript_path"))
    if isinstance(turn_id, str) and turn_id:
        turn_key = turn_id
    if turn_key is None:
        turn_key = hashlib.sha256(
            str(payload.get("last_assistant_message") or "").encode("utf-8")
        ).hexdigest()[:16]
    work_id = f"{platform}-turn-{session_id}-{turn_key}"
    store = WorkSignatureStore(journal_path)
    history_values = store.read_all()
    existing = next((item for item in history_values if item["work_id"] == work_id), None)
    if existing is not None:
        signature = _signature_from_dict(existing)
        captured = False
    else:
        signature = WorkSignature(
            work_id=work_id,
            project_id=project["project_id"],
            work_type="agent-turn",
            input_shape=f"{platform}-completed-turn",
            steps=("user-turn", "assistant-response"),
            tools=tools,
            outcome_category="completed-response",
            purpose_class="ordinary",
            elapsed_seconds=None,
            token_usage=None,
            completed_at=utc_now(),
            thread_id=str(thread_id) if thread_id else None,
            turn_id=str(turn_id) if turn_id else None,
            evidence_state="stop-captured",
        )
        try:
            captured = store.capture_completion(signature)
        except ValueError:
            # Two Hook processes can observe the same turn before either writes.
            # Treat the completed write as the stable record instead of failing
            # because their capture timestamps differ.
            refreshed = next(
                (item for item in store.read_all() if item["work_id"] == work_id),
                None,
            )
            if refreshed is None:
                raise
            signature = _signature_from_dict(refreshed)
            captured = False
    history_values = store.read_all()
    history = [
        _signature_from_dict(item) for item in history_values if item["work_id"] != work_id
    ]
    recognition = recognise_repetition(history, signature)
    evaluation = {
        "record_type": "work-signature-evaluation",
        "work_id": work_id,
        "project_id": signature.project_id,
        "platform": platform,
        "captured": captured,
        "recognition": {
            "status": recognition.status,
            "occurrence_count": recognition.occurrence_count,
            "confidence": recognition.confidence,
            "route": recognition.route,
            "evidence_work_ids": list(recognition.evidence_work_ids),
            "supporting_action": recognition.supporting_action,
        },
        "evaluated_at": utc_now(),
    }
    evaluations_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = evaluations_path.with_suffix(evaluations_path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            prior = []
            if evaluations_path.exists():
                prior = [
                    json.loads(line)
                    for line in evaluations_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            if not any(item["work_id"] == work_id for item in prior):
                with evaluations_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(evaluation, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    # Stop hooks have no additional-context output. Keep the capture silent so
    # recording completion cannot itself trigger another assistant response.
    return {"suppressOutput": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--event", choices=["session-start", "pre-tool-use", "work-completed"], required=True
    )
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--evaluations", type=Path)
    arguments = parser.parse_args(argv)
    context = json.loads(arguments.context.expanduser().read_text(encoding="utf-8"))
    payload = json.load(sys.stdin)
    if arguments.event == "work-completed":
        if arguments.journal is None or arguments.evaluations is None:
            parser.error("work-completed requires --journal and --evaluations")
        result = capture_work_completion(
            context,
            payload,
            journal_path=arguments.journal.expanduser(),
            evaluations_path=arguments.evaluations.expanduser(),
        )
    else:
        if arguments.event == "session-start":
            try:
                live_state = resolve_session_state(context)
            except LiveRuntimeStateError as error:
                result = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": (
                            "FRACTAL LIVE STATE ERROR: current Project or System Version "
                            f"could not be verified ({error}). Do not use the adapter build "
                            "snapshot as current truth. Stop Project-state-dependent routing "
                            "until canonical live state is repaired."
                        ),
                    }
                }
            else:
                result = handle_hook(
                    arguments.event,
                    context,
                    payload,
                    live_state=live_state,
                )
        else:
            result = handle_hook(arguments.event, context, payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
