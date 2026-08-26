"""Portable Fractal context, component guard, and work-completed hooks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import shlex
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
                "fractalObservation": _signed_hook_observation(
                    {
                        "record_type": "fresh-session-hook-observation",
                        "record_version": 1,
                        "issuer": "fractal-adapter-hook",
                        "observed_at": utc_now(),
                        "version": system_version,
                        "session_id": str(
                            payload.get("session_id")
                            or payload.get("conversation_id")
                            or payload.get("source")
                            or "unknown-session"
                        ),
                        "hook_sha256": _hook_module_sha256(),
                        "decision": "session-start-verified",
                    }
                ),
            }
        }
    if event != "pre-tool-use":
        raise ValueError(f"Unsupported adapter hook event: {event}")
    serialized = json.dumps(payload.get("tool_input", {}), ensure_ascii=False)
    tool_name = str(payload.get("tool_name") or "")
    publication_decision = _publication_guard(context, payload, serialized, tool_name)
    if publication_decision is not None:
        return publication_decision
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


def _publication_guard(
    context: dict[str, Any],
    payload: dict[str, Any],
    serialized: str,
    tool_name: str,
) -> dict[str, Any] | None:
    """Close Fractal-owned publication routes without claiming external interception."""
    governance = context.get("publication_governance", {})
    roots = [str(Path(path).expanduser()) for path in governance.get("repository_roots", [])]
    repository_ids = [
        str(value).lower() for value in governance.get("repository_ids", []) if value
    ]
    if not roots and not repository_ids:
        return None
    tool_input = payload.get("tool_input", {})
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    workdir = ""
    if isinstance(tool_input, dict):
        workdir = str(tool_input.get("workdir") or tool_input.get("cwd") or "")
    targets_owned = any(
        root in serialized
        or _home_abbreviation(root) in serialized
        or _path_is_within(workdir, root)
        for root in roots
    ) or any(repository_id in serialized.lower() for repository_id in repository_ids)
    governed = _is_governed_publication_command(command)
    raw_git = re.search(
        r"(?:^|\s)(?:\S*/)?git\b[^\n;&|]*\b(?:push|send-pack)\b", command
    )
    low_level_cli = re.search(
        r"(?:^|\s)gh\s+(?:api\b[^\n]*--method\s+(?:POST|PATCH|PUT|DELETE)"
        r"|ref\s+(?:write|create|update|delete))",
        command,
        flags=re.IGNORECASE,
    )
    low_level_tool = re.search(
        r"(?i)(?:github|git).*(?:create|update|delete|write).*(?:ref|branch)"
        r"|(?:create|update|delete|write).*(?:ref|branch)",
        tool_name,
    )
    mutation = raw_git or low_level_cli or low_level_tool
    if targets_owned and governed:
        argv = shlex.split(command)
        order_sha256 = _option_value(argv, "--order-sha256")
        repository_id = _owned_repository_id(
            roots, repository_ids, serialized, workdir
        )
        if (
            re.fullmatch(r"[a-f0-9]{64}", str(order_sha256)) is None
            or repository_id is None
        ):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Governed publication requires an exact order digest and repository "
                        "identity observation. VersionStore must validate live Hook trust."
                    ),
                }
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Exact governed Fractal publication route.",
                "fractalObservation": _signed_hook_observation(
                    {
                        "record_type": "publication-route-hook-observation",
                        "record_version": 1,
                        "issuer": "fractal-adapter-hook",
                        "observed_at": utc_now(),
                        "version": context.get("system_version"),
                        "order_sha256": order_sha256,
                        "repository_id": repository_id,
                        "hook_sha256": _hook_module_sha256(),
                        "trust_status": "requires-version-store-validation",
                        "tool_name": tool_name or "exec_command",
                        "tool_input_sha256": _canonical_value_sha256(tool_input),
                        "decision": "allow-governed-publication",
                    }
                ),
            }
        }
    if targets_owned and mutation and not governed:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Fractal-owned publication must use the exact governed executor route: "
                    "fractal version publish. This Hook does not claim to intercept Carson's "
                    "external Terminal or UI."
                ),
            }
        }
    return None


def _is_governed_publication_command(command: str) -> bool:
    if re.search(r"(?:;|&&|\|\||\n|`|\$\()", command):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    executable = Path(argv[0]).name
    if executable == "fractal":
        return argv[1:3] == ["version", "publish"]
    return (
        executable in {"python", "python3"}
        and argv[1:5] == ["-m", "fractal.cli", "version", "publish"]
    )


def _option_value(argv: list[str], option: str) -> str | None:
    try:
        return argv[argv.index(option) + 1]
    except (ValueError, IndexError):
        return None


def _owned_repository_id(
    roots: list[str],
    repository_ids: list[str],
    serialized: str,
    workdir: str,
) -> str | None:
    for repository_id in repository_ids:
        if repository_id in serialized.lower():
            return repository_id
    if workdir:
        candidate = Path(workdir).expanduser().resolve()
        for index, root in enumerate(roots):
            try:
                candidate.relative_to(Path(root).expanduser().resolve())
            except ValueError:
                continue
            if index < len(repository_ids):
                return repository_ids[index]
    return None


def _path_is_within(candidate_path: str, root: str) -> bool:
    if not candidate_path:
        return False
    try:
        Path(candidate_path).expanduser().resolve().relative_to(
            Path(root).expanduser().resolve()
        )
    except (OSError, ValueError):
        return False
    return True


def _canonical_value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hook_module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _signed_hook_observation(content: dict[str, Any]) -> dict[str, Any]:
    return {**content, "observation_sha256": _canonical_value_sha256(content)}


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


def _user_record_text(value: dict[str, Any]) -> str:
    """Extract only the real user's content for a non-reversible request-shape digest."""
    message = value.get("message")
    payload = value.get("payload")
    container = message if isinstance(message, dict) else payload
    if not isinstance(container, dict):
        return ""
    content = container.get("content")
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("input_text")
                if isinstance(text, str):
                    parts.append(text)
    return " ".join(parts)


def _request_shape_digest(text: str) -> str | None:
    """Hash a redacted request shape so paths, URLs, ids and raw text are not retained."""
    if not text.strip():
        return None
    normalized = text.lower()
    normalized = re.sub(r"https?://\S+", " <url> ", normalized)
    normalized = re.sub(r"(?:^|\s)(?:/|~/?|\.\.?/)[^\s]+", " <path> ", normalized)
    normalized = re.sub(r"\b[0-9a-f]{12,}\b", " <id> ", normalized)
    normalized = re.sub(r"\b\d+(?:\.\d+)*\b", " <number> ", normalized)
    normalized = re.sub(r"[\"'`][^\"'`]{1,120}[\"'`]", " <value> ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _request_class(text: str) -> str:
    """Return a small non-sensitive task class without retaining user wording."""
    normalized = text.lower()
    classes = (
        "assess",
        "automate",
        "build",
        "complete",
        "create",
        "deploy",
        "edit",
        "fix",
        "match",
        "organize",
        "publish",
        "research",
        "review",
        "summarize",
        "test",
        "translate",
        "write",
    )
    for task_class in classes:
        if re.search(rf"\b{task_class}(?:e[sd]?|ing)?\b", normalized):
            return task_class
    return "general"


def _transcript_work_context(
    path: str | None,
) -> tuple[str | None, tuple[str, ...], str | None, str]:
    """Extract the latest real user-turn key, Tool ids and redacted request shape."""
    if not path:
        return None, (), None, "general"
    transcript = Path(path).expanduser()
    try:
        usable = transcript.is_file() and transcript.stat().st_size <= 5_000_000
    except OSError:
        usable = False
    if not usable:
        return None, (), None, "general"
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
    request_text = _user_record_text(records[start]) if user_indexes else ""
    request_shape = _request_shape_digest(request_text)
    request_class = _request_class(request_text)
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
    return turn_key, tuple(sorted(found)), request_shape, request_class


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
    live_state = resolve_session_state(context)
    project = live_state["project"] if live_state is not None else context["active_project"]
    platform = context.get("platform", "unknown")
    thread_id = payload.get("thread_id") or payload.get("threadId")
    turn_id = payload.get("turn_id") or payload.get("turnId")
    if thread_id is None and platform == "codex":
        thread_id = payload.get("session_id")
    session_id = str(thread_id or payload.get("session_id") or "unknown-session")
    turn_key, tools, request_shape, request_class = _transcript_work_context(
        payload.get("transcript_path")
    )
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
            work_type=f"request-{request_class}",
            input_shape=(
                f"{platform}-request-{request_shape}"
                if request_shape is not None
                else f"{platform}-completed-turn"
            ),
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
    history = [_signature_from_dict(item) for item in history_values if item["work_id"] != work_id]
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
    if recognition.status == "investigation-required":
        try:
            if live_state is None:
                raise LiveRuntimeStateError(
                    "Fatigue orchestration requires verified canonical live state"
                )
            from fractal.orchestrator import FractalOrchestrator
            from fractal.self_improvement import MethodCandidateStore, PostWorkLearning
            from fractal.storage import ProjectStore

            state_path = Path(context["live_runtime"]["state_path"]).expanduser()
            runtime_root = state_path.parent.parent
            project_path = Path(live_state["project"]["source_path"])
            project_store = ProjectStore(project_path.parent.parent, runtime_root)
            orchestrator = FractalOrchestrator(project_store)
            outcome = orchestrator.handle_fatigue(
                recognition,
                signature,
                actor="fractal-runtime",
                platform=platform,
            )
            learning = None
            research_action_id = (outcome.get("result") or {}).get("research_action_id")
            if research_action_id:
                candidate_store = MethodCandidateStore(runtime_root / "learning")
                reviewer = PostWorkLearning(
                    constraint_history=candidate_store.recent_constraint_reports()
                )
                learning = orchestrator.run_learning_review(
                    research_action_id,
                    reviewer=reviewer,
                    candidate_store=candidate_store,
                )
            evaluation["orchestration"] = {
                "status": "completed",
                "execution_id": outcome["execution"]["execution_id"],
                "idempotent": outcome["idempotent"],
                "result": outcome["result"],
                "learning": (
                    {
                        "status": learning["result"]["status"],
                        "candidate_id": (
                            learning["result"].get("candidate_manifest") or {}
                        ).get("candidate_id"),
                        "canonical_evidence_id": learning["result"].get(
                            "canonical_evidence_id"
                        ),
                        "next_action_id": learning.get("next_action", {}).get(
                            "action_id"
                        ),
                    }
                    if learning is not None
                    else None
                ),
            }
        except Exception as error:
            evaluation["orchestration"] = {
                "status": "failed",
                "error": str(error),
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
