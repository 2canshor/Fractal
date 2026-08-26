"""Deterministic Apple-principles acceptance for persistent Fractal work.

This module is deliberately an acceptance boundary, not a lifecycle or a new
Fractal core.  It validates the retained Apple HIG source registry and checks
that one persistent responsibility has explicit evidence for every universal
principle, requirement, and source-applicability decision.  Continuous
Improvement remains the governing core and must have an evidenced Project
Review -> System Review path in every accepted alignment.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REGISTRY_RECORD_TYPE = "apple-principles-registry"
REGISTRY_RECORD_VERSION = 1
ALIGNMENT_RECORD_TYPE = "apple-responsibility-alignment"
ALIGNMENT_RECORD_VERSION = 2
VERSION_ACCEPTANCE_RECORD_TYPE = "apple-system-version-acceptance"
VERSION_ACCEPTANCE_RECORD_VERSION = 1

EXPECTED_SOURCE_COUNT = 171
EXPECTED_INDEX_UPDATED = "2026-08-21"
EXPECTED_INDEX_SHA256 = "94b10ebc13cbb5dd7542487e8a232b315c191b6fc2e801f15cec5081c502d1d1"
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "40308dfd08e1c7ad3acdf05b659463b1984c69f7dbff96d2c752de0f169bec1c"
)
EXPECTED_APPLICABILITY_COUNTS = {
    "universal": 12,
    "conditional": 109,
    "not-current": 50,
}
EXPECTED_VARIANT_COUNTS = {"current-(2)": 143, "indexed-only": 28}
EXPECTED_PRINCIPLE_IDS = (
    "purpose",
    "agency",
    "responsibility",
    "familiarity",
    "flexibility",
    "simplicity",
    "craft",
    "delight",
)
EXPECTED_REQUIREMENT_IDS = (
    "generative-ai",
    "accessibility",
    "inclusion",
    "privacy",
    "writing",
    "feedback",
    "undo-and-recovery",
    "loading-and-progress",
    "offering-help",
    "continuous-improvement-core",
)
EXPECTED_UNIVERSAL_SOURCE_IDS = frozenset(
    {
        "hig:accessibility",
        "hig:design-principles",
        "hig:feedback",
        "hig:foundations",
        "hig:generative-ai",
        "hig:inclusion",
        "hig:loading",
        "hig:offering-help",
        "hig:privacy",
        "hig:status",
        "hig:undo-and-redo",
        "hig:writing",
    }
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SOURCE_ID_RE = re.compile(r"^hig:[a-z0-9]+(?:-[a-z0-9]+)*$")
_OFFICIAL_HIG_PREFIX = "https://developer.apple.com/design/human-interface-guidelines/"


class AppleAlignmentError(ValueError):
    """Raised when a source registry or responsibility alignment is incomplete."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AppleAlignmentError("Apple alignment values must be portable JSON") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AppleAlignmentError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise AppleAlignmentError(f"{label} must be an ordered list")
    return copy.deepcopy(list(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppleAlignmentError(f"{label} must be a non-empty string")
    return value


def _evidence(value: Any, label: str, *, required: bool) -> list[str]:
    items = _list(value, label)
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(items)]
    if len(result) != len(set(result)):
        raise AppleAlignmentError(f"{label} must not contain duplicates")
    if required and not result:
        raise AppleAlignmentError(f"{label} must contain direct evidence")
    if not required and result:
        raise AppleAlignmentError(f"{label} must be empty when status is not-applicable")
    return result


def _indexed(records: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_list(records, label)):
        record = _mapping(raw, f"{label}[{index}]")
        identifier = _text(record.get(key), f"{label}[{index}].{key}")
        if identifier in result:
            raise AppleAlignmentError(f"{label} contains duplicate {key}: {identifier}")
        result[identifier] = record
    return result


def load_apple_principles_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the packaged registry without weakening any validation gate."""

    registry_path = (
        Path(path)
        if path is not None
        else Path(__file__).with_name("data") / "apple-principles-registry.json"
    )
    try:
        value = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppleAlignmentError(
            f"cannot load Apple principles registry: {registry_path}"
        ) from error
    return _mapping(value, "registry")


def _validate_portable_label(value: Any, label: str) -> str:
    text = _text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "Apple":
        raise AppleAlignmentError(f"{label} must be a portable Apple-relative label")
    return text


def validate_apple_principles_registry(
    registry: Mapping[str, Any],
    *,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the complete 171-page retained HIG registry.

    ``source_root`` may point at the local ``Apple`` guide directory.  When it
    is supplied, every selected file is also read and checked against its
    retained SHA-256.  The portable manifest, URL, classification, and hash
    gates are always enforced.
    """

    value = _mapping(registry, "registry")
    if value.get("record_type") != REGISTRY_RECORD_TYPE:
        raise AppleAlignmentError("registry.record_type is not apple-principles-registry")
    if value.get("record_version") != REGISTRY_RECORD_VERSION:
        raise AppleAlignmentError("registry.record_version is unsupported")
    if value.get("continuous_improvement_is_core") is not True:
        raise AppleAlignmentError("Continuous Improvement must remain the registry core")
    if value.get("acceptance_layer_only") is not True:
        raise AppleAlignmentError("Apple alignment must remain an acceptance layer only")

    library = _mapping(value.get("library"), "registry.library")
    if library.get("hig_source_count") != EXPECTED_SOURCE_COUNT:
        raise AppleAlignmentError("registry.library.hig_source_count must be 171")
    if library.get("index_version") != 2:
        raise AppleAlignmentError("registry.library.index_version must be 2")
    if library.get("index_updated") != EXPECTED_INDEX_UPDATED:
        raise AppleAlignmentError("registry.library.index_updated changed")
    _validate_portable_label(library.get("index_label"), "registry.library.index_label")
    if library.get("index_sha256") != EXPECTED_INDEX_SHA256:
        raise AppleAlignmentError("registry.library.index_sha256 changed")
    if library.get("applicability_counts") != EXPECTED_APPLICABILITY_COUNTS:
        raise AppleAlignmentError("registry.library.applicability_counts changed")
    if library.get("source_variant_counts") != EXPECTED_VARIANT_COUNTS:
        raise AppleAlignmentError("registry.library.source_variant_counts changed")

    sources = _indexed(value.get("sources"), "source_id", "registry.sources")
    if len(sources) != EXPECTED_SOURCE_COUNT:
        raise AppleAlignmentError("registry must contain exactly 171 HIG sources")
    titles: set[str] = set()
    urls: set[str] = set()
    labels: set[str] = set()
    applicability_counts: Counter[str] = Counter()
    variant_counts: Counter[str] = Counter()
    root = Path(source_root).resolve() if source_root is not None else None

    for source_id, source in sources.items():
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise AppleAlignmentError(f"invalid HIG source_id: {source_id}")
        title = _text(source.get("title"), f"{source_id}.title")
        if title in titles:
            raise AppleAlignmentError(f"duplicate HIG title: {title}")
        titles.add(title)
        _text(source.get("category"), f"{source_id}.category")
        indexed_label = _validate_portable_label(
            source.get("indexed_label"), f"{source_id}.indexed_label"
        )
        source_label = _validate_portable_label(
            source.get("source_label"), f"{source_id}.source_label"
        )
        if source_label in labels:
            raise AppleAlignmentError(f"duplicate selected HIG source label: {source_label}")
        labels.add(source_label)
        variant = _text(source.get("source_variant"), f"{source_id}.source_variant")
        if variant not in EXPECTED_VARIANT_COUNTS:
            raise AppleAlignmentError(f"unsupported source variant for {source_id}: {variant}")
        variant_counts[variant] += 1
        if variant == "indexed-only" and source_label != indexed_label:
            raise AppleAlignmentError(f"indexed-only source must use indexed_label: {source_id}")
        if variant == "current-(2)":
            indexed_path = Path(indexed_label)
            expected_current = indexed_path.with_name(f"{indexed_path.stem} (2).txt").as_posix()
            if source_label != expected_current:
                raise AppleAlignmentError(
                    f"current-(2) source must select its exact (2) file: {source_id}"
                )

        official_url = _text(source.get("official_url"), f"{source_id}.official_url")
        if not official_url.startswith(_OFFICIAL_HIG_PREFIX) or official_url in urls:
            raise AppleAlignmentError(f"HIG URL is not unique and official: {source_id}")
        if source_id != f"hig:{official_url.removeprefix(_OFFICIAL_HIG_PREFIX)}":
            raise AppleAlignmentError(f"HIG source_id does not match its official URL: {source_id}")
        urls.add(official_url)
        sha256 = _text(source.get("sha256"), f"{source_id}.sha256")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise AppleAlignmentError(f"invalid source SHA-256: {source_id}")
        applicability = _text(source.get("applicability"), f"{source_id}.applicability")
        if applicability not in EXPECTED_APPLICABILITY_COUNTS:
            raise AppleAlignmentError(f"unsupported applicability for {source_id}: {applicability}")
        applicability_counts[applicability] += 1
        _text(source.get("applicability_rule"), f"{source_id}.applicability_rule")

        if root is not None:
            relative = Path(*Path(source_label).parts[1:])
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                raise AppleAlignmentError(f"source escapes the Apple guide root: {source_id}")
            try:
                observed = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError as error:
                raise AppleAlignmentError(
                    f"cannot read retained HIG source: {source_id}"
                ) from error
            if observed != sha256:
                raise AppleAlignmentError(f"retained HIG source hash changed: {source_id}")

    if dict(applicability_counts) != EXPECTED_APPLICABILITY_COUNTS:
        raise AppleAlignmentError(
            f"HIG applicability counts changed: {dict(applicability_counts)}"
        )
    if dict(variant_counts) != EXPECTED_VARIANT_COUNTS:
        raise AppleAlignmentError(f"HIG source variant counts changed: {dict(variant_counts)}")
    universal_ids = {
        source_id for source_id, source in sources.items() if source["applicability"] == "universal"
    }
    if universal_ids != EXPECTED_UNIVERSAL_SOURCE_IDS:
        raise AppleAlignmentError("the exact 12-source universal HIG set changed")
    manifest_sha256 = _text(
        library.get("source_manifest_sha256"), "registry.library.source_manifest_sha256"
    )
    if (
        _SHA256_RE.fullmatch(manifest_sha256) is None
        or manifest_sha256 != EXPECTED_SOURCE_MANIFEST_SHA256
        or manifest_sha256 != _digest(list(sources.values()))
    ):
        raise AppleAlignmentError("registry source manifest digest does not match its 171 records")

    principles = _indexed(value.get("principles"), "principle_id", "registry.principles")
    if tuple(principles) != EXPECTED_PRINCIPLE_IDS:
        raise AppleAlignmentError(
            "registry must contain the eight Apple principles in canonical order"
        )
    covered_universal_sources: set[str] = set()
    for principle_id, principle in principles.items():
        _text(principle.get("title"), f"principle.{principle_id}.title")
        _text(principle.get("acceptance"), f"principle.{principle_id}.acceptance")
        _text(
            principle.get("continuous_improvement_reinforcement"),
            f"principle.{principle_id}.continuous_improvement_reinforcement",
        )
        source_refs = _list(principle.get("source_refs"), f"principle.{principle_id}.source_refs")
        if "hig:design-principles" not in source_refs:
            raise AppleAlignmentError(f"principle {principle_id} must cite Design principles")
        for source_ref in source_refs:
            if source_ref not in sources:
                raise AppleAlignmentError(f"unknown principle source_ref: {source_ref}")
            if sources[source_ref]["applicability"] != "universal":
                raise AppleAlignmentError(f"principle cites non-universal source: {source_ref}")
            covered_universal_sources.add(source_ref)

    requirements = _indexed(
        value.get("cross_cutting_requirements"),
        "requirement_id",
        "registry.cross_cutting_requirements",
    )
    if tuple(requirements) != EXPECTED_REQUIREMENT_IDS:
        raise AppleAlignmentError("registry cross-cutting requirements are incomplete or reordered")
    for requirement_id, requirement in requirements.items():
        _text(requirement.get("title"), f"requirement.{requirement_id}.title")
        _text(requirement.get("acceptance"), f"requirement.{requirement_id}.acceptance")
        _text(
            requirement.get("continuous_improvement_reinforcement"),
            f"requirement.{requirement_id}.continuous_improvement_reinforcement",
        )
        for source_ref in _list(
            requirement.get("source_refs"), f"requirement.{requirement_id}.source_refs"
        ):
            if source_ref not in sources:
                raise AppleAlignmentError(f"unknown requirement source_ref: {source_ref}")
            if sources[source_ref]["applicability"] != "universal":
                raise AppleAlignmentError(f"requirement cites non-universal source: {source_ref}")
            covered_universal_sources.add(source_ref)
    if requirements["generative-ai"].get("must_reinforce_continuous_improvement") is not True:
        raise AppleAlignmentError("Generative AI must reinforce Continuous Improvement")
    if covered_universal_sources != EXPECTED_UNIVERSAL_SOURCE_IDS:
        raise AppleAlignmentError("not every universal HIG source has a maintained acceptance rule")
    return value


def _validate_passed_alignment(record: dict[str, Any], label: str) -> None:
    if record.get("status") != "passed":
        raise AppleAlignmentError(f"{label}.status must be passed")
    _evidence(record.get("evidence_ids"), f"{label}.evidence_ids", required=True)


def validate_responsibility_alignment(
    alignment: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    require_human_qualitative_acceptance: bool = True,
) -> dict[str, Any]:
    """Fail closed unless one responsibility addresses all 171 HIG pages.

    The alignment stores one manifest reference and three grouped applicability
    decisions rather than copying 171 Source rows into every responsibility.
    The validator expands those groups against the registry and still proves
    exact set equality. Triggered conditional pages need per-Source evidence;
    all other conditional pages use one explicit ``all-except-triggered``
    decision. Delight additionally needs direct human qualitative acceptance.
    """

    source_registry = validate_apple_principles_registry(registry)
    sources = _indexed(source_registry["sources"], "source_id", "registry.sources")
    value = _mapping(alignment, "alignment")
    if value.get("record_type") != ALIGNMENT_RECORD_TYPE:
        raise AppleAlignmentError("alignment.record_type is not apple-responsibility-alignment")
    if value.get("record_version") != ALIGNMENT_RECORD_VERSION:
        raise AppleAlignmentError("alignment.record_version is unsupported")
    _text(value.get("responsibility_id"), "alignment.responsibility_id")

    improvement = _mapping(value.get("continuous_improvement"), "alignment.continuous_improvement")
    if improvement.get("is_core") is not True:
        raise AppleAlignmentError(
            "Continuous Improvement must remain core for every responsibility"
        )
    _validate_passed_alignment(improvement, "alignment.continuous_improvement")
    path = _list(improvement.get("review_path"), "alignment.continuous_improvement.review_path")
    if path != ["project-review", "system-review"]:
        raise AppleAlignmentError(
            "Continuous Improvement review_path must be project-review then system-review"
        )

    principle_alignment = _indexed(
        value.get("principle_alignment"), "principle_id", "alignment.principle_alignment"
    )
    if tuple(principle_alignment) != EXPECTED_PRINCIPLE_IDS:
        raise AppleAlignmentError("alignment must address all eight Apple principles in order")
    for principle_id, record in principle_alignment.items():
        if record.get("applicability") != "universal" or record.get("triggered") is not True:
            raise AppleAlignmentError(f"principle {principle_id} must be universally triggered")
        _validate_passed_alignment(record, f"alignment.principle_alignment.{principle_id}")
    delight = principle_alignment["delight"]
    human_acceptance = delight.get("human_qualitative_acceptance")
    if human_acceptance is True:
        _evidence(
            delight.get("human_qualitative_evidence_ids"),
            "alignment.principle_alignment.delight.human_qualitative_evidence_ids",
            required=True,
        )
    elif not require_human_qualitative_acceptance and human_acceptance == "pending":
        _evidence(
            delight.get("human_qualitative_evidence_ids"),
            "alignment.principle_alignment.delight.human_qualitative_evidence_ids",
            required=False,
        )
        _evidence(
            delight.get("observable_proxy_evidence_ids"),
            "alignment.principle_alignment.delight.observable_proxy_evidence_ids",
            required=True,
        )
    else:
        raise AppleAlignmentError("Delight requires human qualitative acceptance")

    requirement_alignment = _indexed(
        value.get("requirement_alignment"),
        "requirement_id",
        "alignment.requirement_alignment",
    )
    if tuple(requirement_alignment) != EXPECTED_REQUIREMENT_IDS:
        raise AppleAlignmentError("alignment must address every cross-cutting requirement in order")
    for requirement_id, record in requirement_alignment.items():
        if record.get("applicability") != "universal" or record.get("triggered") is not True:
            raise AppleAlignmentError(f"requirement {requirement_id} must be universally triggered")
        _validate_passed_alignment(record, f"alignment.requirement_alignment.{requirement_id}")

    source_applicability = _mapping(
        value.get("source_applicability"), "alignment.source_applicability"
    )
    if set(source_applicability) != {
        "registry_manifest_sha256",
        "universal",
        "conditional",
        "not_current",
    }:
        raise AppleAlignmentError("alignment source applicability groups are incomplete")
    expected_manifest = source_registry["library"]["source_manifest_sha256"]
    if source_applicability.get("registry_manifest_sha256") != expected_manifest:
        raise AppleAlignmentError("alignment is bound to a different HIG source manifest")

    source_ids_by_class = {
        applicability: {
            source_id
            for source_id, source in sources.items()
            if source["applicability"] == applicability
        }
        for applicability in EXPECTED_APPLICABILITY_COUNTS
    }
    universal = _mapping(source_applicability.get("universal"), "source_applicability.universal")
    if set(universal) != {"selection", "triggered", "status", "evidence_ids"}:
        raise AppleAlignmentError("universal HIG source group fields are incomplete")
    if universal.get("selection") != "all" or universal.get("triggered") is not True:
        raise AppleAlignmentError("all universal HIG sources must be triggered")
    _validate_passed_alignment(universal, "alignment.source_applicability.universal")

    conditional = _mapping(
        source_applicability.get("conditional"), "source_applicability.conditional"
    )
    if set(conditional) != {
        "triggered_source_ids",
        "triggered_evidence",
        "not_triggered_selection",
        "not_triggered_reason",
    }:
        raise AppleAlignmentError("conditional HIG source group fields are incomplete")
    triggered_ids = _list(
        conditional.get("triggered_source_ids"),
        "alignment.source_applicability.conditional.triggered_source_ids",
    )
    if len(triggered_ids) != len(set(triggered_ids)):
        raise AppleAlignmentError("conditional HIG triggered source ids must be unique")
    triggered_set = set(triggered_ids)
    if not triggered_set <= source_ids_by_class["conditional"]:
        raise AppleAlignmentError("conditional HIG trigger includes a non-conditional source")
    triggered_evidence = _mapping(
        conditional.get("triggered_evidence"),
        "alignment.source_applicability.conditional.triggered_evidence",
    )
    if set(triggered_evidence) != triggered_set:
        raise AppleAlignmentError("conditional HIG triggered evidence must match exact triggers")
    for source_id in triggered_ids:
        _evidence(
            triggered_evidence[source_id],
            f"alignment.source_applicability.conditional.triggered_evidence.{source_id}",
            required=True,
        )
    if conditional.get("not_triggered_selection") != "all-except-triggered":
        raise AppleAlignmentError("conditional HIG N/A coverage must be all-except-triggered")
    _text(
        conditional.get("not_triggered_reason"),
        "alignment.source_applicability.conditional.not_triggered_reason",
    )
    # Expanding the compact selection is the completeness proof. This remains
    # explicit even when every current conditional source is N/A.
    not_triggered = source_ids_by_class["conditional"].difference(triggered_set)
    if triggered_set | not_triggered != source_ids_by_class["conditional"]:
        raise AppleAlignmentError("conditional HIG grouped coverage is incomplete")

    not_current = _mapping(
        source_applicability.get("not_current"), "source_applicability.not_current"
    )
    if set(not_current) != {"selection", "triggered", "status", "reason", "evidence_ids"}:
        raise AppleAlignmentError("not-current HIG source group fields are incomplete")
    if not_current.get("selection") != "all" or not_current.get("triggered") is not False:
        raise AppleAlignmentError("not-current HIG sources cannot be triggered")
    if not_current.get("status") != "not-applicable":
        raise AppleAlignmentError("not-current HIG source group must be not-applicable")
    _text(not_current.get("reason"), "alignment.source_applicability.not_current.reason")
    _evidence(
        not_current.get("evidence_ids"),
        "alignment.source_applicability.not_current.evidence_ids",
        required=False,
    )
    covered_sources = (
        source_ids_by_class["universal"]
        | source_ids_by_class["conditional"]
        | source_ids_by_class["not-current"]
    )
    if len(covered_sources) != EXPECTED_SOURCE_COUNT:
        raise AppleAlignmentError("grouped HIG applicability does not cover all 171 sources")
    return value


def validate_apple_version_acceptance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Require deterministic alignment plus explicit primary-user Delight acceptance.

    This receipt is a build input, not activation or publication authority. The
    later version lifecycle must still prove the exact active pointer, fresh
    session, trusted Hook route, remote acknowledgement, and restore path.
    """

    receipt = _mapping(value, "Apple version acceptance")
    required = {
        "record_type",
        "record_version",
        "apple_registry",
        "responsibilities",
        "components",
        "user_surface",
        "continuous_improvement",
        "human_delight",
        "authority_scope",
        "evidence_ids",
        "receipt_sha256",
    }
    if set(receipt) != required:
        raise AppleAlignmentError("Apple version acceptance fields are incomplete or unexpected")
    if (
        receipt["record_type"] != VERSION_ACCEPTANCE_RECORD_TYPE
        or receipt["record_version"] != VERSION_ACCEPTANCE_RECORD_VERSION
    ):
        raise AppleAlignmentError("Apple version acceptance record contract is invalid")
    supplied_digest = receipt["receipt_sha256"]
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if not isinstance(supplied_digest, str) or supplied_digest != _digest(unsigned):
        raise AppleAlignmentError("Apple version acceptance receipt integrity failure")

    registry = _mapping(receipt["apple_registry"], "Apple version acceptance.registry")
    if registry != {
        "source_count": EXPECTED_SOURCE_COUNT,
        "source_manifest_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
        "index_sha256": EXPECTED_INDEX_SHA256,
    }:
        raise AppleAlignmentError("Apple version acceptance is not bound to the exact HIG registry")
    responsibilities = _mapping(
        receipt["responsibilities"], "Apple version acceptance.responsibilities"
    )
    expected_responsibilities = [f"RESP-{index:02d}" for index in range(20)]
    if responsibilities != {
        "count": 20,
        "responsibility_ids": expected_responsibilities,
        "deterministic_alignment_passed": True,
        "active_live_claimed": False,
    }:
        raise AppleAlignmentError(
            "Apple responsibility acceptance is incomplete or overclaims live proof"
        )
    components = _mapping(receipt["components"], "Apple version acceptance.components")
    if (
        not isinstance(components.get("count"), int)
        or components["count"] < 1
        or components.get("deterministic_audit_passed") is not True
        or components.get("human_acceptance_was_pending_before_this_receipt") is not True
        or _SHA256_RE.fullmatch(str(components.get("audit_sha256", ""))) is None
    ):
        raise AppleAlignmentError("Apple component acceptance is incomplete")
    user_surface = _mapping(receipt["user_surface"], "Apple version acceptance.user_surface")
    if (
        user_surface.get("deterministic_audit_clean") is not True
        or user_surface.get("human_acceptance_was_pending_before_this_receipt") is not True
        or _SHA256_RE.fullmatch(str(user_surface.get("audit_sha256", ""))) is None
    ):
        raise AppleAlignmentError("Apple user-surface acceptance is incomplete")
    improvement = _mapping(
        receipt["continuous_improvement"], "Apple version acceptance.continuous_improvement"
    )
    if improvement != {
        "core": "continuous-improvement",
        "sole_protagonist": "system-review",
        "parallel_lifecycle": False,
    }:
        raise AppleAlignmentError("Apple acceptance must reinforce the existing improvement core")
    delight = _mapping(receipt["human_delight"], "Apple version acceptance.human_delight")
    if (
        delight.get("status") != "accepted"
        or delight.get("accepted_by") != "primary-user"
        or delight.get("scope") != "exact-version-batch"
    ):
        raise AppleAlignmentError("Human Delight requires exact primary-user acceptance")
    _evidence(
        delight.get("evidence_ids"),
        "Apple version acceptance.human_delight.evidence_ids",
        required=True,
    )
    authority = _mapping(receipt["authority_scope"], "Apple version acceptance.authority_scope")
    _text(authority.get("project_id"), "Apple version acceptance.authority_scope.project_id")
    if not isinstance(authority.get("project_revision"), int) or authority["project_revision"] < 0:
        raise AppleAlignmentError("Apple version acceptance requires a Project revision")
    _text(
        authority.get("decision_batch_id"),
        "Apple version acceptance.authority_scope.decision_batch_id",
    )
    _evidence(
        receipt["evidence_ids"], "Apple version acceptance.evidence_ids", required=True
    )
    return receipt
