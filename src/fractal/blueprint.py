"""Load, validate and render the candidate Fractal Blueprint."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

EXPECTED_GENRE_MARKERS = {
    "steps": {"$"},
    "values": {"$"},
    "principles": {"%"},
    "infrastructure": {"^"},
    "methods": {"$", "¢"},
}
ROLE_BY_MARKER = {"$": "deuteragonist", "^": "extra", "¢": "prop", "%": "principle"}

REQUIRED_ELEMENTS = {
    "system-review",
    "find-problems",
    "find-local-patterns",
    "find-global-patterns",
    "find-global-pattern-reasons",
    "find-global-pattern-solutions",
    "map-implementations-to-blueprint",
    "debate-global-pattern-solutions",
    "present-decisions-one-by-one",
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
    if not isinstance(lifecycle, dict) or lifecycle.get("status") != "candidate":
        raise ValueError("The New Blueprint must remain an explicit candidate")
    if lifecycle.get("active") is not False:
        raise ValueError("A Blueprint candidate cannot claim active status")
    core = value.get("core")
    if not isinstance(core, dict):
        raise ValueError("Blueprint core is missing")
    if core.get("philosophy", {}).get("element_id") != "continuous-improvement":
        raise ValueError("Continuous Improvement must remain the core philosophy")
    protagonist = core.get("protagonist")
    if not isinstance(protagonist, dict) or protagonist.get("element_id") != "system-review":
        raise ValueError("System Review must be the sole Protagonist")
    if protagonist.get("marker") != "#":
        raise ValueError("The Protagonist marker must be #")

    tag_definitions = value.get("tag_definitions")
    if not isinstance(tag_definitions, list):
        raise ValueError("Blueprint Tag definitions are missing")
    tag_ids = [tag.get("tag_id") for tag in tag_definitions]
    if tag_ids != ["signature-function"]:
        raise ValueError("Signature Function must be a single defined Tag")
    if tag_definitions[0].get("cross_genre") is not True:
        raise ValueError("Signature Function must remain a cross-Genre Tag")

    role_definitions = value.get("role_definitions")
    if not isinstance(role_definitions, list):
        raise ValueError("Blueprint Role definitions are missing")
    observed_roles = {item.get("marker"): item.get("role_id") for item in role_definitions}
    if observed_roles != ROLE_BY_MARKER:
        raise ValueError("Blueprint Roles and markers are incomplete or unexpected")

    genres = value.get("genres")
    if not isinstance(genres, list):
        raise ValueError("Blueprint Genres are missing")
    genre_ids = [genre.get("genre_id") for genre in genres if isinstance(genre, dict)]
    if len(genre_ids) != len(set(genre_ids)):
        raise ValueError("Blueprint Genre ids must be unique")
    if set(genre_ids) != set(EXPECTED_GENRE_MARKERS):
        raise ValueError("Blueprint Genres are incomplete or unexpected")

    element_ids: set[str] = {"system-review"}
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
        raise ValueError("Project Review cannot become a second Protagonist")
    if tag_members["signature-function"] != {
        "curiosity",
        "fatigue",
        "greed",
        "project-review",
    }:
        raise ValueError("Signature Function Tag members are incomplete or unexpected")

    steps_genre = next(genre for genre in genres if genre["genre_id"] == "steps")
    if steps_genre.get("decision_status") != "primary-user-confirmed":
        raise ValueError("The eight Blueprint Steps require primary-user confirmation")
    steps = steps_genre["elements"]
    if [item.get("sequence") for item in steps] != list(range(1, len(steps) + 1)):
        raise ValueError("Blueprint Steps must be explicit and contiguous")
    if "reality-check" in {item["element_id"] for item in steps}:
        raise ValueError("Reality Check cannot remain a System Review Step")

    rules = value.get("modification_rules")
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
    if rules.get("blueprint_mapping_before_debate") is not True:
        raise ValueError("Blueprint Mapping must occur before debate")
    if rules.get("old_plan_authority") != "superseded":
        raise ValueError("The New Blueprint must override the old plan")

    donor = value.get("donor_policy")
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

    unclassified = value.get("unclassified_elements")
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
    lines = [
        "# Fractal Blueprint Candidate",
        "",
        f"- Blueprint Version: `{blueprint['blueprint_version']}`",
        f"- Status: `{lifecycle['status']}`",
        f"- Active: `{str(lifecycle['active']).lower()}`",
        f"- Implementation Status: `{lifecycle['implementation_status']}`",
        "",
        f"> {lifecycle['claim_boundary']}",
        "",
        "## Architecture",
        "",
        "```text",
        f"{blueprint['core']['philosophy']['human_name']}    Core Philosophy",
        f"└── # {blueprint['core']['protagonist']['human_name']}    Sole Protagonist",
    ]
    for genre in blueprint["genres"]:
        lines.append(f"    ├── * {genre['human_name']}")
        for index, element in enumerate(genre["elements"]):
            branch = "└──" if index == len(genre["elements"]) - 1 else "├──"
            sequence = f"{element['sequence']}. " if "sequence" in element else ""
            tags = (
                " [Signature Function]" if "signature-function" in element.get("tags", []) else ""
            )
            lines.append(
                f"    │   {branch} {element['marker']} {sequence}{element['human_name']}{tags}"
            )
    lines.extend(
        [
            "```",
            "",
            "## Tags",
            "",
            "- `Signature Function` is a cross-Genre Tag, not a Genre.",
            "- Current members: `Fatigue`, `Curiosity`, `Greed`, and `Perspective`.",
            "",
            "## Modification Rules",
            "",
            "- There is one Protagonist: `System Review`.",
            "- Every persistent element belongs to exactly one Genre.",
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
            "- Hermes is a donor as well as a possible environment.",
            "- Donor implementations may be reused; donor architecture and "
            "mutation authority may not.",
            "- Unclassified donor capabilities remain quarantined.",
            "- Bulk donor import is prohibited.",
        ]
    )
    if blueprint["unclassified_elements"]:
        lines.extend(["", "## Unclassified Elements", ""])
        for element in blueprint["unclassified_elements"]:
            lines.append(
                f"- `{element['element_id']}` — `{element['status']}`: {element['open_question']}"
            )
    if blueprint["open_questions"]:
        lines.extend(["", "## Open Questions", ""])
        for question in blueprint["open_questions"]:
            lines.append(f"- `{question['question_id']}` — {question['summary']}")
    return "\n".join(lines) + "\n"
