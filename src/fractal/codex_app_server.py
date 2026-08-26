"""Evidence-led Codex App Server inspection and controlled runtime operations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import selectors
import subprocess
import time
import uuid
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from fractal.component_governance import active_components
from fractal.improvement import WorkSignature, WorkSignatureStore, recognise_repetition
from fractal.models import utc_now
from fractal.user_surface import audit_codex_skill_path_surface


class CodexAppServerError(RuntimeError):
    """Raised when the local App Server rejects or cannot complete a request."""


class CodexAppServerClient:
    """Small newline-delimited JSON-RPC client for the installed Codex runtime."""

    def __init__(
        self,
        executable: str = "codex",
        *,
        timeout: float = 30.0,
        config_overrides: list[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self._next_id = 1
        self.notifications: list[dict[str, Any]] = []
        command = [executable, "app-server", "--stdio"]
        for override in config_overrides or []:
            command.extend(["--config", override])
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise CodexAppServerError("Could not open the Codex App Server pipes")
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "fractal", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized", {})

    def __enter__(self) -> CodexAppServerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _write(self, value: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            raise CodexAppServerError("Codex App Server stopped unexpectedly")
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            message = self.read_message(max(0.0, deadline - time.monotonic()))
            if message is None:
                raise CodexAppServerError(f"Timed out waiting for {method}")
            if message.get("id") != request_id:
                self.notifications.append(message)
                continue
            if "error" in message:
                error = message["error"]
                detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                raise CodexAppServerError(f"{method}: {detail}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise CodexAppServerError(f"{method}: invalid response")
            return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": method, "params": params or {}})

    def read_message(self, timeout: float) -> dict[str, Any] | None:
        events = self._selector.select(timeout)
        if not events:
            return None
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            raise CodexAppServerError("Codex App Server closed its output")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise CodexAppServerError("Codex App Server returned a non-object message")
        return value

    def wait_for_notification(
        self,
        methods: set[str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        for index, message in enumerate(self.notifications):
            if message.get("method") in methods:
                return self.notifications.pop(index)
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while True:
            message = self.read_message(max(0.0, deadline - time.monotonic()))
            if message is None:
                raise CodexAppServerError("Timed out waiting for " + ", ".join(sorted(methods)))
            if message.get("method") in methods:
                return message
            self.notifications.append(message)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self._selector.close()


def detect_codex_compatibility(
    client: CodexAppServerClient,
    *,
    executable: str = "codex",
) -> dict[str, Any]:
    """Probe the installed version instead of assuming source-main support."""
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    probes: dict[str, dict[str, Any]] = {}
    requests = {
        # Remote and implicitly installed Plugins can register their Skills lazily.
        # Discover Plugins before asking for the Skill catalogue or the result can
        # omit exactly the entries the live Codex menu later exposes.
        "plugin/installed": {"cwds": [str(Path.cwd())]},
        "skills/list": {"cwds": [str(Path.cwd())], "forceReload": True},
        "hooks/list": {"cwds": [str(Path.cwd())]},
        "mcpServerStatus/list": {"detail": "toolsAndAuthOnly"},
        "app/installed": {"forceRefresh": False},
        "config/read": {"cwd": str(Path.cwd()), "includeLayers": True},
        "configRequirements/read": {},
        "externalAgentConfig/detect": {"cwds": [str(Path.cwd())], "includeHome": True},
        "plugin/list": {},
    }
    for method, params in requests.items():
        try:
            client.request(method, params)
        except CodexAppServerError as error:
            probes[method] = {"supported": False, "detail": str(error)}
        else:
            probes[method] = {
                "supported": True,
                "authority": "auxiliary" if method == "plugin/list" else "runtime",
            }
    return {
        "record_type": "codex-version-compatibility",
        "codex_version": version,
        "observed_at": utc_now(),
        "methods": probes,
        "source_main_assumed": False,
    }


def load_codex_skill_catalog(
    client: CodexAppServerClient,
    *,
    cwd: Path,
    force_reload: bool = True,
    required_stable_reads: int = 2,
    maximum_passes: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the Skill catalogue only after Plugin discovery reaches a fixed point."""
    if required_stable_reads < 1 or maximum_passes < required_stable_reads:
        raise ValueError("Skill catalogue convergence limits are invalid")
    resolved_cwd = Path(cwd).expanduser().resolve()
    previous_signature: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    stable_reads = 0
    for _ in range(maximum_passes):
        plugins = client.request("plugin/installed", {"cwds": [str(resolved_cwd)]})
        response = client.request(
            "skills/list",
            {"cwds": [str(resolved_cwd)], "forceReload": force_reload},
        )
        buckets = response.get("data") or []
        if len(buckets) != 1:
            raise CodexAppServerError("Codex returned an unexpected Skill-list scope")
        skills = buckets[0].get("skills") or []
        installed_plugin_ids = tuple(
            sorted(
                str(plugin["id"])
                for marketplace in plugins.get("marketplaces") or []
                for plugin in marketplace.get("plugins") or []
                if plugin.get("installed") and plugin.get("enabled", True)
            )
        )
        skill_paths = tuple(sorted(str(item.get("path") or "") for item in skills))
        signature = (installed_plugin_ids, skill_paths)
        stable_reads = stable_reads + 1 if signature == previous_signature else 1
        if stable_reads >= required_stable_reads:
            return skills, plugins
        previous_signature = signature
    raise CodexAppServerError(
        "Codex Plugin and Skill catalogue did not converge before the audit limit"
    )


def _normalise_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _component_skill_paths(component: dict[str, Any], codex_home: Path) -> set[str]:
    paths: set[str] = set()
    for value in (component["source"]["locator"], component["projection"]["target"]):
        if not value or value.startswith(("platform-", "plugin:")):
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            if str(value).startswith("skills/"):
                path = codex_home / value
            else:
                continue
        paths.add(_normalise_path(path))
        paths.add(_normalise_path(path / "SKILL.md"))
    return paths


def _normalise_hook_event(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _canonical_hook_event_label(value: str) -> str:
    canonical = {
        "pretooluse": "PreToolUse",
        "sessionstart": "SessionStart",
        "stop": "Stop",
    }.get(_normalise_hook_event(value))
    if canonical is None:
        raise CodexAppServerError(f"Unsupported registered Hook event: {value}")
    return canonical


def _all_mcp_status(client: CodexAppServerClient) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"detail": "full", "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = client.request("mcpServerStatus/list", params)
        result.extend(page.get("data", []))
        cursor = page.get("nextCursor")
        if not cursor:
            return result


def reconcile_codex_components(
    registry: dict[str, Any],
    client: CodexAppServerClient,
    *,
    cwd: Path,
    codex_home: Path,
) -> dict[str, Any]:
    """Compare Fractal's expected set with what Codex reports as loaded now."""
    expected = active_components(registry, "codex")
    expected_by_id = {item["component_id"]: item for item in expected}
    loaded_skills, plugins_response = load_codex_skill_catalog(
        client,
        cwd=cwd,
        force_reload=True,
    )
    hooks_response = client.request("hooks/list", {"cwds": [str(cwd)]})
    mcp_statuses = _all_mcp_status(client)
    apps = client.request("app/installed", {"forceRefresh": False}).get("apps", [])
    config = client.request("config/read", {"cwd": str(cwd), "includeLayers": False})["config"]
    thread = client.request(
        "thread/start",
        {
            "cwd": str(cwd),
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        },
    )
    instruction_sources = {_normalise_path(path) for path in thread.get("instructionSources", [])}

    loaded_hooks: list[dict[str, Any]] = []
    for entry in hooks_response.get("data", []):
        loaded_hooks.extend(entry.get("hooks", []))

    matched_skills: set[int] = set()
    matched_hooks: set[int] = set()
    matched_mcps: set[str] = set()
    matched_apps: set[str] = set()
    matched_plugins: set[str] = set()
    records: list[dict[str, Any]] = []

    mcp_by_name = {item["name"]: item for item in mcp_statuses}
    app_by_id = {item["id"]: item for item in apps}
    installed_plugins = [
        plugin
        for marketplace in plugins_response.get("marketplaces", [])
        for plugin in marketplace.get("plugins", [])
        if plugin.get("installed")
    ]
    plugin_by_id = {item["id"]: item for item in installed_plugins}
    configured_mcp = config.get("mcp_servers") or {}
    mcp_tools: set[str] = set()
    for server in mcp_statuses:
        server_name = server["name"].replace("-", "_")
        for tool_name in server.get("tools", {}):
            mcp_tools.add(f"mcp__{server_name}__{tool_name}")

    for component in expected:
        kind = component["kind"]
        external = component.get("external_identifier")
        record: dict[str, Any] = {
            "component_id": component["component_id"],
            "kind": kind,
            "registered": True,
            "loaded": None,
            "enabled": None,
            "authenticated": None,
            "callable": None,
            "successful_execution": component["status"]["execution"] == "verified-live",
            "evidence": [],
        }
        if kind == "skill":
            expected_paths = _component_skill_paths(component, codex_home)
            for index, skill in enumerate(loaded_skills):
                if _normalise_path(skill["path"]) in expected_paths:
                    matched_skills.add(index)
                    record.update(
                        loaded=True,
                        enabled=bool(skill["enabled"]),
                        callable=bool(skill["enabled"]),
                        evidence=["skills/list"],
                    )
                    break
            else:
                record.update(loaded=False, enabled=False, callable=False)
        elif kind == "hook" and external:
            wanted = _normalise_hook_event(external)
            target = component["projection"]["target"]
            for index, hook in enumerate(loaded_hooks):
                source_matches = not target or Path(hook["sourcePath"]).name == Path(target).name
                if (
                    index not in matched_hooks
                    and _normalise_hook_event(hook["eventName"]) == wanted
                    and source_matches
                ):
                    matched_hooks.add(index)
                    trusted = hook["trustStatus"] in {"trusted", "managed"}
                    record.update(
                        loaded=True,
                        enabled=bool(hook["enabled"]),
                        callable=bool(hook["enabled"] and trusted),
                        evidence=["hooks/list", f"trust:{hook['trustStatus']}"],
                    )
                    break
            else:
                record.update(loaded=False, enabled=False, callable=False)
        elif kind == "mcp" and external:
            server = mcp_by_name.get(external)
            if server is None:
                record.update(loaded=False, enabled=False, authenticated=False, callable=False)
            else:
                matched_mcps.add(external)
                configured = configured_mcp.get(external, {})
                enabled = configured.get("enabled", bool(server.get("tools")))
                auth = server["authStatus"] != "notLoggedIn"
                callable_now = enabled and auth and bool(server.get("tools"))
                record.update(
                    loaded=True,
                    enabled=enabled,
                    authenticated=auth,
                    callable=callable_now,
                    evidence=["mcpServerStatus/list", f"auth:{server['authStatus']}"],
                )
        elif kind == "app" and external:
            app = app_by_id.get(external)
            if app is None:
                record.update(loaded=False, enabled=False, callable=False)
            else:
                matched_apps.add(external)
                record.update(
                    loaded=True,
                    enabled=bool(app["enabled"]),
                    authenticated=bool(app["callable"]),
                    callable=bool(app["callable"]),
                    evidence=["app/installed"],
                )
        elif kind == "plugin":
            plugin = plugin_by_id.get(external)
            if plugin is not None:
                matched_plugins.add(external)
                enabled = bool(plugin.get("enabled"))
                record.update(
                    loaded=True,
                    enabled=enabled,
                    callable=None,
                    evidence=["plugin/installed"],
                )
            else:
                source = _normalise_path(component["source"]["locator"])
                found = any(
                    _normalise_path(skill["path"]).startswith(source + os.sep)
                    for skill in loaded_skills
                )
                record.update(
                    loaded=True if found else None,
                    enabled=True if found else None,
                    callable=None,
                    evidence=["skills/list:plugin-path"] if found else [],
                )
        elif kind == "tool" and external and external.startswith("mcp__"):
            found = external in mcp_tools
            record.update(
                loaded=found,
                enabled=found,
                callable=found,
                evidence=["mcpServerStatus/list:tool"] if found else [],
            )
        elif kind == "adapter":
            found = any(
                Path(path).name in {"AGENTS.md", "AGENTS.override.md"}
                for path in instruction_sources
            )
            record.update(
                loaded=found,
                enabled=found,
                callable=found,
                evidence=["thread/start:instructionSources"] if found else [],
            )
        else:
            record["evidence"] = ["not-observable-through-current-app-server"]
        records.append(record)

    unmanaged = {
        "skills": [
            {"name": skill["name"], "path": skill["path"], "enabled": skill["enabled"]}
            for index, skill in enumerate(loaded_skills)
            if index not in matched_skills and skill["enabled"]
        ],
        "hooks": [
            {
                "key": hook["key"],
                "event": hook["eventName"],
                "source_path": hook["sourcePath"],
            }
            for index, hook in enumerate(loaded_hooks)
            if index not in matched_hooks and hook["enabled"]
        ],
        "mcp_servers": [
            item["name"]
            for item in mcp_statuses
            if item["name"] not in matched_mcps
            and item["name"]
            not in {
                component.get("external_identifier")
                for component in registry["components"]
                if component["kind"] == "mcp"
            }
        ],
        "apps": [item["id"] for item in apps if item["id"] not in matched_apps],
        "plugins": [item["id"] for item in installed_plugins if item["id"] not in matched_plugins],
    }
    registered_skill_paths = {
        path
        for component in registry["components"]
        if component["kind"] == "skill" and "codex" in component["platforms"]
        for path in _component_skill_paths(component, codex_home)
    }
    unmanaged["skills"] = [
        item
        for item in unmanaged["skills"]
        if _normalise_path(item["path"]) not in registered_skill_paths
    ]
    inactive_discovered = sorted(
        component["component_id"]
        for component in registry["components"]
        if "codex" in component["platforms"]
        and not component["status"]["active"]
        and (
            (component["kind"] == "mcp" and component.get("external_identifier") in mcp_by_name)
            or (
                component["kind"] == "plugin"
                and component.get("external_identifier") in plugin_by_id
            )
        )
    )
    missing = sorted(
        record["component_id"]
        for record in records
        if record["kind"] in {"adapter", "app", "hook", "mcp", "skill"}
        and (record["loaded"] is False or record["enabled"] is False)
    )
    observable = [record for record in records if record["loaded"] is not None]
    return {
        "record_type": "codex-live-component-reconciliation",
        "system_version": registry["system_version"],
        "observed_at": utc_now(),
        "cwd": str(cwd),
        "summary": {
            "registered_active": len(expected_by_id),
            "observed_by_runtime": len(observable),
            "loaded": sum(record["loaded"] is True for record in observable),
            "callable": sum(record["callable"] is True for record in observable),
            "successful_execution": sum(record["successful_execution"] for record in records),
            "not_observable_by_current_api": len(records) - len(observable),
        },
        "clean": not missing and not any(unmanaged.values()),
        "missing": missing,
        "unmanaged": unmanaged,
        "inactive_discovered": inactive_discovered,
        "instruction_sources": sorted(instruction_sources),
        "components": records,
        "claim_boundary": (
            "Loaded or callable does not prove a component completed real work. "
            "Only successful_execution uses real execution evidence."
        ),
    }


def render_codex_inspection(report: dict[str, Any]) -> str:
    """Render the four evidence levels in ordinary, compact language."""
    live = report["reconciliation"]
    summary = live["summary"]
    unmanaged_count = sum(len(items) for items in live["unmanaged"].values())
    lines = [
        "# Codex Live Check",
        "",
        f"- Codex version: `{report['compatibility']['codex_version']}`",
        f"- Fractal-managed active set: `{summary['registered_active']}`",
        f"- Visible through current Codex runtime APIs: `{summary['observed_by_runtime']}`",
        f"- Loaded now: `{summary['loaded']}`",
        f"- Callable now: `{summary['callable']}`",
        f"- Proven by a successful real execution: `{summary['successful_execution']}`",
        "- Not visible through a current runtime API: "
        f"`{summary['not_observable_by_current_api']}`",
        f"- Unmanaged live items found: `{unmanaged_count}`",
        f"- Registered live items missing: `{len(live['missing'])}`",
        "",
        "Being registered, loaded, callable, and proven successful are four different things. "
        "This check does not merge those claims.",
        "",
        f"AGENTS hierarchy matches the live Codex thread: "
        f"`{report['agents_hierarchy']['sources_match']}`",
        f"External material waiting for review: `{len(report['legacy_review']['items'])}`",
    ]
    if unmanaged_count:
        lines.extend(
            ["", "Unmanaged items are reported for review; nothing was deleted or imported."]
        )
    return "\n".join(lines) + "\n"


def resolve_agents_hierarchy(
    *,
    cwd: Path,
    codex_home: Path,
    fallback_filenames: tuple[str, ...] = (),
    maximum_bytes: int = 32 * 1024,
) -> dict[str, Any]:
    """Resolve documented AGENTS precedence and the aggregate byte limit."""
    cwd = cwd.resolve()
    codex_home = codex_home.resolve()
    selected: list[Path] = []
    override = codex_home / "AGENTS.override.md"
    global_file = (
        override if override.is_file() and override.stat().st_size else codex_home / "AGENTS.md"
    )
    if global_file.is_file() and global_file.stat().st_size:
        selected.append(global_file)
    project_root: Path | None = None
    for directory in (cwd, *cwd.parents):
        if (directory / ".git").exists():
            project_root = directory
            break
    if project_root is not None:
        directories = [project_root]
        relative = cwd.relative_to(project_root)
        current = project_root
        for part in relative.parts:
            current = current / part
            directories.append(current)
        names = ("AGENTS.override.md", "AGENTS.md", *fallback_filenames)
        for directory in directories:
            candidate = next(
                (
                    directory / name
                    for name in names
                    if (directory / name).is_file() and (directory / name).stat().st_size
                ),
                None,
            )
            if candidate is not None and candidate not in selected:
                selected.append(candidate)
    used: list[dict[str, Any]] = []
    remaining = maximum_bytes
    truncated = False
    for path in selected:
        size = path.stat().st_size
        included = min(size, remaining)
        used.append({"path": str(path), "size_bytes": size, "included_bytes": included})
        remaining -= included
        if included < size:
            truncated = True
            break
        if remaining == 0:
            truncated = len(used) < len(selected)
            break
    return {
        "record_type": "codex-agents-hierarchy-audit",
        "cwd": str(cwd),
        "maximum_bytes": maximum_bytes,
        "selected_sources": used,
        "truncated": truncated,
        "precedence": "later project sources override earlier global sources",
    }


def audit_agents_hierarchy(
    client: CodexAppServerClient,
    *,
    cwd: Path,
    codex_home: Path,
    maximum_bytes: int = 32 * 1024,
) -> dict[str, Any]:
    expected = resolve_agents_hierarchy(
        cwd=cwd,
        codex_home=codex_home,
        maximum_bytes=maximum_bytes,
    )
    response = client.request(
        "thread/start",
        {
            "cwd": str(cwd),
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        },
    )
    actual = [_normalise_path(path) for path in response.get("instructionSources", [])]
    expected_paths = [_normalise_path(item["path"]) for item in expected["selected_sources"]]
    expected.update(
        actual_instruction_sources=actual,
        sources_match=expected_paths == actual,
        live_evidence="thread/start:instructionSources",
    )
    return expected


def detect_legacy_review_inputs(
    client: CodexAppServerClient,
    *,
    cwds: list[Path],
) -> dict[str, Any]:
    """Detect external material and route it to review without importing it."""
    response = client.request(
        "externalAgentConfig/detect",
        {"cwds": [str(path) for path in cwds], "includeHome": True},
    )
    return {
        "record_type": "legacy-material-review-input",
        "observed_at": utc_now(),
        "items": [
            {
                "item_type": item["itemType"],
                "description": item["description"],
                "cwd": item.get("cwd"),
                "details": _summarise_migration_details(item.get("details")),
                "route": "legacy-material-review",
                "naming_required_if_adopted": True,
                "automatic_action": "none",
            }
            for item in response.get("items", [])
        ],
        "import_called": False,
        "removal_called": False,
    }


def _summarise_migration_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep review counts and identifiers, not session paths, titles, or content."""
    if details is None:
        return None
    summary: dict[str, Any] = {}
    named_collections = {
        "commands": "name",
        "hooks": "name",
        "mcpServers": "name",
        "skills": "name",
        "subagents": "name",
    }
    for key, name_key in named_collections.items():
        values = details.get(key) or []
        summary[key] = {
            "count": len(values),
            "names": sorted(
                value[name_key] for value in values if isinstance(value.get(name_key), str)
            ),
        }
    plugins = details.get("plugins") or []
    summary["plugins"] = {
        "count": sum(len(item.get("pluginNames", [])) for item in plugins),
        "names": sorted(
            f"{item['marketplaceName']}:{name}"
            for item in plugins
            for name in item.get("pluginNames", [])
        ),
    }
    summary["memory_count"] = len(details.get("memory") or [])
    summary["session_count"] = len(details.get("sessions") or [])
    return summary


def watch_codex_drift(
    client: CodexAppServerClient,
    *,
    paths: list[Path],
    timeout: float,
) -> dict[str, Any]:
    """Watch selected roots and route changes to review without changing them."""
    watches = []
    for path in paths:
        watch_id = f"fractal-{uuid.uuid4().hex}"
        response = client.request("fs/watch", {"path": str(path), "watchId": watch_id})
        watches.append({"watch_id": watch_id, "path": response["path"]})
    try:
        message = client.wait_for_notification({"fs/changed", "skills/changed"}, timeout=timeout)
        return {
            "record_type": "codex-live-drift-event",
            "observed_at": utc_now(),
            "event": message,
            "route": "legacy-material-review",
            "naming_required_if_adopted": True,
            "automatic_import": False,
            "automatic_removal": False,
        }
    finally:
        for watch in watches:
            with suppress(CodexAppServerError):
                client.request("fs/unwatch", {"watchId": watch["watch_id"]})


def _get_key_path(value: dict[str, Any], key_path: str) -> Any:
    current: Any = value
    for part in _split_key_path(key_path):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _split_key_path(key_path: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in key_path:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "." and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if quoted or escaped:
        raise ValueError("Invalid quoted config key path")
    parts.append("".join(current))
    return parts


def _user_layer_version(response: dict[str, Any]) -> str | None:
    for layer in response.get("layers") or []:
        name = layer.get("name", {})
        if isinstance(name, dict) and name.get("type") == "user":
            return layer.get("version")
    return None


def audit_codex_config_projection(
    client: CodexAppServerClient,
    registry: dict[str, Any],
    *,
    cwd: Path,
    user_surface: dict[str, Any] | None = None,
    visible_skill_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check governed MCP activation and Skill hiding without exposing secrets."""
    response = client.request("config/read", {"cwd": str(cwd), "includeLayers": True})
    requirements = client.request("configRequirements/read", {})
    configured = response["config"].get("mcp_servers") or {}
    desired: dict[str, bool] = {}
    for component in registry["components"]:
        locator = component["source"]["locator"]
        if (
            component["kind"] == "mcp"
            and "codex" in component["platforms"]
            and locator.startswith("~/.codex/config.toml#mcp_servers.")
        ):
            desired[locator.rsplit(".", 1)[-1]] = component["status"]["active"]
    actual = {name: bool(configured.get(name, {}).get("enabled", True)) for name in desired}
    mismatched = sorted(name for name in desired if actual[name] != desired[name])
    desired_disabled_skill_paths: list[str] = []
    actual_disabled_skill_paths: list[str] = []
    skill_surface_path_audit: dict[str, Any] | None = None
    if user_surface is not None:
        visible = {item["entry_id"] for item in user_surface["entries"]}
        listed_skills, _ = load_codex_skill_catalog(
            client,
            cwd=cwd,
            force_reload=True,
        )
        desired_disabled_skill_paths = sorted(
            str(item["path"])
            for item in listed_skills
            if (
                str(item["path"]) not in set(visible_skill_paths.values())
                if visible_skill_paths is not None
                else item["name"] not in visible
            )
        )
        skill_config = response["config"].get("skills") or {}
        entries = skill_config.get("config") or []
        actual_disabled_skill_paths = sorted(
            str(item["path"])
            for item in entries
            if item.get("enabled") is False and item.get("path")
        )
        missing_paths = sorted(
            set(desired_disabled_skill_paths).difference(actual_disabled_skill_paths)
        )
        mismatched.extend(f"skills.config:{path}" for path in missing_paths)
        if visible_skill_paths is not None:
            skill_surface_path_audit = audit_codex_skill_path_surface(
                user_surface,
                listed_skills,
                visible_skill_paths=visible_skill_paths,
                require_visible_paths=False,
            )
            mismatched.extend(
                f"skills.enabled-unexpected:{path}"
                for path in skill_surface_path_audit["unexpected_enabled_skill_paths"]
            )
            mismatched.extend(
                f"skills.visible-disabled:{path}"
                for path in skill_surface_path_audit["disabled_visible_skill_paths"]
            )
        mismatched.sort()
    return {
        "record_type": "codex-config-projection-audit",
        "clean": not mismatched,
        "desired_mcp_activation": desired,
        "actual_mcp_activation": actual,
        "desired_disabled_skill_paths": desired_disabled_skill_paths,
        "actual_disabled_skill_paths": actual_disabled_skill_paths,
        "skill_surface_path_audit": skill_surface_path_audit,
        "mismatched": mismatched,
        "requirements_present": requirements.get("requirements") is not None,
        "config_version": _user_layer_version(response),
        "secret_values_included": False,
    }


def apply_codex_config_transaction(
    client: CodexAppServerClient,
    *,
    edits: list[dict[str, Any]],
    recovery_path: Path,
    cwd: Path,
) -> dict[str, Any]:
    """Read, constraint-check, batch-write, re-read, and retain a private restore record."""
    before = client.request("config/read", {"cwd": str(cwd), "includeLayers": True})
    requirements = client.request("configRequirements/read", {})
    expected_version = _user_layer_version(before)
    before_values = {
        edit["keyPath"]: _get_key_path(before["config"], edit["keyPath"]) for edit in edits
    }
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    recovery = {
        "record_type": "codex-config-restore",
        "created_at": utc_now(),
        "expected_version": expected_version,
        "before_values": before_values,
        "requirements": requirements,
    }
    recovery_path.write_text(
        json.dumps(recovery, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    recovery_path.chmod(0o600)
    written = client.request(
        "config/batchWrite",
        {
            "edits": edits,
            "expectedVersion": expected_version,
            "reloadUserConfig": True,
        },
    )
    after = client.request("config/read", {"cwd": str(cwd), "includeLayers": True})
    verified = all(
        _get_key_path(after["config"], edit["keyPath"]) == edit["value"] for edit in edits
    )
    if not verified:
        restore_edits = [
            {"keyPath": key, "value": value, "mergeStrategy": "replace"}
            for key, value in before_values.items()
        ]
        client.request(
            "config/batchWrite",
            {
                "edits": restore_edits,
                "expectedVersion": written["version"],
                "reloadUserConfig": True,
            },
        )
        raise CodexAppServerError("Config read-back did not match; the before state was restored")
    before_hash = hashlib.sha256(
        json.dumps(before["config"], sort_keys=True).encode("utf-8")
    ).hexdigest()
    after_hash = hashlib.sha256(
        json.dumps(after["config"], sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "record_type": "codex-config-transaction-evidence",
        "status": "verified",
        "changed_key_paths": [edit["keyPath"] for edit in edits],
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "written_version": written["version"],
        "recovery_path": str(recovery_path),
        "secret_values_recorded_in_evidence": False,
    }


def trust_registered_codex_hooks(
    client: CodexAppServerClient,
    registry: dict[str, Any],
    *,
    cwd: Path,
    codex_home: Path,
    recovery_path: Path,
) -> dict[str, Any]:
    """Trust only the exact current hashes of registered generated Codex Hooks."""
    expected_events = {
        _normalise_hook_event(component["external_identifier"])
        for component in active_components(registry, "codex")
        if component["kind"] == "hook" and component.get("external_identifier")
    }
    response = client.request("hooks/list", {"cwds": [str(cwd)]})
    hooks = [hook for entry in response.get("data", []) for hook in entry.get("hooks", [])]
    source_path = _normalise_path(codex_home / "hooks.json")
    selected = [
        hook
        for hook in hooks
        if _normalise_path(hook["sourcePath"]) == source_path
        and _normalise_hook_event(hook["eventName"]) in expected_events
    ]
    selected_events = {_normalise_hook_event(hook["eventName"]) for hook in selected}
    if selected_events != expected_events or len(selected) != len(expected_events):
        raise CodexAppServerError("Live Hooks do not match the registered Codex Hook set")
    edits = []
    for hook in selected:
        escaped_key = hook["key"].replace("\\", "\\\\").replace('"', '\\"')
        edits.append(
            {
                "keyPath": f'hooks.state."{escaped_key}".trusted_hash',
                "value": hook["currentHash"],
                "mergeStrategy": "replace",
            }
        )
    transaction = apply_codex_config_transaction(
        client,
        edits=edits,
        recovery_path=recovery_path,
        cwd=cwd,
    )
    verified_response = client.request("hooks/list", {"cwds": [str(cwd)]})
    verified_hooks = [
        hook
        for entry in verified_response.get("data", [])
        for hook in entry.get("hooks", [])
        if _normalise_path(hook["sourcePath"]) == source_path
        and _normalise_hook_event(hook["eventName"]) in expected_events
    ]
    untrusted = sorted(
        hook["key"] for hook in verified_hooks if hook["trustStatus"] not in {"trusted", "managed"}
    )
    if untrusted:
        raise CodexAppServerError("Hook trust read-back failed for: " + ", ".join(untrusted))
    return {
        "record_type": "codex-hook-trust-evidence",
        "status": "verified",
        "hook_count": len(verified_hooks),
        "hook_events": sorted(
            {_canonical_hook_event_label(hook["eventName"]) for hook in verified_hooks}
        ),
        "trusted_hashes": sorted(hook["currentHash"] for hook in verified_hooks),
        "transaction": transaction,
        "persistent_system_version_activated": False,
    }


def _tool_evidence(item: dict[str, Any]) -> str | None:
    item_type = item.get("type")
    if item_type == "commandExecution":
        return f"commandExecution:{item.get('status')}:{item.get('exitCode')}"
    if item_type == "mcpToolCall":
        return f"mcp:{item.get('server')}:{item.get('tool')}:{item.get('status')}"
    if item_type == "dynamicToolCall":
        return f"dynamic:{item.get('namespace')}:{item.get('tool')}:{item.get('status')}"
    if item_type == "fileChange":
        return f"fileChange:{item.get('status')}"
    return None


def _finalize_work_signature_evaluation(
    evaluations_path: Path,
    evaluation: dict[str, Any],
) -> None:
    """Keep one evaluation per work item while adding final event evidence."""
    evaluations_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = evaluations_path.with_suffix(evaluations_path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            records = []
            if evaluations_path.exists():
                records = [
                    json.loads(line)
                    for line in evaluations_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            retained = [item for item in records if item.get("work_id") != evaluation["work_id"]]
            retained.append(evaluation)
            temporary = evaluations_path.with_name(
                f".{evaluations_path.name}.{uuid.uuid4().hex}.tmp"
            )
            temporary.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in retained
                ),
                encoding="utf-8",
            )
            temporary.replace(evaluations_path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def verify_live_turn_completion(
    client: CodexAppServerClient,
    *,
    cwd: Path,
    project_id: str,
    journal_path: Path,
    evaluations_path: Path,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run one real, read-only turn and finalize its Work Signature from events."""
    thread_response = client.request(
        "thread/start",
        {
            "cwd": str(cwd),
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        },
    )
    thread_id = thread_response["thread"]["id"]
    started = time.monotonic()
    store = WorkSignatureStore(journal_path)
    prior_work_ids = {item["work_id"] for item in store.read_all()}
    turn_response = client.request(
        "turn/start",
        {
            "threadId": thread_id,
            "input": [
                {
                    "type": "text",
                    "text": "Reply exactly FRACTAL_TURN_OK. Do not call tools.",
                }
            ],
        },
    )
    turn_id = turn_response["turn"]["id"]
    work_id = f"codex-turn-{thread_id}-{turn_id}"
    tokens: int | None = None
    tool_records: list[str] = []
    turn: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while turn is None:
        message = client.wait_for_notification(
            {"thread/tokenUsage/updated", "item/completed", "turn/completed"},
            timeout=max(0.0, deadline - time.monotonic()),
        )
        params = message.get("params", {})
        if params.get("threadId") != thread_id or params.get("turnId", turn_id) != turn_id:
            continue
        if message["method"] == "thread/tokenUsage/updated":
            tokens = params["tokenUsage"]["last"]["totalTokens"]
        elif message["method"] == "item/completed":
            compact = _tool_evidence(params["item"])
            if compact:
                tool_records.append(compact)
        else:
            turn = params["turn"]
    elapsed = time.monotonic() - started
    status = turn.get("status", "unknown")
    outcome = {
        "completed": "completed",
        "interrupted": "interrupted",
        "failed": "failed",
    }.get(status, status)
    hook_deadline = time.monotonic() + 5
    captured: dict[str, Any] | None = None
    while captured is None and time.monotonic() < hook_deadline:
        captured = next(
            (
                item
                for item in store.read_all()
                if item["work_id"] not in prior_work_ids
                and item.get("thread_id") == thread_id
                and item.get("turn_id") == turn_id
            ),
            None,
        )
        if captured is None:
            time.sleep(0.05)
    if captured is None:
        raise CodexAppServerError("The live Stop Hook did not capture this thread_id and turn_id")
    lightweight = WorkSignature(
        work_id=captured["work_id"],
        project_id=captured["project_id"],
        work_type=captured["work_type"],
        input_shape=captured["input_shape"],
        steps=tuple(captured["steps"]),
        tools=tuple(captured["tools"]),
        outcome_category=captured["outcome_category"],
        purpose_class="verification",
        elapsed_seconds=captured["elapsed_seconds"],
        token_usage=captured["token_usage"],
        completed_at=captured["completed_at"],
        thread_id=captured.get("thread_id"),
        turn_id=captured.get("turn_id"),
        tool_evidence=tuple(captured.get("tool_evidence", ())),
        evidence_state=captured.get("evidence_state", "stop-captured"),
    )
    work_id = lightweight.work_id
    final = replace(
        lightweight,
        tools=tuple(sorted({item.split(":", 1)[0] for item in tool_records})),
        outcome_category=outcome,
        elapsed_seconds=elapsed,
        token_usage=tokens,
        completed_at=utc_now(),
        tool_evidence=tuple(tool_records),
        evidence_state="turn-completed",
    )
    store.enrich_completion(final)
    history = [
        WorkSignature(
            work_id=item["work_id"],
            project_id=item["project_id"],
            work_type=item["work_type"],
            input_shape=item["input_shape"],
            steps=tuple(item["steps"]),
            tools=tuple(item["tools"]),
            outcome_category=item["outcome_category"],
            purpose_class=item["purpose_class"],
            elapsed_seconds=item["elapsed_seconds"],
            token_usage=item["token_usage"],
            completed_at=item["completed_at"],
            thread_id=item.get("thread_id"),
            turn_id=item.get("turn_id"),
            tool_evidence=tuple(item.get("tool_evidence", ())),
            evidence_state=item.get("evidence_state", "stop-captured"),
        )
        for item in store.read_all()
        if item["work_id"] != work_id
    ]
    recognition = recognise_repetition(history, final)
    evaluation = {
        "record_type": "work-signature-evaluation",
        "work_id": work_id,
        "project_id": project_id,
        "platform": "codex",
        "captured": True,
        "event_evidence": [
            "turn/completed",
            "thread/tokenUsage/updated" if tokens is not None else "token-usage-unavailable",
            "item/completed",
        ],
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
    _finalize_work_signature_evaluation(evaluations_path, evaluation)
    return {
        "record_type": "codex-live-turn-verification",
        "thread_id": thread_id,
        "turn_id": turn_id,
        "status": status,
        "work_signature": final.to_dict(),
        "fatigue_evaluation": evaluation["recognition"],
        "persistent_system_activation": False,
        "live_stop_hook_captured": True,
    }
