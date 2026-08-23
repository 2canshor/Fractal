"""Deterministic migrations for canonical Project records."""

from __future__ import annotations

import copy
from typing import Any

from fractal.models import default_lifecycle, default_plan

CURRENT_PROJECT_SCHEMA_VERSION = "1.2"


class MigrationError(ValueError):
    """Raised when no verified migration path exists."""


def migrate_project_record(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Migrate a Project record to the current schema without losing canonical state."""
    migrated = copy.deepcopy(value)
    applied: list[str] = []
    version = migrated.get("schema_version")
    if version == "1.0":
        legacy_direction = migrated.pop("direction")
        lifecycle = default_lifecycle()
        lifecycle["direction"]["intended_outcome"] = legacy_direction["summary"]
        lifecycle["direction"]["status"] = legacy_direction["status"]
        if legacy_direction["confirmed_at"] is not None:
            lifecycle["direction"]["confirmations"].append(
                {
                    "id": "confirmation-migrated-v1",
                    "actor": "migration",
                    "confirmed_at": legacy_direction["confirmed_at"],
                    "summary_sha256": None,
                    "authority_source": "legacy-record",
                }
            )
        migrated["lifecycle"] = lifecycle
        migrated["schema_version"] = "1.1"
        version = "1.1"
        applied.append("project-1.0-to-1.1")
    if version == "1.1":
        resources = default_plan()["resources"]
        for item in resources:
            item["reason"] = (
                "Migrated from Project schema 1.1; no plan-time resource state was recorded."
            )
        migrated["plan"]["resources"] = resources
        migrated["schema_version"] = "1.2"
        version = "1.2"
        applied.append("project-1.1-to-1.2")
    if version != CURRENT_PROJECT_SCHEMA_VERSION:
        raise MigrationError(f"Unsupported Project schema version: {version}")
    return migrated, applied
