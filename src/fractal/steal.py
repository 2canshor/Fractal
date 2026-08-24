"""Run the governed Steal Method without inheriting donor authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from fractal.blueprint import load_blueprint
from fractal.blueprint_audit import load_blueprint_implementation_map
from fractal.blueprint_mapping import load_donor_candidate_mappings
from fractal.donors import load_donor_inventory

RESEARCH_ROUTES = [
    ("improve-current-method", 60),
    ("research-latest-findings", 20),
    ("explore-related-fields", 20),
]
COMPARISON_DIMENSIONS = {
    "authority-compatibility",
    "blueprint-fidelity",
    "context-cost",
    "maintainability",
    "privacy",
    "recovery",
    "reliability",
}
COMPARISON_RESULTS = {"better", "equal", "unknown", "worse"}
DISPOSITIONS = {
    "keep-current",
    "need-more-evidence",
    "no-finding",
    "reject-donor-authority",
    "staged-adaptation-candidate",
}


def select_donors_for_need(element_id: str) -> list[dict[str, Any]]:
    """Return every current viable source for one Element without popularity ranking."""
    if not element_id.strip():
        raise ValueError("Donor selection requires a current Element need")
    inventory = load_donor_inventory()
    candidates = []
    for donor in inventory["donors"]:
        for capability in donor["capabilities"]:
            if capability["blueprint_target"] != element_id or capability["disposition"] in {
                "quarantined",
                "reject-authority",
            }:
                continue
            candidates.append(
                {
                    "donor_id": donor["donor_id"],
                    "capability_id": capability["capability_id"],
                    "disposition": capability["disposition"],
                    "reason": capability["reason"],
                    "source_url": donor["source_url"],
                    "commit": donor["commit"],
                    "version": donor["version"],
                    "licence": donor["licence"],
                    "context_cost": donor["context_cost"],
                    "recovery_path": donor["recovery_path"],
                    "architecture_authority": donor["architecture_authority"],
                }
            )
    return sorted(candidates, key=lambda item: (item["donor_id"], item["capability_id"]))


def stage_local_source_snapshot(
    *,
    snapshot_root: Path,
    donor_id: str,
    capability_id: str,
    source_url: str,
    commit: str,
    acquired_at: str,
    licence_spdx: str,
    licence_text: bytes,
    expected_licence_sha256: str,
    source_files: dict[str, bytes],
    fractal_local_name: str,
    implementation_modules: list[str],
    target_element_id: str,
) -> dict[str, Any]:
    """Persist an immutable selected-source snapshot outside the runtime dependency path."""
    if re.fullmatch(r"[a-f0-9]{40}", commit) is None:
        raise ValueError("Donor source snapshot requires an exact commit")
    if not donor_id.strip() or not capability_id.strip() or not source_url.startswith("https://"):
        raise ValueError("Donor source snapshot provenance is incomplete")
    if (
        not fractal_local_name.strip()
        or not target_element_id.strip()
        or not implementation_modules
    ):
        raise ValueError("Donor source snapshot requires a Fractal mapping")
    actual_licence_sha256 = hashlib.sha256(licence_text).hexdigest()
    if actual_licence_sha256 != expected_licence_sha256:
        raise ValueError("Donor source snapshot licence digest mismatch")
    if not source_files:
        raise ValueError("Donor source snapshot requires selected source files")
    file_records = []
    for relative, content in sorted(source_files.items()):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not relative.strip():
            raise ValueError(f"Unsafe donor source path: {relative}")
        if not isinstance(content, bytes):
            raise ValueError("Donor source snapshot content must be bytes")
        file_records.append(
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    snapshot_id = f"{donor_id}-{capability_id}-{commit[:12]}"
    root = Path(snapshot_root)
    destination = root / snapshot_id
    manifest = {
        "record_type": "local-donor-source-snapshot",
        "record_version": 1,
        "snapshot_id": snapshot_id,
        "donor_id": donor_id,
        "capability_id": capability_id,
        "source_url": source_url,
        "commit": commit,
        "acquired_at": acquired_at,
        "licence": {"spdx": licence_spdx, "sha256": actual_licence_sha256},
        "source_files": file_records,
        "fractal_mapping": {
            "local_name": fractal_local_name,
            "target_element_id": target_element_id,
            "implementation_modules": implementation_modules,
        },
        "runtime_dependency_on_snapshot": False,
        "runtime_dependency_on_upstream": False,
        "active": False,
        "automatic_adoption": False,
        "recovery": "Delete this inactive snapshot; the local Fractal implementation remains.",
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if destination.exists():
        existing = (destination / "manifest.json").read_text(encoding="utf-8")
        if existing != manifest_text:
            raise ValueError("Immutable donor source snapshot already exists with different data")
        return {**manifest, "path": str(destination), "idempotent": True}
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=root))
    try:
        for relative, content in sorted(source_files.items()):
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (temporary / "LICENSE").write_bytes(licence_text)
        (temporary / "manifest.json").write_text(manifest_text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    read_back = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if read_back != manifest:
        raise ValueError("Donor source snapshot read-back mismatch")
    return {**manifest, "path": str(destination), "idempotent": False}


def validate_steal_run(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one complete Steal run from baseline through staged disposition."""
    if value.get("record_type") != "steal-run":
        raise ValueError("Steal run record type is invalid")
    if value.get("status") != "completed-staged-not-active":
        raise ValueError("Steal run must remain completed, staged and inactive")
    target = value.get("target")
    if not isinstance(target, dict) or not str(target.get("desired_effect", "")).strip():
        raise ValueError("Steal run requires a current implementation target and desired effect")
    gap_by_id = {
        item["element_id"]: item for item in load_blueprint_implementation_map()["mappings"]
    }
    blueprint_element_id = target.get("blueprint_element_id")
    if blueprint_element_id not in gap_by_id:
        raise ValueError("Steal target is not a current Blueprint element")
    if (
        target.get("current_assessment")
        != gap_by_id[blueprint_element_id]["implementation_assessment"]
    ):
        raise ValueError("Steal target baseline does not match the current gap audit")
    if not target.get("evidence_ids"):
        raise ValueError("Steal target requires baseline evidence")

    routes = value.get("research_routes")
    if (
        not isinstance(routes, list)
        or [(item.get("action_id"), item.get("effort_share")) for item in routes] != RESEARCH_ROUTES
    ):
        raise ValueError("Steal research must preserve the 60/20/20 routes")
    for route in routes:
        if route.get("status") not in {"finding", "no-finding"}:
            raise ValueError("Steal research route requires a finding or honest no-finding")
        if not str(route.get("summary", "")).strip() or not str(route.get("source", "")).strip():
            raise ValueError("Steal research route requires summary and source provenance")

    donor_candidate = value.get("donor_candidate")
    if not isinstance(donor_candidate, dict):
        raise ValueError("Steal run requires one bounded donor Candidate")
    donor_by_id = {item["donor_id"]: item for item in load_donor_inventory()["donors"]}
    donor = donor_by_id.get(donor_candidate.get("donor_id"))
    if donor is None:
        raise ValueError("Steal run references an unknown donor")
    capability = next(
        (
            item
            for item in donor["capabilities"]
            if item["capability_id"] == donor_candidate.get("capability_id")
        ),
        None,
    )
    if capability is None:
        raise ValueError("Steal run references an unknown donor capability")
    if capability["disposition"] in {"quarantined", "reject-authority"}:
        raise ValueError("Steal cannot adapt a quarantined or rejected donor capability")
    if donor_candidate.get("licence_status") != donor["licence"]["status"]:
        raise ValueError("Steal donor licence status does not match the inventory")
    if (
        value.get("disposition") == "staged-adaptation-candidate"
        and donor["licence"]["status"] != "verified-file"
    ):
        raise ValueError("A staged adaptation requires verified licence text")
    if not donor_candidate.get("removed_authority"):
        raise ValueError("Steal must record removed donor authority")

    comparison = value.get("comparison")
    if not isinstance(comparison, list):
        raise ValueError("Steal run requires an implementation comparison")
    dimensions = [item.get("dimension") for item in comparison]
    if len(dimensions) != len(set(dimensions)) or set(dimensions) != COMPARISON_DIMENSIONS:
        raise ValueError("Steal comparison dimensions are incomplete or duplicated")
    by_dimension = {item["dimension"]: item for item in comparison}
    for item in comparison:
        if item.get("result") not in COMPARISON_RESULTS:
            raise ValueError("Steal comparison result is invalid")
        if not str(item.get("summary", "")).strip() or not item.get("evidence_ids"):
            raise ValueError("Steal comparison requires a summary and evidence")
    if by_dimension["authority-compatibility"]["result"] == "worse":
        raise ValueError("Steal cannot stage weaker authority compatibility")
    if by_dimension["blueprint-fidelity"]["result"] == "worse":
        raise ValueError("Steal cannot stage weaker Blueprint fidelity")

    mappings = {item["candidate_id"]: item for item in load_donor_candidate_mappings()["mappings"]}
    mapping = mappings.get(value.get("candidate_mapping_id"))
    if mapping is None:
        raise ValueError("Steal run requires a validated Blueprint Candidate Mapping")
    if donor_candidate["donor_id"] not in mapping["donor_ids"]:
        raise ValueError("Steal donor and Blueprint Mapping provenance disagree")
    if mapping["target"]["existing_element_id"] != blueprint_element_id:
        raise ValueError("Steal target and Blueprint Mapping target disagree")

    if value.get("disposition") not in DISPOSITIONS:
        raise ValueError("Steal disposition is invalid")
    if value.get("automatic_replace") is not False:
        raise ValueError("Steal can never replace an implementation automatically")
    authority = value.get("authority")
    if not isinstance(authority, dict):
        raise ValueError("Steal run authority boundary is missing")
    for forbidden in ("activation", "canonical_write", "persistent_mutation", "publication"):
        if authority.get(forbidden) is not False:
            raise ValueError(f"Steal cannot receive {forbidden} authority")
    if not str(value.get("recovery", "")).strip():
        raise ValueError("Steal run requires a recovery path")
    return value


def load_steal_dry_runs() -> dict[str, Any]:
    """Load complete dry runs that prove the Method without initialising it globally."""
    path = files("fractal.data").joinpath("steal-dry-runs.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("record_type") != "steal-run-set":
        raise ValueError("Steal run set record type is invalid")
    if value.get("blueprint_version") != load_blueprint()["blueprint_version"]:
        raise ValueError("Steal run set Blueprint version mismatch")
    runs = value.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Steal run set requires at least one dry run")
    run_ids = [run.get("run_id") for run in runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Steal run ids must be unique")
    for run in runs:
        validate_steal_run(run)
    return value
