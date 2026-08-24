"""Validate the governed open-source donor inventory."""

from __future__ import annotations

import json
import re
from importlib import util
from importlib.resources import files
from typing import Any

from fractal.blueprint import load_blueprint

DISPOSITIONS = {
    "future-migration-source",
    "quarantined",
    "reject-authority",
    "research-only",
    "staged-adaptation-candidate",
}
REPOSITORY_STATUSES = {"observed", "no-primary-source-finding"}
LICENCE_STATUSES = {"declared-no-licence-file", "unknown", "verified-file"}


def _blueprint_targets() -> set[str]:
    blueprint = load_blueprint()
    return {
        blueprint["element_library"]["core"]["philosophy"]["element_id"],
        blueprint["element_library"]["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in blueprint["element_library"]["genres"]
            for element in genre["elements"]
        ),
        *(
            element["element_id"]
            for element in blueprint["blueprint_change_rules"]["candidate_queue"]
        ),
    }


def validate_donor_inventory(value: dict[str, Any]) -> dict[str, Any]:
    """Reject untraceable donors and any implied donor authority."""
    if value.get("record_type") != "donor-inventory":
        raise ValueError("Donor inventory record type is invalid")
    if value.get("blueprint_version") != load_blueprint()["blueprint_version"]:
        raise ValueError("Donor inventory Blueprint version mismatch")
    if (
        value.get("donor_set_fixed") is not False
        or value.get("select_from_current_element_need") is not True
        or value.get("runtime_dependency_on_donor_services") is not False
        or value.get("fractal_local_names_required") is not True
    ):
        raise ValueError("Donor selection and local-runtime policy is invalid")
    donors = value.get("donors")
    if not isinstance(donors, list):
        raise ValueError("Donor inventory is missing")
    donor_ids = [item.get("donor_id") for item in donors]
    if len(donor_ids) != len(set(donor_ids)):
        raise ValueError("Donor ids must be unique")
    if "hermes-agent" not in donor_ids:
        raise ValueError("Hermes must be recorded as a donor")
    targets = _blueprint_targets()
    flow_ids = {flow["flow_id"] for flow in load_blueprint()["flows"]["entries"]}
    for donor in donors:
        donor_id = donor.get("donor_id")
        if donor.get("architecture_authority") is not False:
            raise ValueError(f"Donor cannot receive architecture authority: {donor_id}")
        status = donor.get("repository_status")
        if status not in REPOSITORY_STATUSES:
            raise ValueError(f"Donor repository status is invalid: {donor_id}")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(donor.get("acquired_at"))) is None:
            raise ValueError(f"Donor acquisition date is invalid: {donor_id}")
        context_cost = donor.get("context_cost")
        if (
            not isinstance(context_cost, dict)
            or not isinstance(context_cost.get("always_loaded_tokens_delta"), int)
            or not str(context_cost.get("summary", "")).strip()
        ):
            raise ValueError(f"Donor context cost is incomplete: {donor_id}")
        if not str(donor.get("recovery_path", "")).strip():
            raise ValueError(f"Donor recovery path is missing: {donor_id}")
        licence = donor.get("licence")
        if not isinstance(licence, dict) or licence.get("status") not in LICENCE_STATUSES:
            raise ValueError(f"Donor licence evidence is invalid: {donor_id}")
        if status == "observed":
            if not isinstance(donor.get("source_url"), str):
                raise ValueError(f"Observed donor requires a source URL: {donor_id}")
            if re.fullmatch(r"[0-9a-f]{40}", str(donor.get("commit"))) is None:
                raise ValueError(f"Observed donor requires an exact commit: {donor_id}")
            if not str(donor.get("version", "")).strip():
                raise ValueError(f"Observed donor requires an exact version: {donor_id}")
        elif donor.get("source_url") is not None or donor.get("commit") is not None:
            raise ValueError(f"A no-finding donor cannot claim a source: {donor_id}")
        for capability in donor.get("capabilities", []):
            if not str(capability.get("capability_id", "")).strip() or not str(
                capability.get("summary", "")
            ).strip():
                raise ValueError(f"Donor capability identity is incomplete: {donor_id}")
            disposition = capability.get("disposition")
            if disposition not in DISPOSITIONS:
                raise ValueError(
                    f"Donor capability disposition is invalid: {capability.get('capability_id')}"
                )
            if capability.get("blueprint_target") not in targets:
                raise ValueError(
                    f"Donor capability has an unknown Blueprint target: "
                    f"{capability.get('capability_id')}"
                )
            if capability.get("serves_flow") is not None and capability.get(
                "serves_flow"
            ) not in flow_ids:
                raise ValueError(
                    f"Donor capability has an unknown Flow target: "
                    f"{capability.get('capability_id')}"
                )
            if not str(capability.get("reason", "")).strip():
                raise ValueError(
                    f"Donor capability adoption or rejection reason is missing: "
                    f"{capability.get('capability_id')}"
                )
            if (
                disposition == "staged-adaptation-candidate"
                and licence.get("status") != "verified-file"
            ):
                raise ValueError(
                    f"A code adaptation candidate requires verified licence text: {donor_id}"
                )
    return value


def load_local_donor_adaptations(
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove every staged donor unit has a named local, offline implementation."""
    current_inventory = inventory or load_donor_inventory()
    path = files("fractal.data").joinpath("local-donor-adaptations.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("record_type") != "local-donor-adaptation-set"
        or value.get("runtime_dependency_on_upstream") is not False
        or value.get("donor_set_fixed") is not False
        or value.get("select_from_current_element_need") is not True
    ):
        raise ValueError("Local donor adaptation policy is invalid")
    staged = {
        capability["capability_id"]: donor["donor_id"]
        for donor in current_inventory["donors"]
        for capability in donor["capabilities"]
        if capability["disposition"] == "staged-adaptation-candidate"
    }
    adaptations = value.get("adaptations")
    if not isinstance(adaptations, list):
        raise ValueError("Local donor adaptations are missing")
    adaptation_ids = [item.get("capability_id") for item in adaptations]
    if len(adaptation_ids) != len(set(adaptation_ids)) or set(adaptation_ids) != set(staged):
        missing = sorted(set(staged).difference(adaptation_ids))
        extra = sorted(set(adaptation_ids).difference(staged))
        raise ValueError(
            f"Local donor adaptation coverage mismatch: missing={missing}, extra={extra}"
        )
    donor_names = {
        donor["donor_id"]: {
            donor["donor_id"].lower(),
            donor["human_name"].lower(),
        }
        for donor in current_inventory["donors"]
    }
    for adaptation in adaptations:
        capability_id = adaptation["capability_id"]
        donor_id = adaptation.get("donor_id")
        if donor_id != staged[capability_id]:
            raise ValueError(f"Local adaptation donor mismatch: {capability_id}")
        local_name = str(adaptation.get("local_name", "")).strip()
        if not local_name or any(
            donor_name in local_name.lower() for donor_name in donor_names[donor_id]
        ):
            raise ValueError(f"Local adaptation must use a Fractal name: {capability_id}")
        modules = adaptation.get("implementation_modules")
        if (
            not isinstance(modules, list)
            or not modules
            or any(
                not isinstance(module, str) or util.find_spec(module) is None
                for module in modules
            )
        ):
            raise ValueError(f"Local adaptation implementation is missing: {capability_id}")
        if not adaptation.get("evidence_ids") or not str(
            adaptation.get("recovery", "")
        ).strip():
            raise ValueError(f"Local adaptation evidence or recovery is missing: {capability_id}")
    return value


def load_donor_inventory() -> dict[str, Any]:
    """Load and validate the packaged donor inventory."""
    path = files("fractal.data").joinpath("donor-inventory.json")
    inventory = validate_donor_inventory(json.loads(path.read_text(encoding="utf-8")))
    load_local_donor_adaptations(inventory)
    return inventory


def render_donor_inventory(value: dict[str, Any] | None = None) -> str:
    """Render donor provenance and dispositions without implying adoption."""
    inventory = validate_donor_inventory(value) if value is not None else load_donor_inventory()
    lines = [
        "# Donor Inventory",
        "",
        f"- Observed At: `{inventory['observed_at']}`",
        f"- Blueprint Version: `{inventory['blueprint_version']}`",
        "",
        f"> {inventory['claim_boundary']}",
        "",
        "## Sources",
        "",
        "| Donor | Repository Status | Version | Exact Commit | Acquired | Licence | Role |",
        "|---|---|---|---|---|---|---|",
    ]
    for donor in inventory["donors"]:
        source = (
            f"[{donor['human_name']}]({donor['source_url']})"
            if donor["source_url"]
            else donor["human_name"]
        )
        commit = f"`{donor['commit']}`" if donor["commit"] else "None"
        version = f"`{donor['version']}`" if donor["version"] else "None"
        licence = donor["licence"]
        licence_text = f"{licence['spdx'] or 'Unknown'} / {licence['status']}"
        lines.append(
            f"| {source} | `{donor['repository_status']}` | {version} | {commit} | "
            f"`{donor['acquired_at']}` | {licence_text} | `{donor['donor_role']}` |"
        )
    lines.extend(
        [
            "",
            "## Context and Recovery",
            "",
            "| Donor | Always-Loaded Tokens | Context Boundary | Recovery |",
            "|---|---:|---|---|",
        ]
    )
    for donor in inventory["donors"]:
        context = donor["context_cost"]
        lines.append(
            f"| `{donor['donor_id']}` | `{context['always_loaded_tokens_delta']}` | "
            f"{context['summary']} | {donor['recovery_path']} |"
        )
    lines.extend(
        [
            "",
            "## Bounded Capabilities",
            "",
            "| Donor | Capability | Library Element | Serves Flow | Disposition | Reason |",
            "|---|---|---|---|---|---|",
        ]
    )
    for donor in inventory["donors"]:
        for capability in donor["capabilities"]:
            lines.append(
                f"| `{donor['donor_id']}` | `{capability['capability_id']}` — "
                f"{capability['summary']} | `{capability['blueprint_target']}` | "
                f"`{capability.get('serves_flow', 'not-specific')}` | "
                f"`{capability['disposition']}` | {capability['reason']} |"
            )
    return "\n".join(lines) + "\n"
