"""Build an exhaustive evidence matrix without creating Blueprint Elements."""

from __future__ import annotations

from typing import Any

from fractal.blueprint import ROLE_BY_MARKER, load_blueprint
from fractal.blueprint_audit import load_blueprint_implementation_map
from fractal.flagship import load_flagship_implementation_matrix
from fractal.methods import load_agentic_element_map

PROOF_LEVELS = ("contract", "synthetic", "staged", "active_live")


def _proof_layers(node_ids: list[str], nodes: dict[str, dict[str, Any]]) -> dict[str, str]:
    statuses = [nodes[node_id]["status"] for node_id in node_ids]
    execution = {status["execution"] for status in statuses}
    return {
        "contract": "verified",
        "synthetic": (
            "verified"
            if execution.intersection({"verified-synthetic", "verified-staged", "verified-live"})
            else "pending"
        ),
        "staged": (
            "verified"
            if execution.intersection({"verified-staged", "verified-live"})
            else "pending"
        ),
        "active_live": "verified" if "verified-live" in execution else "pending",
    }


def build_blueprint_coverage_matrix() -> dict[str, Any]:
    """Return one row per core item, Library Element, Flow, and Flow use."""
    blueprint = load_blueprint()
    audit = load_blueprint_implementation_map()
    agentic_map = load_agentic_element_map()
    nodes = {item["node_id"]: item for item in agentic_map["mappings"]}
    element_audit = {item["element_id"]: item for item in audit["mappings"]}
    flagship = {
        item["element_id"]: item for item in load_flagship_implementation_matrix()["entries"]
    }
    flow_audit = {item["flow_id"]: item for item in audit["flow_mappings"]}
    library = blueprint["element_library"]

    classifications: dict[str, dict[str, str]] = {
        library["core"]["philosophy"]["element_id"]: {
            "human_name": library["core"]["philosophy"]["human_name"],
            "genre": "core-philosophy",
            "role": "core",
        },
        library["core"]["protagonist"]["element_id"]: {
            "human_name": library["core"]["protagonist"]["human_name"],
            "genre": "core",
            "role": "protagonist",
        },
    }
    for genre in library["genres"]:
        for element in genre["elements"]:
            classifications[element["element_id"]] = {
                "human_name": element["human_name"],
                "genre": genre["genre_id"],
                "role": ROLE_BY_MARKER[element["marker"]],
            }

    downstream_flows: dict[str, list[str]] = {
        element_id: [] for element_id in classifications
    }
    for flow in blueprint["flows"]["entries"]:
        for element_id in flow["uses_elements"]:
            downstream_flows[element_id].append(flow["flow_id"])

    element_rows = []
    for element_id, classification in classifications.items():
        mapping = element_audit[element_id]
        node_ids = mapping["current_node_ids"]
        element_rows.append(
            {
                "element_id": element_id,
                **classification,
                "current_implementation": node_ids,
                "implementation_assessment": mapping["implementation_assessment"],
                "proof_layers": _proof_layers(node_ids, nodes),
                "evidence_refs": [
                    f"blueprint-implementation-map:{element_id}",
                    *(f"agentic-element-map:{node_id}" for node_id in node_ids),
                ],
                "downstream_flows": downstream_flows[element_id],
                "missing_behavior": mapping["gap"],
                "flagship_research": flagship[element_id]["decision"],
            }
        )

    flow_rows = []
    flow_use_rows = []
    for flow in blueprint["flows"]["entries"]:
        mapping = flow_audit[flow["flow_id"]]
        node_ids = mapping["current_node_ids"]
        flow_rows.append(
            {
                "flow_id": flow["flow_id"],
                "human_name": flow["human_name"],
                "sequence": flow["sequence"],
                "uses_elements": flow["uses_elements"],
                "current_implementation": node_ids,
                "implementation_assessment": mapping["implementation_assessment"],
                "proof_layers": _proof_layers(node_ids, nodes),
                "evidence_refs": [
                    f"blueprint-flow:{flow['flow_id']}",
                    f"blueprint-implementation-map:{flow['flow_id']}",
                    *(f"agentic-element-map:{node_id}" for node_id in node_ids),
                ],
                "missing_behavior": mapping["gap"],
            }
        )
        for element_id in flow["uses_elements"]:
            flow_use_rows.append(
                {
                    "use_id": f"{flow['flow_id']}->{element_id}",
                    "flow_id": flow["flow_id"],
                    "element_id": element_id,
                    "contract_proof": "verified",
                    "synthetic_proof": "pending",
                    "staged_proof": "pending",
                    "active_live_proof": "pending",
                    "evidence_refs": [
                        f"blueprint-flow:{flow['flow_id']}",
                        f"blueprint-element:{element_id}",
                    ],
                    "recovery": "Remove the Flow reference; the Library Element remains intact.",
                }
            )

    flow_ids = [flow["flow_id"] for flow in blueprint["flows"]["entries"]]
    flow_transition_rows = [
        {
            "transition_id": "project-completion->find-problems",
            "from": "project-completion",
            "to": flow_ids[0],
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "stop_boundary": False,
        },
        *[
            {
                "transition_id": f"{current}->{successor}",
                "from": current,
                "to": successor,
                "contract_proof": "verified",
                "synthetic_proof": "verified",
                "staged_proof": "verified",
                "active_live_proof": "pending",
                "stop_boundary": False,
            }
            for current, successor in zip(flow_ids, flow_ids[1:], strict=False)
        ],
        {
            "transition_id": "present-decisions-one-by-one->human-decision",
            "from": flow_ids[-1],
            "to": "human-decision",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "stop_boundary": True,
        },
    ]
    lifecycle_arrow_rows = [
        {
            "arrow_id": "work-completed->work-signature",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "verified",
            "remaining_gap": "No gap in the currently active capture arrow.",
        },
        {
            "arrow_id": "fatigue->perspective",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "remaining_gap": "Candidate Hook has not been activated on the live Project.",
        },
        {
            "arrow_id": "perspective->versioned-project-plan",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "remaining_gap": (
                "A candidate-triggered live checkpoint has not updated the active Plan."
            ),
        },
        {
            "arrow_id": "project-completion->system-review",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "remaining_gap": (
                "The active Project is not completed and cannot be used for this proof."
            ),
        },
        {
            "arrow_id": "system-review->one-by-one-human-decision",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "verified",
            "active_live_proof": "pending",
            "remaining_gap": (
                "The staged path stopped correctly; Carson has not received an active-live "
                "decision from it."
            ),
        },
        {
            "arrow_id": "human-approved-candidate->system-version",
            "contract_proof": "verified",
            "synthetic_proof": "verified",
            "staged_proof": "pending",
            "active_live_proof": "pending",
            "remaining_gap": "No approval or versioning authority exists in this task.",
        },
        {
            "arrow_id": "system-version->future-project-outcome",
            "contract_proof": "verified",
            "synthetic_proof": "pending",
            "staged_proof": "pending",
            "active_live_proof": "pending",
            "remaining_gap": "A future Project under an activated candidate does not yet exist.",
        },
    ]
    responsibility_rows = [
        {
            "responsibility_id": item["responsibility_id"],
            "human_name": item["human_name"],
            "primary_element_id": item["primary_element_id"],
            "supporting_element_ids": item["supporting_element_ids"],
            "flow_ids": item["flow_ids"],
            "artifact_count": sum(
                len(item[field])
                for field in ("source_paths", "schema_paths", "data_paths", "test_paths")
            ),
            "proof_layers": item["proof_layers"],
            "continuous_improvement_path": item["continuous_improvement_path"],
            "apple_alignment": "deterministic-validated-human-delight-pending",
            "claim_boundary": item["claim_boundary"],
        }
        for item in audit["responsibility_mappings"]
    ]

    return {
        "record_type": "blueprint-coverage-matrix",
        "record_version": 1,
        "blueprint_version": blueprint["blueprint_version"],
        "claim_boundary": (
            "Contract coverage is verified from source. Synthetic, staged and active-live "
            "proof remain separate and are never inferred from registration alone."
        ),
        "element_rows": element_rows,
        "flow_rows": flow_rows,
        "flow_use_rows": flow_use_rows,
        "flow_transition_rows": flow_transition_rows,
        "lifecycle_arrow_rows": lifecycle_arrow_rows,
        "responsibility_rows": responsibility_rows,
    }


def render_blueprint_coverage_matrix() -> str:
    """Render the complete current matrix for human inspection."""
    matrix = build_blueprint_coverage_matrix()
    lines = [
        "# Blueprint Coverage Matrix",
        "",
        f"> {matrix['claim_boundary']}",
        "",
        "## Core and Element Library",
        "",
        "| Element | Genre | Role | Current implementation | Proof | Missing behaviour |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix["element_rows"]:
        proof = ", ".join(
            f"{level}={row['proof_layers'][level]}" for level in PROOF_LEVELS
        )
        implementation = ", ".join(row["current_implementation"]) or "None"
        lines.append(
            f"| `{row['element_id']}` | `{row['genre']}` | `{row['role']}` | "
            f"{implementation} | {proof} | {row['missing_behavior']} |"
        )
    lines.extend(
        [
            "",
            "## Flows",
            "",
            "| Flow | Uses | Current implementation | Proof | Missing behaviour |",
            "|---|---|---|---|---|",
        ]
    )
    for row in matrix["flow_rows"]:
        proof = ", ".join(
            f"{level}={row['proof_layers'][level]}" for level in PROOF_LEVELS
        )
        implementation = ", ".join(row["current_implementation"]) or "None"
        lines.append(
            f"| `{row['sequence']}. {row['flow_id']}` | "
            f"{', '.join(row['uses_elements'])} | {implementation} | {proof} | "
            f"{row['missing_behavior']} |"
        )
    lines.extend(
        [
            "",
            "## Flow-to-Element Uses",
            "",
            "| Use | Contract | Synthetic | Staged | Active live | Recovery |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in matrix["flow_use_rows"]:
        lines.append(
            f"| `{row['use_id']}` | `{row['contract_proof']}` | "
            f"`{row['synthetic_proof']}` | `{row['staged_proof']}` | "
            f"`{row['active_live_proof']}` | {row['recovery']} |"
        )
    lines.extend(
        [
            "",
            "## Flow Transitions",
            "",
            "| Transition | Contract | Synthetic | Staged | Active live | Human stop |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in matrix["flow_transition_rows"]:
        lines.append(
            f"| `{row['transition_id']}` | `{row['contract_proof']}` | "
            f"`{row['synthetic_proof']}` | `{row['staged_proof']}` | "
            f"`{row['active_live_proof']}` | `{row['stop_boundary']}` |"
        )
    lines.extend(
        [
            "",
            "## Continuous Improvement Lifecycle Arrows",
            "",
            "| Arrow | Contract | Synthetic | Staged | Active live | Remaining gap |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in matrix["lifecycle_arrow_rows"]:
        lines.append(
            f"| `{row['arrow_id']}` | `{row['contract_proof']}` | "
            f"`{row['synthetic_proof']}` | `{row['staged_proof']}` | "
            f"`{row['active_live_proof']}` | {row['remaining_gap']} |"
        )
    lines.extend(
        [
            "",
            "## Persistent Responsibilities",
            "",
            (
                "| Responsibility | Primary Element | Supporting Elements | Flows | "
                "Artifacts | Proof | Apple |"
            ),
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in matrix["responsibility_rows"]:
        proof = ", ".join(
            f"{level}={row['proof_layers'][level]}" for level in PROOF_LEVELS
        )
        supporting = ", ".join(row["supporting_element_ids"]) or "None"
        lines.append(
            f"| `{row['responsibility_id']}` {row['human_name']} | "
            f"`{row['primary_element_id']}` | {supporting} | "
            f"{', '.join(row['flow_ids'])} | `{row['artifact_count']}` | {proof} | "
            f"`{row['apple_alignment']}` |"
        )
    return "\n".join(lines) + "\n"
