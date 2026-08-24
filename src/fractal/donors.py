"""Validate the governed open-source donor inventory."""

from __future__ import annotations

import json
import re
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
        blueprint["core"]["philosophy"]["element_id"],
        blueprint["core"]["protagonist"]["element_id"],
        *(element["element_id"] for genre in blueprint["genres"] for element in genre["elements"]),
        *(element["element_id"] for element in blueprint["unclassified_elements"]),
    }


def validate_donor_inventory(value: dict[str, Any]) -> dict[str, Any]:
    """Reject untraceable donors and any implied donor authority."""
    if value.get("record_type") != "donor-inventory":
        raise ValueError("Donor inventory record type is invalid")
    if value.get("blueprint_version") != load_blueprint()["blueprint_version"]:
        raise ValueError("Donor inventory Blueprint version mismatch")
    donors = value.get("donors")
    if not isinstance(donors, list):
        raise ValueError("Donor inventory is missing")
    donor_ids = [item.get("donor_id") for item in donors]
    if len(donor_ids) != len(set(donor_ids)):
        raise ValueError("Donor ids must be unique")
    if "hermes-agent" not in donor_ids:
        raise ValueError("Hermes must be recorded as a donor")
    targets = _blueprint_targets()
    for donor in donors:
        donor_id = donor.get("donor_id")
        if donor.get("architecture_authority") is not False:
            raise ValueError(f"Donor cannot receive architecture authority: {donor_id}")
        status = donor.get("repository_status")
        if status not in REPOSITORY_STATUSES:
            raise ValueError(f"Donor repository status is invalid: {donor_id}")
        licence = donor.get("licence")
        if not isinstance(licence, dict) or licence.get("status") not in LICENCE_STATUSES:
            raise ValueError(f"Donor licence evidence is invalid: {donor_id}")
        if status == "observed":
            if not isinstance(donor.get("source_url"), str):
                raise ValueError(f"Observed donor requires a source URL: {donor_id}")
            if re.fullmatch(r"[0-9a-f]{40}", str(donor.get("commit"))) is None:
                raise ValueError(f"Observed donor requires an exact commit: {donor_id}")
        elif donor.get("source_url") is not None or donor.get("commit") is not None:
            raise ValueError(f"A no-finding donor cannot claim a source: {donor_id}")
        for capability in donor.get("capabilities", []):
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
            if (
                disposition == "staged-adaptation-candidate"
                and licence.get("status") != "verified-file"
            ):
                raise ValueError(
                    f"A code adaptation candidate requires verified licence text: {donor_id}"
                )
    return value


def load_donor_inventory() -> dict[str, Any]:
    """Load and validate the packaged donor inventory."""
    path = files("fractal.data").joinpath("donor-inventory.json")
    return validate_donor_inventory(json.loads(path.read_text(encoding="utf-8")))


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
        "| Donor | Repository Status | Exact Commit | Licence | Role |",
        "|---|---|---|---|---|",
    ]
    for donor in inventory["donors"]:
        source = (
            f"[{donor['human_name']}]({donor['source_url']})"
            if donor["source_url"]
            else donor["human_name"]
        )
        commit = f"`{donor['commit']}`" if donor["commit"] else "None"
        licence = donor["licence"]
        licence_text = f"{licence['spdx'] or 'Unknown'} / {licence['status']}"
        lines.append(
            f"| {source} | `{donor['repository_status']}` | {commit} | "
            f"{licence_text} | `{donor['donor_role']}` |"
        )
    lines.extend(
        [
            "",
            "## Bounded Capabilities",
            "",
            "| Donor | Capability | Blueprint Target | Disposition | Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for donor in inventory["donors"]:
        for capability in donor["capabilities"]:
            lines.append(
                f"| `{donor['donor_id']}` | `{capability['capability_id']}` — "
                f"{capability['summary']} | `{capability['blueprint_target']}` | "
                f"`{capability['disposition']}` | {capability['reason']} |"
            )
    return "\n".join(lines) + "\n"
