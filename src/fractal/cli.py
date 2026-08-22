"""Command-line entrypoint for Fractal."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from fractal import SYSTEM_VERSION
from fractal.context import RetrievalRequest, assemble_context_package, rebuild_context_index
from fractal.models import ProjectRecord
from fractal.storage import ProjectStore
from fractal.views import render_project_summary


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="fractal",
        description="Operate a Fractal continuous-improvement workspace.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("version", help="Show the active Fractal system version.")
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
    return parser


def _add_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Fractal command-line interface."""
    args = build_parser().parse_args(argv)
    if args.action == "version":
        print(SYSTEM_VERSION)
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
    raise AssertionError(f"Unhandled action: {args.action}")
