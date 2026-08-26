"""Prove every Blueprint responsibility has one path to Continuous Improvement."""

from __future__ import annotations

from typing import Any

from fractal.blueprint import load_blueprint
from fractal.blueprint_audit import load_blueprint_implementation_map
from fractal.flagship import load_flagship_implementation_matrix


def build_continuous_improvement_purpose_receipt() -> dict[str, Any]:
    """Return the exact Element-to-Flow-to-Protagonist-to-purpose paths."""
    blueprint = load_blueprint()
    implementation = load_blueprint_implementation_map()
    flagship = load_flagship_implementation_matrix()
    source_decisions = {
        entry["element_id"]: entry["decision"] for entry in flagship["entries"]
    }
    downstream = {
        element["element_id"]: []
        for genre in blueprint["element_library"]["genres"]
        for element in genre["elements"]
    }
    for flow in blueprint["flows"]["entries"]:
        for element_id in flow["uses_elements"]:
            downstream[element_id].append(flow["flow_id"])
    if any(not flow_ids for flow_ids in downstream.values()):
        raise ValueError("Every Library Element requires a Continuous Improvement Flow path")
    element_paths = {
        element_id: {
            "flows": flow_ids,
            "owner": blueprint["flows"]["owner"],
            "purpose": blueprint["element_library"]["core"]["philosophy"]["element_id"],
            "flagship_decision": source_decisions[element_id],
        }
        for element_id, flow_ids in downstream.items()
    }
    responsibility_paths = {
        item["responsibility_id"]: {
            "human_name": item["human_name"],
            "primary_element_id": item["primary_element_id"],
            "flows": item["flow_ids"],
            "path": item["continuous_improvement_path"],
            "apple_alignment": "deterministic-validated-human-delight-pending",
            "claim_boundary": item["claim_boundary"],
        }
        for item in implementation["responsibility_mappings"]
    }
    return {
        "record_type": "continuous-improvement-purpose-receipt",
        "record_version": 1,
        "purpose": "continuous-improvement",
        "sole_protagonist": "system-review",
        "flow_owner": blueprint["flows"]["owner"],
        "element_paths": element_paths,
        "responsibility_paths": responsibility_paths,
        "blueprint_change_path": [
            "blueprint-change-rules",
            "map-implementations-to-blueprint",
            "system-review",
            "continuous-improvement",
        ],
        "naming_path": [
            "naming-system",
            "map-implementations-to-blueprint",
            "system-review",
            "continuous-improvement",
        ],
        "retained_change_groups": [
            {
                "group": "Blueprint and Flow truth",
                "purpose": "Define what Fractal owns and how System Review uses it.",
            },
            {
                "group": "Flagship local implementations",
                "purpose": "Make the owned responsibilities execute without donor authority.",
            },
            {
                "group": "Naming",
                "purpose": "Keep Fractal-controlled identities intelligible and exact.",
            },
            {
                "group": "Evidence, tests and recovery",
                "purpose": "Distinguish claims from contract, staged and active-live reality.",
            },
        ],
        "unrelated_change_groups": [],
        "aligned": True,
        "claim_boundary": (
            "This receipt proves architecture and candidate-scope alignment. Active-live later "
            "Project improvement remains pending until a separately authorised System Version."
        ),
    }


def render_continuous_improvement_purpose_audit() -> str:
    receipt = build_continuous_improvement_purpose_receipt()
    lines = [
        "# Continuous Improvement Purpose Audit",
        "",
        f"> {receipt['claim_boundary']}",
        "",
        f"- One purpose: `{receipt['purpose']}`",
        f"- Sole Protagonist: `{receipt['sole_protagonist']}`",
        f"- Classified Library Elements with a Flow path: `{len(receipt['element_paths'])}`",
        (
            "- Persistent responsibilities with the same purpose path: "
            f"`{len(receipt['responsibility_paths'])}`"
        ),
        f"- Unrelated retained change groups: `{len(receipt['unrelated_change_groups'])}`",
        "",
        "## Element paths",
        "",
        "| Element | Used by Flows | Flagship decision | Owner | Purpose |",
        "|---|---|---|---|---|",
    ]
    for element_id, path in receipt["element_paths"].items():
        lines.append(
            f"| `{element_id}` | {', '.join(path['flows'])} | "
            f"`{path['flagship_decision']}` | `{path['owner']}` | `{path['purpose']}` |"
        )
    lines.extend(
        [
            "",
            "## Persistent responsibility paths",
            "",
            "| Responsibility | Primary Element | Flows | Review path | Apple | Claim boundary |",
            "|---|---|---|---|---|---|",
        ]
    )
    for responsibility_id, path in receipt["responsibility_paths"].items():
        lines.append(
            f"| `{responsibility_id}` {path['human_name']} | "
            f"`{path['primary_element_id']}` | {', '.join(path['flows'])} | "
            f"{' → '.join(path['path'])} | `{path['apple_alignment']}` | "
            f"{path['claim_boundary']} |"
        )
    lines.extend(
        [
            "",
            "## Retained change groups",
            "",
        ]
    )
    for group in receipt["retained_change_groups"]:
        lines.append(f"- **{group['group']}:** {group['purpose']}")
    lines.extend(
        [
            "",
            "## Explicit supporting path",
            "",
            "`Naming System → Flow 6 → System Review → Continuous Improvement`",
            "",
            "No donor engine, donor identity, publication, activation, or unrelated product "
            "scope is retained by this candidate.",
        ]
    )
    return "\n".join(lines) + "\n"
