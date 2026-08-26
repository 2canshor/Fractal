"""Load, validate and render the candidate Fractal Blueprint."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

EXPECTED_GENRE_MARKERS = {
    "values": {"$"},
    "principles": {"%"},
    "infrastructure": {"^"},
    "methods": {"$", "¢"},
}
ROLE_BY_MARKER = {"$": "deuteragonist", "^": "extra", "¢": "prop", "%": "principle"}

EXPECTED_FLOWS = (
    ("find-problems", "Observe"),
    ("find-local-patterns", "Group"),
    ("find-global-patterns", "Connect"),
    ("find-global-pattern-reasons", "Explain"),
    ("find-global-pattern-solutions", "Explore"),
    ("map-implementations-to-blueprint", "Map"),
    ("debate-global-pattern-solutions", "Challenge"),
    ("present-decisions-one-by-one", "Decide"),
)

REQUIRED_ELEMENTS = {
    "system-review",
    "fatigue",
    "curiosity",
    "greed",
    "project-review",
    "component-governance",
    "deterministic-over-probabilistic",
    "quantity-over-quality",
    "subtraction-first",
    "global-outcome-over-local-optimisation",
    "work-signature",
    "naming-system",
    "capability-check",
    "hooks",
    "reality-check",
    "experiment",
    "human-control",
    "cause-research",
    "two-sided-review",
    "steal",
}


def validate_blueprint(value: dict[str, Any]) -> dict[str, Any]:
    """Reject target-architecture drift without pretending the candidate is active."""
    if value.get("record_type") != "fractal-blueprint":
        raise ValueError("Blueprint record type is invalid")
    lifecycle = value.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("status") != "canonical":
        raise ValueError("The New Blueprint must be the canonical workflow architecture")
    if lifecycle.get("active_state_source") != "system-version-pointer":
        raise ValueError("Blueprint active state must come from the System Version pointer")
    if lifecycle.get("implementation_status") != "active-workflow-required":
        raise ValueError("Blueprint implementation status must require active workflow alignment")
    library = value.get("element_library")
    flows_section = value.get("flows")
    change_section = value.get("blueprint_change_rules")
    if not all(isinstance(section, dict) for section in (library, flows_section, change_section)):
        raise ValueError("Blueprint requires Element Library, Flows and Change Rules sections")
    if [
        library.get("section_id"),
        flows_section.get("section_id"),
        change_section.get("section_id"),
    ] != ["element-library", "flows", "blueprint-change-rules"]:
        raise ValueError("Blueprint sections are incomplete or out of order")

    core = library.get("core")
    if not isinstance(core, dict):
        raise ValueError("Blueprint core is missing")
    if core.get("philosophy", {}).get("element_id") != "continuous-improvement":
        raise ValueError("Continuous Improvement must remain the core philosophy")
    protagonist = core.get("protagonist")
    if not isinstance(protagonist, dict) or protagonist.get("element_id") != "system-review":
        raise ValueError("System Review must be the sole Protagonist")
    if protagonist.get("marker") != "#":
        raise ValueError("The Protagonist marker must be #")

    tag_definitions = library.get("tag_definitions")
    if not isinstance(tag_definitions, list):
        raise ValueError("Blueprint Tag definitions are missing")
    tag_ids = [tag.get("tag_id") for tag in tag_definitions]
    if tag_ids != ["signature-function"]:
        raise ValueError("Signature Function must be a single defined Tag")
    if tag_definitions[0].get("cross_genre") is not True:
        raise ValueError("Signature Function must remain a cross-Genre Tag")

    role_definitions = library.get("role_definitions")
    if not isinstance(role_definitions, list):
        raise ValueError("Blueprint Role definitions are missing")
    observed_roles = {item.get("marker"): item.get("role_id") for item in role_definitions}
    if observed_roles != ROLE_BY_MARKER:
        raise ValueError("Blueprint Roles and markers are incomplete or unexpected")

    genres = library.get("genres")
    if not isinstance(genres, list):
        raise ValueError("Blueprint Genres are missing")
    genre_ids = [genre.get("genre_id") for genre in genres if isinstance(genre, dict)]
    if len(genre_ids) != len(set(genre_ids)):
        raise ValueError("Blueprint Genre ids must be unique")
    if set(genre_ids) != set(EXPECTED_GENRE_MARKERS):
        raise ValueError("Blueprint Genres are incomplete or unexpected")

    element_ids: set[str] = {"system-review"}
    library_element_ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {"system-review": protagonist}
    tag_members: dict[str, set[str]] = {tag_id: set() for tag_id in tag_ids}
    for genre in genres:
        if genre.get("marker") != "*":
            raise ValueError(f"Genre marker must be *: {genre.get('genre_id')}")
        expected_markers = EXPECTED_GENRE_MARKERS[genre["genre_id"]]
        if set(genre.get("allowed_markers", [])) != expected_markers:
            raise ValueError(f"Genre contract mismatch: {genre['genre_id']}")
        elements = genre.get("elements")
        if not isinstance(elements, list) or not elements:
            raise ValueError(f"Genre requires elements: {genre['genre_id']}")
        for element in elements:
            element_id = element.get("element_id")
            if not isinstance(element_id, str) or not element_id:
                raise ValueError(f"Genre element requires an id: {genre['genre_id']}")
            if element_id in element_ids:
                raise ValueError(f"Persistent element has more than one Genre: {element_id}")
            if element.get("marker") not in expected_markers:
                raise ValueError(f"Element marker mismatch: {element_id}")
            if not str(element.get("core_concept", "")).strip():
                raise ValueError(f"Element core concept is missing: {element_id}")
            for tag_id in element.get("tags", []):
                if tag_id not in tag_members:
                    raise ValueError(f"Element has an unknown Blueprint Tag: {element_id}")
                tag_members[tag_id].add(element_id)
            element_ids.add(element_id)
            library_element_ids.add(element_id)
            by_id[element_id] = {
                **element,
                "genre_id": genre["genre_id"],
                "role_id": ROLE_BY_MARKER[element["marker"]],
            }

    missing = sorted(REQUIRED_ELEMENTS.difference(element_ids))
    if missing:
        raise ValueError(f"Required Blueprint elements are missing: {missing}")
    if "reality-check" not in {
        item["element_id"]
        for item in next(
            genre["elements"] for genre in genres if genre["genre_id"] == "infrastructure"
        )
    }:
        raise ValueError("Reality Check must be Infrastructure")
    if by_id["reality-check"].get("implementation_instruction") is not None:
        raise ValueError("Reality Check classification cannot freeze an implementation instruction")
    if protagonist["element_id"] == "project-review":
        raise ValueError("Perspective cannot become a second Protagonist")
    if tag_members["signature-function"] != {
        "curiosity",
        "fatigue",
        "greed",
        "project-review",
    }:
        raise ValueError("Signature Function Tag members are incomplete or unexpected")
    if by_id["steal"].get("activation_contract") != {
        "activated_by": "curiosity",
        "route_kind": "implementation-evidence-acquisition",
        "serves_flow": "find-global-pattern-solutions",
        "hands_off_to_flow": "map-implementations-to-blueprint",
        "cannot_replace": ["project-review", "cause-research", "two-sided-review"],
    }:
        raise ValueError("Adapt must remain Curiosity's bounded donor implementation route")

    if flows_section.get("owner") != "system-review":
        raise ValueError("System Review must own the Flows section")
    if flows_section.get("decision_status") != "primary-user-confirmed":
        raise ValueError("The eight Blueprint Flows require primary-user confirmation")
    flows = flows_section.get("entries")
    if not isinstance(flows, list):
        raise ValueError("Blueprint Flows are missing")
    if [(item.get("flow_id"), item.get("human_name")) for item in flows] != list(
        EXPECTED_FLOWS
    ):
        raise ValueError("Blueprint Flows are incomplete or out of order")
    if [item.get("sequence") for item in flows] != list(range(1, 9)):
        raise ValueError("Blueprint Flows must be explicit and contiguous")
    flow_ids = {item["flow_id"] for item in flows}
    overlap = sorted(flow_ids.intersection(element_ids))
    if overlap:
        raise ValueError(f"Flows cannot also be Blueprint Elements: {overlap}")
    used_elements: set[str] = set()
    for flow in flows:
        references = flow.get("uses_elements")
        if not isinstance(references, list) or not references:
            raise ValueError(f"Flow requires registered Elements: {flow['flow_id']}")
        if len(references) != len(set(references)):
            raise ValueError(f"Flow contains duplicate Element references: {flow['flow_id']}")
        missing_references = sorted(set(references).difference(library_element_ids))
        if missing_references:
            raise ValueError(
                f"Flow references Elements absent from the Library: {missing_references}"
            )
        if not str(flow.get("activation_boundary", "")).strip():
            raise ValueError(f"Flow activation boundary is missing: {flow['flow_id']}")
        if not str(flow.get("output", "")).strip():
            raise ValueError(f"Flow output is missing: {flow['flow_id']}")
        used_elements.update(references)
    unused_elements = sorted(library_element_ids.difference(used_elements))
    if unused_elements:
        raise ValueError(f"Element Library members are unused by every Flow: {unused_elements}")

    rules = change_section.get("modification_rules")
    if not isinstance(rules, dict):
        raise ValueError("Blueprint modification rules are missing")
    if rules.get("addition_priority_markers") != ["^", "¢", "%", "$"]:
        raise ValueError("Blueprint addition priority must be ^ > ¢ > % > $")
    if rules.get("addition_priority") != [
        "extra",
        "prop",
        "principle",
        "deuteragonist",
    ]:
        raise ValueError("Blueprint addition priority types are invalid")
    if rules.get("truthful_classification_required") is not True:
        raise ValueError("Blueprint priority cannot override truthful classification")
    if rules.get("persistent_library_elements_require_exactly_one_genre") is not True:
        raise ValueError("Every persistent Library Element requires exactly one Genre")
    if rules.get("flows_are_elements") is not False or rules.get("flows_are_genres") is not False:
        raise ValueError("Flows cannot become Elements or Genres")
    if rules.get("flow_references_require_registered_elements") is not True:
        raise ValueError("Flows must use only registered Element Library capabilities")
    if rules.get("blueprint_mapping_before_debate") is not True:
        raise ValueError("Blueprint Mapping must occur before debate")
    if rules.get("old_plan_authority") != "superseded":
        raise ValueError("The New Blueprint must override the old plan")

    donor = change_section.get("donor_policy")
    if not isinstance(donor, dict) or donor.get("hermes_is_a_donor") is not True:
        raise ValueError("Hermes must be governed as a donor")
    if donor.get("donor_architecture_has_authority") is not False:
        raise ValueError("Donor architecture cannot receive Fractal authority")
    if donor.get("donor_mutation_authority_may_be_inherited") is not False:
        raise ValueError("Donor mutation authority cannot be inherited")
    if donor.get("unclassified_capability_status") != "quarantined":
        raise ValueError("Unclassified donor capabilities must remain quarantined")
    if donor.get("bulk_import_allowed") is not False:
        raise ValueError("Bulk donor import cannot be permitted")
    if (
        donor.get("donor_set_fixed") is not False
        or donor.get("select_from_current_need") is not True
    ):
        raise ValueError("Donors must vary according to the current Element need")
    if donor.get("local_fork_required") is not True:
        raise ValueError("Reused donor implementation must have a recoverable local fork")
    if donor.get("runtime_upstream_dependency_allowed") is not False:
        raise ValueError("Fractal cannot depend on a donor service remaining online")
    if donor.get("donor_name_becomes_fractal_name") is not False:
        raise ValueError("Donor names cannot become Fractal capability names")

    unclassified = change_section.get("candidate_queue")
    if not isinstance(unclassified, list):
        raise ValueError("Unclassified Blueprint elements require an explicit queue")
    for element in unclassified:
        if element.get("status") != "quarantined" or element.get("persistent") is not False:
            raise ValueError("Unclassified elements cannot become persistent")
        if element.get("element_id") in element_ids:
            raise ValueError("An element cannot be classified and unclassified")
    return value


def load_blueprint() -> dict[str, Any]:
    """Load and validate the packaged candidate Blueprint."""
    path = files("fractal.data").joinpath("blueprint.json")
    return validate_blueprint(json.loads(path.read_text(encoding="utf-8")))


def render_blueprint(value: dict[str, Any] | None = None) -> str:
    """Render a compact Human Control view of the candidate Blueprint."""
    blueprint = validate_blueprint(value) if value is not None else load_blueprint()
    lifecycle = blueprint["lifecycle"]
    library = blueprint["element_library"]
    change_rules = blueprint["blueprint_change_rules"]
    element_names = {
        element["element_id"]: element["human_name"]
        for genre in library["genres"]
        for element in genre["elements"]
    }
    lines = [
        "# Fractal Blueprint",
        "",
        f"- Blueprint Version: `{blueprint['blueprint_version']}`",
        f"- Status: `{lifecycle['status']}`",
        f"- Active State Source: `{lifecycle['active_state_source']}`",
        f"- Implementation Status: `{lifecycle['implementation_status']}`",
        "",
        f"> {lifecycle['claim_boundary']}",
        "",
        "## Core",
        "",
        "```text",
        f"{library['core']['philosophy']['human_name']}    Core Philosophy",
        f"└── # {library['core']['protagonist']['human_name']}    Sole Protagonist",
        "```",
        "",
        "## Section 1 — Element Library",
        "",
        "```text",
    ]
    for genre in library["genres"]:
        lines.append(f"* {genre['human_name']}")
        for index, element in enumerate(genre["elements"]):
            branch = "└──" if index == len(genre["elements"]) - 1 else "├──"
            tags = (
                " [Signature Function]" if "signature-function" in element.get("tags", []) else ""
            )
            lines.append(f"  {branch} {element['marker']} {element['human_name']}{tags}")
    lines.extend(
        [
            "```",
            "",
            "Every persistent Library Element is classified exactly once. "
            "Flows are not stored here.",
            "",
            "## Section 2 — Flows",
            "",
            "Flows are ordered use rules owned by `System Review`; "
            "they are not Genres or Elements.",
            "",
        ]
    )
    for flow in blueprint["flows"]["entries"]:
        used = ", ".join(f"`{element_names[item]}`" for item in flow["uses_elements"])
        lines.extend(
            [
                f"### {flow['sequence']}. {flow['human_name']}",
                "",
                f"- Starts when: {flow['activation_boundary']}",
                f"- Uses: {used}",
                f"- Produces: {flow['output']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Tags",
            "",
            "- `Signature Function` is a cross-Genre Tag, not a Genre.",
            "- Current members: `Fatigue`, `Curiosity`, `Greed`, and `Perspective`.",
        ]
    )
    lines.extend(
        [
            "",
            "## Section 3 — Blueprint Change Rules",
            "",
            "- There is one Protagonist: `System Review`.",
            "- Every persistent Element Library member belongs to exactly one Genre.",
            "- A Flow is neither a Genre nor an Element.",
            "- Every Flow may use only capabilities registered in the Element Library.",
            "- A Deuteragonist preserves its core concept while its implementation may change.",
            "- An Extra stays outside product emphasis and supports an existing `#` or `$`.",
            "- A Prop is a reusable Method and never owns a lifecycle or authority boundary.",
            "- A Principle is asymptotic and cannot be claimed complete.",
            "- Addition priority is `^ → ¢ → % → $`; classification must remain truthful.",
            "- Every Candidate implementation is mapped into the Blueprint before debate.",
            "- The New Blueprint supersedes conflicting architecture claims in the old plan.",
            "",
            "## Donor Boundary",
            "",
            "- Hermes is governed as the first researched donor, not a permanent donor choice.",
            "- Donors are selected separately for the current Element need.",
            "- Reused implementation is forked locally and cannot require the donor "
            "service to stay online.",
            "- Fractal uses its own capability names instead of donor product names.",
            "- Donor implementations may be reused; donor architecture and "
            "mutation authority may not.",
            "- Unclassified donor capabilities remain quarantined.",
            "- Bulk donor import is prohibited.",
            "",
            "## Adapt and Curiosity",
            "",
            "- `Curiosity` decides when implementation evidence beyond the current "
            "method is needed.",
            "- `Adapt` is that Value's bounded donor-facing acquisition and adaptation route.",
            "- `Adapt` serves Flow 5, hands every Candidate to Flow 6, and cannot replace "
            "`Perspective`, `Cause Research`, or `Two-Sided Review`.",
        ]
    )
    if change_rules["candidate_queue"]:
        lines.extend(["", "## Unclassified Elements", ""])
        for element in change_rules["candidate_queue"]:
            lines.append(
                f"- `{element['element_id']}` — `{element['status']}`: {element['open_question']}"
            )
    if blueprint["open_questions"]:
        lines.extend(["", "## Open Questions", ""])
        for question in blueprint["open_questions"]:
            lines.append(f"- `{question['question_id']}` — {question['summary']}")
    return "\n".join(lines) + "\n"
