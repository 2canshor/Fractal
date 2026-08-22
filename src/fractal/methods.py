"""Validation and lookup for Fractal Method and Philosophy records."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

VALID_STATUSES = {
    "architecture-baseline",
    "approved-technical-decision",
    "proposed-technical-baseline",
    "intent-established-mechanism-deferred",
    "intent-established-mechanism-under-research",
    "intent-established-mechanism-partially-defined",
    "concept-under-clarification",
    "retired",
}


def load_method_registry() -> dict[str, Any]:
    """Load the registry and reject false or incomplete capability claims."""
    path = files("fractal.data").joinpath("method-registry.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if [item["id"] for item in value["core_philosophies"]] != ["continuous-improvement"]:
        raise ValueError("Continuous Improvement must be the only Core Philosophy")
    all_items = [
        *value["core_philosophies"],
        *value["methodologies"],
        *value["supporting_philosophies"],
    ]
    ids: set[str] = set()
    for item in all_items:
        if item["id"] in ids:
            raise ValueError(f"Duplicate Method id: {item['id']}")
        ids.add(item["id"])
        if item["decision_status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid Decision Status: {item['decision_status']}")
        if not item.get("operational_mapping"):
            raise ValueError(f"Missing operational mapping: {item['id']}")
        if "partially-defined" in item["decision_status"] and (
            not item.get("open_questions") or not item.get("false_claim_guard")
        ):
            raise ValueError(f"Partially defined Method lacks open design record: {item['id']}")
    return value
