"""Command-line entrypoint for Fractal."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from fractal import SYSTEM_VERSION
from fractal.codex_app_server import (
    CodexAppServerClient,
    apply_codex_config_transaction,
    audit_agents_hierarchy,
    detect_codex_compatibility,
    detect_legacy_review_inputs,
    load_codex_skill_catalog,
    reconcile_codex_components,
    render_codex_inspection,
    trust_registered_codex_hooks,
    verify_live_turn_completion,
    watch_codex_drift,
)
from fractal.component_governance import (
    audit_component_drift,
    load_component_registry,
    render_component_status,
)
from fractal.component_installation import (
    ClaudeComponentInstaller,
    CodexComponentInstaller,
    GeminiComponentInstaller,
)
from fractal.component_inventory import (
    build_component_registry,
    observe_platform_components,
)
from fractal.context import RetrievalRequest, assemble_context_package, rebuild_context_index
from fractal.live_state import LiveRuntimeStateStore
from fractal.models import ProjectRecord
from fractal.storage import ProjectStore, value_sha256
from fractal.user_surface import (
    audit_codex_skill_path_surface,
    audit_codex_skill_surface,
    build_codex_skill_config_edits,
    build_user_surface,
    load_user_surface,
)
from fractal.versioning import PublicationExecutor, VersionStore
from fractal.views import render_project_summary
from fractal.workplace import (
    WorkplaceError,
    ensure_workplace,
    load_workplace,
    resolve_workplace_root,
)
from fractal.workplace_status import build_workplace_status, render_workplace_status


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="fractal",
        description="Operate a Fractal continuous-improvement workspace.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    version_parser = subparsers.add_parser(
        "version", help="Show the active version or execute its governed publication route."
    )
    version_actions = version_parser.add_subparsers(dest="version_action")
    version_publish = version_actions.add_parser(
        "publish", help="Publish one exact activated commit through the governed executor."
    )
    version_publish.add_argument("--runtime-root", required=True, type=Path)
    version_publish.add_argument("--repository-root", required=True, type=Path)
    version_publish.add_argument("--order", required=True, type=Path)
    version_publish.add_argument("--order-sha256", required=True)
    version_publish.add_argument("--fresh-session-receipt-id", required=True)
    version_publish.add_argument("--runtime-route-receipt-id", required=True)
    version_publish.add_argument("--authority-receipt-id", required=True)
    version_publish.add_argument("--project-id", required=True)
    version_publish.add_argument("--project-revision", required=True, type=int)
    version_publish.add_argument("--bypass-audit", type=Path)
    version_publish.add_argument(
        "--reconcile",
        action="store_true",
        help="Inspect remote and close an indeterminate exact acknowledgement; never push.",
    )
    workplace_parser = subparsers.add_parser(
        "workplace", help="Ensure, validate, or migrate the durable Workplace."
    )
    workplace_actions = workplace_parser.add_subparsers(
        dest="workplace_action", required=True
    )
    workplace_ensure = workplace_actions.add_parser(
        "ensure", help="Load, safely migrate, or create a neutral Workplace."
    )
    _add_workplace_root_arguments(workplace_ensure)
    workplace_validate = workplace_actions.add_parser(
        "validate", help="Validate the canonical Workplace without changing it."
    )
    _add_workplace_root_arguments(workplace_validate)
    workplace_migrate = workplace_actions.add_parser(
        "migrate", help="Explicitly migrate the complete legacy Workplace tree."
    )
    _add_workplace_root_arguments(workplace_migrate)
    workplace_migrate.add_argument(
        "--active-pointer", required=True, type=Path,
        help="Verified active System Version pointer JSON.",
    )
    workplace_migrate.add_argument(
        "--live-state", required=True, type=Path,
        help="Verified live runtime state JSON.",
    )
    workplace_migrate.add_argument(
        "--runtime-root", required=True, type=Path,
        help="Explicit external runtime root used for migration.",
    )
    workplace_migrate.add_argument(
        "--event-root", required=True, type=Path,
        help="Explicit event-journal root used for migration.",
    )
    workplace_migrate.add_argument(
        "--context-root", action="append", default=[], metavar="NAME=PATH",
        help="Explicit context source mapping; repeat for each external root.",
    )

    status_parser = subparsers.add_parser(
        "status", help="Show the current Workplace and Perspective status."
    )
    _add_workplace_root_arguments(status_parser, positional_name="status_root")
    status_parser.add_argument(
        "--details", action="store_true", help="Include evidence and component details."
    )
    status_parser.add_argument(
        "--json", action="store_true", help="Render the complete status model as JSON."
    )
    status_parser.add_argument(
        "--public-system",
        "--public-version",
        dest="public_system",
        type=str,
        help="Optional canonical public System record or version identity.",
    )
    status_parser.add_argument(
        "--active-system",
        "--active-system-path",
        dest="active_system",
        type=Path,
        help="Optional canonical active System record or pointer.",
    )
    status_parser.add_argument(
        "--candidate-system",
        "--candidate-system-path",
        dest="candidate_system",
        type=Path,
        help="Optional canonical candidate System record or pointer.",
    )
    status_parser.add_argument(
        "--projects",
        "--project-root",
        dest="projects",
        type=Path,
        help="Optional canonical Project record or directory.",
    )
    status_parser.add_argument(
        "--live-state",
        "--runtime-state",
        "--runtime-root",
        dest="live_state",
        type=Path,
        help="Optional rebuildable live runtime state record.",
    )
    status_parser.add_argument(
        "--components",
        "--component-registry",
        dest="components",
        type=Path,
        help="Optional Workplace component registry.",
    )
    status_parser.add_argument(
        "--decisions",
        "--decision-records",
        dest="decisions",
        type=Path,
        help="Optional Workplace decision records.",
    )
    live_state_parser = subparsers.add_parser(
        "live-state", help="Rebuild or verify the mutable runtime read model."
    )
    live_state_actions = live_state_parser.add_subparsers(dest="live_state_action", required=True)
    live_state_reconcile = live_state_actions.add_parser(
        "reconcile", help="Rebuild live state from canonical sources."
    )
    live_state_reconcile.add_argument("--state", required=True, type=Path)
    live_state_reconcile.add_argument("--project-record", required=True, type=Path)
    live_state_reconcile.add_argument("--active-pointer", required=True, type=Path)
    live_state_show = live_state_actions.add_parser(
        "show", help="Verify and show the current live state."
    )
    live_state_show.add_argument("--state", required=True, type=Path)
    project_parser = subparsers.add_parser("project", help="Record and inspect Projects.")
    project_actions = project_parser.add_subparsers(dest="project_action", required=True)

    create_parser = project_actions.add_parser("create", help="Create a canonical Project.")
    _add_storage_arguments(create_parser)
    create_parser.add_argument("--project-id", required=True)
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--system-version", default=SYSTEM_VERSION)
    create_parser.add_argument("--actor", default="main-agent")
    create_parser.add_argument("--platform", required=True)

    show_parser = project_actions.add_parser("show", help="Show a Human Control summary.")
    _add_storage_arguments(show_parser)
    show_parser.add_argument("--project-id", required=True)
    show_parser.add_argument("--details", action="store_true")

    verify_parser = project_actions.add_parser("verify", help="Verify state and event integrity.")
    _add_storage_arguments(verify_parser)
    verify_parser.add_argument("--project-id", required=True)

    migrate_parser = project_actions.add_parser("migrate", help="Migrate canonical schema.")
    _add_storage_arguments(migrate_parser)
    migrate_parser.add_argument("--project-id", required=True)
    migrate_parser.add_argument("--actor", default="main-agent")
    migrate_parser.add_argument("--platform", required=True)

    context_parser = subparsers.add_parser("context", help="Build and query bounded context.")
    context_actions = context_parser.add_subparsers(dest="context_action", required=True)
    rebuild_parser = context_actions.add_parser("rebuild", help="Rebuild the local context index.")
    rebuild_parser.add_argument("--catalogue", required=True, type=Path)
    rebuild_parser.add_argument("--database", required=True, type=Path)
    rebuild_parser.add_argument("--maximum-file-bytes", type=int, default=2_000_000)
    rebuild_parser.add_argument(
        "--context-root",
        action="append",
        default=[],
        metavar="SCHEME=PATH",
        help=(
            "Explicit runtime mapping for a logical context URI; repeat for "
            "workplace=, system=, or local=."
        ),
    )

    search_parser = context_actions.add_parser("search", help="Build an auditable context package.")
    search_parser.add_argument("--database", required=True, type=Path)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--purpose", required=True)
    search_parser.add_argument("--requester", default="main-agent")
    search_parser.add_argument("--task-type", required=True)
    search_parser.add_argument("--project-id")
    search_parser.add_argument("--max-items", type=int, default=5)
    search_parser.add_argument("--allow-personalisation", action="store_true")
    search_parser.add_argument("--manifest", type=Path)

    components_parser = subparsers.add_parser(
        "components", help="Inspect the Fractal-managed component set."
    )
    component_actions = components_parser.add_subparsers(dest="component_action", required=True)
    component_show = component_actions.add_parser(
        "show", help="Show the Human Control component view."
    )
    component_show.add_argument(
        "--registry",
        type=Path,
        default=Path("~/.codex/fractal/component-registry.json"),
    )
    component_show.add_argument("--platform")
    component_show.add_argument(
        "--live-state",
        type=Path,
        default=Path("~/Library/Application Support/Fractal/runtime/live-state/current.json"),
    )
    component_audit = component_actions.add_parser(
        "audit", help="Compare observed components with the registered active set."
    )
    component_audit.add_argument("--registry", required=True, type=Path)
    component_audit.add_argument("--observed", required=True, type=Path)
    component_audit.add_argument("--platform", required=True)
    component_rebuild = component_actions.add_parser(
        "rebuild", help="Rebuild the registry from an explicit discovery policy."
    )
    component_rebuild.add_argument("--policy", required=True, type=Path)
    component_rebuild.add_argument("--output", required=True, type=Path)
    component_snapshot = component_actions.add_parser(
        "snapshot", help="Observe the registered live component surface."
    )
    component_snapshot.add_argument("--registry", required=True, type=Path)
    component_snapshot.add_argument("--platform", required=True)
    component_snapshot.add_argument("--home", required=True, type=Path)
    component_snapshot.add_argument("--tools", required=True, type=Path)
    component_snapshot.add_argument("--configured-mcp", action="append", default=[])
    component_snapshot.add_argument("--platform-surface", type=Path)
    component_snapshot.add_argument("--output", required=True, type=Path)
    component_surface = component_actions.add_parser(
        "surface-build",
        help="Compile the Action and Command allowlist over reusable internal Skill dots.",
    )
    component_surface.add_argument("--policy", required=True, type=Path)
    component_surface.add_argument("--registry", required=True, type=Path)
    component_surface.add_argument("--output", required=True, type=Path)
    component_install = component_actions.add_parser(
        "install-candidate", help="Install a verified platform candidate recoverably."
    )
    component_install.add_argument(
        "--platform", choices=("claude", "codex", "gemini"), default="codex"
    )
    component_install.add_argument("--built", required=True, type=Path)
    component_install.add_argument("--home", required=True, type=Path)
    component_install.add_argument("--state-root", required=True, type=Path)
    component_install.add_argument("--quarantine-root", required=True, type=Path)
    component_restore = component_actions.add_parser(
        "restore", help="Restore a previous platform component projection."
    )
    component_restore.add_argument("--install-id", required=True)
    component_restore.add_argument("--state-root", required=True, type=Path)
    component_restore.add_argument("--quarantine-root", required=True, type=Path)

    codex_parser = subparsers.add_parser(
        "codex", help="Inspect and safely manage the live Codex projection."
    )
    codex_actions = codex_parser.add_subparsers(dest="codex_action", required=True)
    codex_inspect = codex_actions.add_parser(
        "inspect", help="Compare Fractal with what Codex has loaded now."
    )
    _add_codex_runtime_arguments(codex_inspect)
    codex_inspect.add_argument("--output", type=Path)
    codex_watch = codex_actions.add_parser(
        "watch", help="Watch managed roots and report changes without importing them."
    )
    codex_watch.add_argument("--path", action="append", required=True, type=Path)
    codex_watch.add_argument("--timeout", type=float, default=30.0)
    codex_watch.add_argument("--output", type=Path)
    codex_config = codex_actions.add_parser(
        "config-apply", help="Apply an approved Codex config batch with read-back and restore."
    )
    codex_config.add_argument("--edits", required=True, type=Path)
    codex_config.add_argument("--recovery", required=True, type=Path)
    codex_config.add_argument("--cwd", type=Path, default=Path.cwd())
    codex_surface = codex_actions.add_parser(
        "surface-plan",
        help="Build a recoverable config batch that leaves only Actions and Commands enabled.",
    )
    _add_codex_runtime_arguments(codex_surface)
    codex_surface.add_argument("--surface", required=True, type=Path)
    codex_surface.add_argument("--candidate", required=True, type=Path)
    codex_surface.add_argument("--edits-output", required=True, type=Path)
    codex_surface.add_argument("--output", type=Path)
    codex_surface_audit = codex_actions.add_parser(
        "surface-audit",
        help="Fail unless the exact candidate Actions and Commands are the only enabled Skills.",
    )
    _add_codex_runtime_arguments(codex_surface_audit)
    codex_surface_audit.add_argument("--surface", required=True, type=Path)
    codex_surface_audit.add_argument("--candidate", required=True, type=Path)
    codex_surface_audit.add_argument("--output", type=Path)
    codex_verify = codex_actions.add_parser(
        "verify-turn", help="Run one read-only turn and capture real completion evidence."
    )
    codex_verify.add_argument("--cwd", type=Path, default=Path.cwd())
    codex_verify.add_argument("--project-id", required=True)
    codex_verify.add_argument("--journal", required=True, type=Path)
    codex_verify.add_argument("--evaluations", required=True, type=Path)
    codex_verify.add_argument("--output", type=Path)
    codex_trust = codex_actions.add_parser(
        "trust-hooks", help="Trust only the current registered generated Hook hashes."
    )
    _add_codex_runtime_arguments(codex_trust)
    codex_trust.add_argument("--runtime-root", required=True, type=Path)
    codex_trust.add_argument("--recovery", required=True, type=Path)
    codex_trust.add_argument("--output", type=Path)
    return parser


def _add_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)


def _parse_context_roots(values: Sequence[str]) -> dict[str, Path]:
    """Parse explicit ``NAME=PATH`` context mappings without path guessing."""

    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --context-root {value!r}; expected scheme=path")
        scheme, raw_path = value.split("=", 1)
        scheme = scheme.strip().lower().removesuffix("://")
        if scheme.endswith("_root"):
            scheme = scheme[:-5]
        raw_path = raw_path.strip()
        if not scheme or not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", scheme):
            raise ValueError(
                f"Invalid --context-root name {scheme!r}; use a lowercase logical name"
            )
        if not raw_path:
            raise ValueError(f"Missing path for --context-root {scheme}=...")
        if scheme in roots:
            raise ValueError(f"Duplicate --context-root mapping for {scheme}")
        roots[scheme] = Path(raw_path).expanduser()
    return roots


def _add_workplace_root_arguments(
    parser: argparse.ArgumentParser,
    *,
    positional_name: str = "workplace_root_argument",
) -> None:
    """Add a portable Workplace root without defaulting to a user path early.

    The positional spelling keeps the small ``fractal workplace ensure ROOT``
    command convenient, while the option aliases make scripts explicit and
    allow ``status`` to share the same path contract.  Resolution itself is
    deferred to :func:`resolve_workplace_root`, which honours
    ``FRACTAL_WORKPLACE``.
    """

    parser.add_argument(positional_name, nargs="?", type=Path)
    parser.add_argument(
        "--root",
        "--path",
        "--workplace",
        "--workplace-root",
        "--workspace-root",
        dest="workplace_root",
        type=Path,
        help="Explicit Workplace root (otherwise FRACTAL_WORKPLACE or the default is used).",
    )


def _add_codex_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "~/.codex")),
    )


def _write_optional_json(path: Path | None, value: dict) -> None:
    if path is None:
        return
    destination = path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _argument_workplace_root(args: argparse.Namespace, *, positional_name: str) -> Path:
    """Resolve a command's explicit/positional/env Workplace root."""

    explicit = getattr(args, "workplace_root", None)
    positional = getattr(args, positional_name, None)
    return resolve_workplace_root(explicit or positional)


def _first_existing_path(root: Path, *relative_paths: str) -> Path:
    """Choose the first known canonical location, retaining a useful fallback."""

    candidates = [root / relative for relative in relative_paths]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _verified_external_runtime_routes(
    root: Path,
    workplace: object | None,
) -> tuple[Path | None, Path | None, bool, list[str]]:
    """Discover the default current runtime from a verified Workplace route.

    A legacy Workplace version field is historical.  The one legacy field that
    remains useful for discovery is its storage-class declaration: the
    ``local-application-support`` class has one deterministic runtime root.

    A loaded canonical Workplace is already schema-verified.  When it has an
    explicit canonical active pointer but no committed runtime, the same local
    Application Support runtime is its rebuildable current-state source.  A
    neutral first-run Workplace has no active pointer and is intentionally not
    attached to unrelated machine-global state.  Cross-source digests and
    identities are verified by the status model before anything is called
    current.
    """

    legacy_path = root / "workspace.json"
    recognised = False
    discovery_issues: list[str] = []
    if legacy_path.is_file():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            legacy = None
            discovery_issues.append(
                "Current runtime routes could not be discovered safely; pass "
                "--active-system PATH and --live-state PATH"
            )
        runtime = legacy.get("runtime") if isinstance(legacy, dict) else None
        recognised = (
            isinstance(legacy, dict)
            and legacy.get("record_type") == "fractal-workspace"
            and legacy.get("record_version") == 1
            and isinstance(runtime, dict)
            and runtime.get("storage_class") == "local-application-support"
            and runtime.get("committed") is False
        )
    else:
        record = getattr(workplace, "record", None)
        runtime = record.get("runtime") if isinstance(record, dict) else None
        recognised = (
            isinstance(record, dict)
            and record.get("record_type") == "fractal-workplace"
            and record.get("record_version") == 1
            and isinstance(runtime, dict)
            and runtime.get("storage_class") == "local-ephemeral"
            and runtime.get("committed") is False
            and runtime.get("rebuildable") is True
            and (root / "system" / "active-version.json").is_file()
        )
    if not recognised:
        if legacy_path.is_file() and not discovery_issues:
            discovery_issues.append(
                "Current runtime routes could not be discovered safely; pass "
                "--active-system PATH and --live-state PATH"
            )
        return None, None, False, discovery_issues
    runtime_root = Path("~/Library/Application Support/Fractal/runtime").expanduser()
    active_path = runtime_root / "system-version" / "active.json"
    live_path = runtime_root / "live-state" / "current.json"
    return (
        active_path if active_path.is_file() else None,
        live_path if live_path.is_file() else None,
        True,
        discovery_issues,
    )


def _workplace_version_input(
    root: Path,
    workplace: object | None,
    kind: str,
) -> tuple[object | None, list[str]]:
    """Read a pointer target so candidate lifecycle state is not guessed.

    The canonical Workplace stores compact pointers while the status model
    accepts a version record.  Resolving the target here lets an activated or
    historical candidate disappear from the unresolved decision view, while a
    genuine candidate remains visible.  If a pointer target is broken, the
    pointer is still passed through so the renderer can show its evidence.
    """

    pointer_names = (
        ("active-version.json", "active.json")
        if kind == "active"
        else ("candidate-version.json", "candidate.json")
    )
    pointer_path = next(
        (root / "system" / name for name in pointer_names if (root / "system" / name).is_file()),
        root / "system" / pointer_names[0],
    )
    if not pointer_path.is_file():
        return None, []
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return pointer_path, []
    if not isinstance(pointer, dict):
        return pointer_path, []
    uri = pointer.get("record_uri")
    target: Path | None = None
    if isinstance(uri, str) and uri.startswith("workplace://"):
        relative = uri.removeprefix("workplace://").lstrip("/")
        if relative:
            if workplace is not None and hasattr(workplace, "resolve"):
                try:
                    target = workplace.resolve(uri)
                except (OSError, ValueError):
                    return pointer_path, [
                        f"Workplace {kind} System pointer is invalid: {pointer_path.resolve()}"
                    ]
            else:
                candidate = root / relative
                try:
                    candidate.resolve().relative_to(root.resolve())
                except ValueError:
                    return pointer_path, [
                        f"Workplace {kind} System pointer is invalid: {pointer_path.resolve()}"
                    ]
                target = candidate
    if target is None and isinstance(pointer.get("version"), str):
        target = root / "system" / "versions" / f"{pointer['version']}.json"
    if target is None or not target.is_file():
        missing = target or root / "system" / "versions"
        return pointer_path, [
            f"Workplace {kind} System record is missing: {missing.resolve()}"
        ]
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return pointer_path, [
            f"Workplace {kind} System record is invalid: {target.resolve()}"
        ]
    if not isinstance(record, dict):
        return pointer_path, [
            f"Workplace {kind} System record is not an object: {target.resolve()}"
        ]
    record = dict(record)
    record["source_path"] = str(target.resolve())
    return record, []


def _status_inputs(
    args: argparse.Namespace,
    root: Path,
    workplace: object | None,
) -> tuple[dict, list[str]]:
    """Resolve canonical status inputs while keeping all reads side-effect free."""

    setup_issues: list[str] = []
    runtime_active: Path | None = None
    runtime_live: Path | None = None
    legacy_root = workplace is None and (root / "workspace.json").is_file()
    (
        runtime_active,
        runtime_live,
        external_route_recognised,
        runtime_route_issues,
    ) = _verified_external_runtime_routes(root, workplace)
    setup_issues.extend(runtime_route_issues)
    explicit_public = getattr(args, "public_system", None)
    public_system: object = explicit_public or SYSTEM_VERSION
    if explicit_public is None:
        for relative in (
            "system/public-version.json",
            "system/public.json",
            "public-version.json",
            "public.json",
        ):
            candidate = root / relative
            if candidate.is_file():
                public_system = candidate
                break

    active = getattr(args, "active_system", None)
    candidate = getattr(args, "candidate_system", None)
    if active is None:
        if legacy_root and runtime_active is not None:
            active = runtime_active
        elif not legacy_root:
            active, active_issues = _workplace_version_input(root, workplace, "active")
            setup_issues.extend(active_issues)
            if active is None and runtime_active is not None:
                active = runtime_active
    # A candidate pointer below a legacy root is historical state.  Only a
    # caller-supplied candidate or a canonical Workplace pointer can be
    # represented as unresolved current work.
    if candidate is None and not legacy_root:
        candidate, candidate_issues = _workplace_version_input(root, workplace, "candidate")
        setup_issues.extend(candidate_issues)

    projects = getattr(args, "projects", None)
    if projects is None:
        projects_path = _first_existing_path(root, "projects", "project-records")
        projects = projects_path if projects_path.exists() else []
    live_state = getattr(args, "live_state", None)
    if live_state is not None and live_state.is_dir():
        live_state = live_state / "live-state" / "current.json"
    if live_state is None:
        live_state_path = _first_existing_path(
            root,
            ".runtime/live-state/current.json",
            ".runtime/current.json",
            "runtime/live-state/current.json",
            "runtime/current.json",
            "live-state/current.json",
        )
        live_state = live_state_path if live_state_path.is_file() else runtime_live
    if external_route_recognised and (active is None or live_state is None):
        missing_routes = []
        if active is None:
            missing_routes.append("active System pointer")
        if live_state is None:
            missing_routes.append("live runtime state")
        setup_issues.append(
            "Current runtime routes are incomplete; pass --active-system PATH and "
            "--live-state PATH (missing: " + ", ".join(missing_routes) + ")"
        )
    components = getattr(args, "components", None)
    if components is None:
        components_path = _first_existing_path(
            root,
            "system/components/registry.json",
            "components/registry.json",
        )
        components = components_path if components_path.is_file() else None
    decisions = getattr(args, "decisions", None)
    if decisions is None:
        decisions_path = _first_existing_path(root, "decisions", "system/decisions")
        decisions = decisions_path if decisions_path.exists() else None

    status = build_workplace_status(
        public_system=public_system,
        workplace_active=active,
        workplace_candidate=candidate,
        projects=projects,
        live_state=live_state,
        components=components,
        decisions=decisions,
        workplace_root=root,
    )
    return status, setup_issues


def _append_status_issue(status: dict, issue: str) -> None:
    """Append one CLI setup issue to the read-only status projection."""

    if issue not in status.setdefault("issues", []):
        status["issues"].append(issue)
    workplace = status.setdefault("workplace", {})
    workplace_issues = workplace.setdefault("issues", [])
    if issue not in workplace_issues:
        workplace_issues.append(issue)
    workplace["status"] = "issue"


def _neutral_first_run(status: dict, root: Path, workplace: object | None) -> bool:
    """Return whether only expected missing optional state exists on first run."""

    if workplace is None or getattr(workplace, "record", {}).get("identity") != {"kind": "neutral"}:
        return False
    if any(
        (root / "system" / name).exists()
        for name in (
            "active-version.json",
            "active.json",
            "candidate-version.json",
            "candidate.json",
        )
    ):
        return False
    expected_prefixes = (
        "Workplace active System record is missing",
        "Project record is missing:",
        "Live runtime state is missing",
    )
    issues = status.get("issues", [])
    return bool(issues) and all(
        isinstance(issue, str) and issue.startswith(expected_prefixes) for issue in issues
    )


def _render_cli_status(status: dict, *, details: bool, as_json: bool) -> str:
    """Render the status model with the human-facing Workplace/Perspective label."""

    if as_json:
        return render_workplace_status(status, format="json")
    rendered = render_workplace_status(status, details=details)
    lines = rendered.rstrip("\n").split("\n")
    project = status.get("project", {}).get("current")
    perspective = (
        project.get("title") or project.get("project_id")
        if isinstance(project, dict)
        else "None"
    )
    for index, line in enumerate(lines):
        if line.startswith("Project "):
            lines.insert(index, f"Perspective {perspective}")
            break
    return "\n".join(lines) + "\n"


def _status_requires_nonzero(
    status: dict,
    *,
    setup_error: Exception | None,
    setup_issues: list[str],
    neutral_first_run: bool,
) -> bool:
    """Keep exit codes truthful without making an absent runtime cache fatal."""

    if setup_error is not None or setup_issues:
        return True
    for issue in status.get("issues", []):
        if not isinstance(issue, str):
            return True
        if neutral_first_run and issue.startswith(
            ("Workplace active System record is missing", "Live runtime state is missing")
        ):
            continue
        # The live cache is rebuildable and may legitimately be absent before
        # the first real Project run.  Integrity, ambiguity, stale canonical
        # sources, and malformed durable records remain blocking.
        lower = issue.casefold()
        if lower == "live runtime state is missing":
            continue
        if "missing" in lower:
            return True
        if lower.startswith(("project record", "project records")):
            return True
        if any(
            marker in lower
            for marker in (
                "ambiguous",
                "invalid",
                "integrity",
                "stale",
                "does not match",
                "not explicitly identify",
                "not explicitly active",
                "missing system version",
                "digest mismatch",
                "missing system record:",
                "missing project record",
                "not an object",
            )
        ):
            return True
    return False


def _run_workplace_command(args: argparse.Namespace) -> int:
    root = _argument_workplace_root(args, positional_name="workplace_root_argument")
    try:
        if args.workplace_action == "ensure":
            workplace = ensure_workplace(root)
            print(f"Workplace ready: {workplace.record_path}")
            return 0
        if args.workplace_action == "validate":
            # Validation is deliberately read-only: a legacy source remains
            # untouched until the explicit migrate route is selected.
            workplace = load_workplace(root, migrate_legacy=False)
            workplace.validate_version_state()
            print(f"Workplace valid: {workplace.record_path}")
            return 0
        if args.workplace_action == "migrate":
            # The user-facing route is the transactional whole-tree migration.
            # Keep the legacy single-file helper available only to compatibility
            # callers; it must never be selected by this command.
            from fractal.workplace_migration import migrate_workplace_tree

            result = migrate_workplace_tree(
                root,
                active_pointer_path=args.active_pointer,
                live_state_path=args.live_state,
                runtime_root=args.runtime_root,
                event_root=args.event_root,
                context_roots=_parse_context_roots(args.context_root),
            )
            print(
                "Workplace migration verified: "
                f"changed={result.changed} idempotent={result.idempotent} "
                f"operations={','.join(result.operations) or 'none'}"
            )
            return 0
    except (OSError, WorkplaceError, ValueError, RuntimeError) as error:
        print(f"Workplace issue: {error}")
        return 2
    raise AssertionError(f"Unhandled Workplace action: {args.workplace_action}")


def _run_status_command(args: argparse.Namespace) -> int:
    root = _argument_workplace_root(args, positional_name="status_root")
    workplace = None
    setup_error: Exception | None = None
    try:
        # Status has one intentionally narrow first-run write: ensure_workplace
        # creates a neutral canonical root and verifies its read-back.  All
        # status-source reads and rendering below remain read-only.
        workplace = ensure_workplace(root)
    except (OSError, WorkplaceError, ValueError) as error:
        setup_error = error

    try:
        status, setup_issues = _status_inputs(args, root, workplace)
    except (OSError, ValueError, TypeError) as error:
        print(f"Workplace status issue: {error}")
        return 2
    for issue in setup_issues:
        _append_status_issue(status, issue)
    if setup_error is not None:
        _append_status_issue(status, str(setup_error))

    print(
        _render_cli_status(
            status,
            details=args.details,
            as_json=args.json,
        ),
        end="",
    )
    neutral_first_run = _neutral_first_run(status, root, workplace)
    return (
        2
        if _status_requires_nonzero(
            status,
            setup_error=setup_error,
            setup_issues=setup_issues,
            neutral_first_run=neutral_first_run,
        )
        else 0
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Fractal command-line interface."""
    args = build_parser().parse_args(argv)
    if args.action == "version":
        if args.version_action == "publish":
            order = json.loads(args.order.expanduser().read_text(encoding="utf-8"))
            if value_sha256(order) != args.order_sha256:
                raise ValueError("Publication order digest does not match --order-sha256")
            bypass_audit = (
                json.loads(args.bypass_audit.expanduser().read_text(encoding="utf-8"))
                if args.bypass_audit is not None
                else None
            )
            executor = PublicationExecutor(
                VersionStore(args.runtime_root.expanduser() / "system-version"),
                args.repository_root.expanduser(),
            )
            common = {
                "order": order,
                "fresh_session_receipt_id": args.fresh_session_receipt_id,
                "runtime_route_receipt_id": args.runtime_route_receipt_id,
                "project_id": args.project_id,
                "project_revision": args.project_revision,
                "authority_receipt_id": args.authority_receipt_id,
            }
            if args.reconcile:
                result = executor.reconcile(**common)
            else:
                result = executor.publish(**common, bypass_audit=bypass_audit)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        print(SYSTEM_VERSION)
        return 0
    if args.action == "workplace":
        return _run_workplace_command(args)
    if args.action == "status":
        return _run_status_command(args)
    if args.action == "live-state":
        state_path = args.state.expanduser()
        store = LiveRuntimeStateStore(state_path.parent.parent, state_path=state_path)
        if args.live_state_action == "reconcile":
            state = store.reconcile(
                project_record_path=args.project_record.expanduser(),
                active_pointer_path=args.active_pointer.expanduser(),
            )
        else:
            state = store.verify_current()
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0
    if args.action == "project":
        store = ProjectStore(args.project_root, args.runtime_root)
        if args.project_action == "create":
            record = ProjectRecord(
                project_id=args.project_id,
                title=args.title,
                system_version=args.system_version,
            )
            created = store.create(record, actor=args.actor, platform=args.platform)
            print(json.dumps(created.to_dict(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.project_action == "show":
            print(
                render_project_summary(
                    store.read(args.project_id),
                    details=args.details,
                ),
                end="",
            )
            return 0
        if args.project_action == "verify":
            print(json.dumps(store.verify(args.project_id), sort_keys=True))
            return 0
        if args.project_action == "migrate":
            migrated = store.migrate(
                args.project_id,
                actor=args.actor,
                platform=args.platform,
            )
            print(json.dumps(migrated.to_dict(), ensure_ascii=False, sort_keys=True))
            return 0
    if args.action == "context":
        if args.context_action == "rebuild":
            try:
                context_roots = _parse_context_roots(args.context_root)
                report = rebuild_context_index(
                    args.catalogue,
                    args.database,
                    maximum_file_bytes=args.maximum_file_bytes,
                    roots=context_roots,
                )
            except (OSError, TypeError, ValueError) as error:
                print(f"Context rebuild issue: {error}")
                return 2
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if args.context_action == "search":
            package = assemble_context_package(
                args.database,
                RetrievalRequest(
                    query=args.query,
                    purpose=args.purpose,
                    requester=args.requester,
                    task_type=args.task_type,
                    project_id=args.project_id,
                    max_items=args.max_items,
                    allow_personalisation=args.allow_personalisation,
                ),
                manifest_path=args.manifest,
            )
            print(json.dumps(package, ensure_ascii=False, sort_keys=True))
            return 0
    if args.action == "codex":
        client_environment = None
        if hasattr(args, "codex_home"):
            client_environment = os.environ.copy()
            client_environment["CODEX_HOME"] = str(args.codex_home.expanduser().resolve())
        with CodexAppServerClient(environment=client_environment) as client:
            if args.codex_action == "inspect":
                registry = load_component_registry(args.registry.expanduser())
                cwd = args.cwd.expanduser().resolve()
                codex_home = args.codex_home.expanduser().resolve()
                report = {
                    "record_type": "codex-live-inspection",
                    "compatibility": detect_codex_compatibility(client),
                    "reconciliation": reconcile_codex_components(
                        registry,
                        client,
                        cwd=cwd,
                        codex_home=codex_home,
                    ),
                    "agents_hierarchy": audit_agents_hierarchy(
                        client,
                        cwd=cwd,
                        codex_home=codex_home,
                    ),
                    "legacy_review": detect_legacy_review_inputs(client, cwds=[cwd]),
                    "automatic_change": False,
                }
                _write_optional_json(args.output, report)
                print(render_codex_inspection(report), end="")
                return 0 if report["reconciliation"]["clean"] else 2
            if args.codex_action == "watch":
                report = watch_codex_drift(
                    client,
                    paths=[path.expanduser().resolve() for path in args.path],
                    timeout=args.timeout,
                )
                _write_optional_json(args.output, report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0
            if args.codex_action == "config-apply":
                edits = json.loads(args.edits.expanduser().read_text(encoding="utf-8"))
                report = apply_codex_config_transaction(
                    client,
                    edits=edits,
                    recovery_path=args.recovery.expanduser(),
                    cwd=args.cwd.expanduser().resolve(),
                )
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0
            if args.codex_action == "surface-plan":
                registry = load_component_registry(args.registry.expanduser())
                surface = load_user_surface(args.surface.expanduser(), registry)
                listed, _ = load_codex_skill_catalog(
                    client,
                    cwd=args.cwd.expanduser().resolve(),
                    force_reload=True,
                )
                candidate = args.candidate.expanduser().resolve(strict=True)
                visible_skill_paths = {
                    item["entry_id"]: str(
                        (candidate / "skills" / item["entry_id"] / "SKILL.md").resolve(strict=True)
                    )
                    for item in surface["entries"]
                }
                edits = build_codex_skill_config_edits(
                    surface, listed, visible_skill_paths=visible_skill_paths
                )
                edits_path = args.edits_output.expanduser()
                edits_path.parent.mkdir(parents=True, exist_ok=True)
                edits_path.write_text(
                    json.dumps(edits, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                report = {
                    "record_type": "codex-user-surface-plan",
                    "system_version": surface["system_version"],
                    "audit_before": audit_codex_skill_surface(surface, listed),
                    "edits_output": str(edits_path),
                    "disabled_skill_count": len(edits[0]["value"]),
                    "candidate_visible_skill_paths": visible_skill_paths,
                    "source_files_deleted": False,
                    "requires_restart": True,
                    "automatic_change": False,
                }
                _write_optional_json(args.output, report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0
            if args.codex_action == "surface-audit":
                registry = load_component_registry(args.registry.expanduser())
                surface = load_user_surface(args.surface.expanduser(), registry)
                candidate = args.candidate.expanduser().resolve(strict=True)
                visible_skill_paths = {
                    item["entry_id"]: str(
                        (candidate / "skills" / item["entry_id"] / "SKILL.md").resolve(strict=True)
                    )
                    for item in surface["entries"]
                }
                listed, _ = load_codex_skill_catalog(
                    client,
                    cwd=args.cwd.expanduser().resolve(),
                    force_reload=True,
                )
                report = audit_codex_skill_path_surface(
                    surface,
                    listed,
                    visible_skill_paths=visible_skill_paths,
                )
                _write_optional_json(args.output, report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0 if report["clean"] else 2
            if args.codex_action == "verify-turn":
                report = verify_live_turn_completion(
                    client,
                    cwd=args.cwd.expanduser().resolve(),
                    project_id=args.project_id,
                    journal_path=args.journal.expanduser(),
                    evaluations_path=args.evaluations.expanduser(),
                )
                _write_optional_json(args.output, report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0 if report["status"] == "completed" else 2
            if args.codex_action == "trust-hooks":
                registry = load_component_registry(args.registry.expanduser())
                report = trust_registered_codex_hooks(
                    client,
                    registry,
                    cwd=args.cwd.expanduser().resolve(),
                    codex_home=args.codex_home.expanduser().resolve(),
                    recovery_path=args.recovery.expanduser(),
                )
                trust_receipt = VersionStore(
                    args.runtime_root.expanduser() / "system-version"
                ).record_hook_trust_evidence(report)
                report = {
                    **report,
                    "canonical_trust_receipt_id": trust_receipt["receipt_id"],
                    "canonical_trust_receipt_sha256": trust_receipt["receipt_sha256"],
                }
                _write_optional_json(args.output, report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 0
    if args.action == "components":
        if args.component_action == "rebuild":
            registry = build_component_registry(args.policy.expanduser(), args.output.expanduser())
            print(
                json.dumps(
                    {
                        "record_type": "component-registry-build",
                        "component_count": len(registry["components"]),
                        "output": str(args.output.expanduser()),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.component_action == "surface-build":
            registry = load_component_registry(args.registry.expanduser())
            surface = build_user_surface(
                args.policy.expanduser(), registry, args.output.expanduser()
            )
            print(
                json.dumps(
                    {
                        "record_type": "user-surface-build",
                        "output": str(args.output.expanduser()),
                        **surface["summary"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.component_action == "snapshot":
            registry = load_component_registry(args.registry.expanduser())
            observed = observe_platform_components(
                registry,
                platform=args.platform,
                platform_home=args.home.expanduser(),
                tool_snapshot_path=args.tools.expanduser(),
                configured_mcp=args.configured_mcp,
                platform_surface_path=(
                    args.platform_surface.expanduser() if args.platform_surface else None
                ),
            )
            args.output.expanduser().write_text(
                json.dumps(observed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "record_type": "component-snapshot-build",
                        "component_count": len(observed["components"]),
                        "output": str(args.output.expanduser()),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.component_action == "install-candidate":
            installer_class = {
                "claude": ClaudeComponentInstaller,
                "codex": CodexComponentInstaller,
                "gemini": GeminiComponentInstaller,
            }[args.platform]
            record = installer_class(
                args.state_root.expanduser(), args.quarantine_root.expanduser()
            ).install(args.built.expanduser(), args.home.expanduser())
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
            return 0
        if args.component_action == "restore":
            result = CodexComponentInstaller(
                args.state_root.expanduser(), args.quarantine_root.expanduser()
            ).restore(args.install_id)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        registry = load_component_registry(args.registry.expanduser())
        if args.component_action == "show":
            live_state_path = args.live_state.expanduser()
            live_state = None
            if live_state_path.is_file():
                live_state = LiveRuntimeStateStore(
                    live_state_path.parent.parent,
                    state_path=live_state_path,
                ).verify_current()
            print(
                render_component_status(
                    registry,
                    platform=args.platform,
                    live_state=live_state,
                ),
                end="",
            )
            return 0
        if args.component_action == "audit":
            observed = json.loads(args.observed.expanduser().read_text(encoding="utf-8"))
            if isinstance(observed, dict):
                observed = observed["components"]
            audit = audit_component_drift(registry, observed, platform=args.platform)
            print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
            return 0 if audit["clean"] else 2
    raise AssertionError(f"Unhandled action: {args.action}")
