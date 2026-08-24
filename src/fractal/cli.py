"""Command-line entrypoint for Fractal."""

from __future__ import annotations

import argparse
import json
import os
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
from fractal.storage import ProjectStore
from fractal.user_surface import (
    audit_codex_skill_path_surface,
    audit_codex_skill_surface,
    build_codex_skill_config_edits,
    build_user_surface,
    load_user_surface,
)
from fractal.views import render_project_summary


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="fractal",
        description="Operate a Fractal continuous-improvement workspace.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("version", help="Show the active Fractal system version.")
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
    codex_trust.add_argument("--recovery", required=True, type=Path)
    codex_trust.add_argument("--output", type=Path)
    return parser


def _add_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Fractal command-line interface."""
    args = build_parser().parse_args(argv)
    if args.action == "version":
        print(SYSTEM_VERSION)
        return 0
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
            report = rebuild_context_index(
                args.catalogue,
                args.database,
                maximum_file_bytes=args.maximum_file_bytes,
            )
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
        with CodexAppServerClient() as client:
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
