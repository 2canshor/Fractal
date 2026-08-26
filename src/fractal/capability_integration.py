# ruff: noqa: E501

"""Pure comparison and integration for portable capability responsibilities.

Genesis extraction is intentionally kept outside this module.  The extraction
step may describe a responsibility, its portable evidence, and the Source
observations that support it; this module only compares those descriptions and
builds inactive Candidate Dots.  In particular, this module does not intake a
Source, read or write Workplace state, create an Action or Workflow, execute a
provider, or activate a Dot.

There are two useful boundaries in the implementation:

* :func:`compare_responsibilities` produces a stable, reviewable comparison
  record.  Identity/normalisation is decided before semantic relationships.
* :func:`integrate_capabilities` applies those records to deterministic groups
  and returns Candidate Dots together with their comparison evidence.

The functions accept ordinary mappings rather than an extraction-engine type.
That is deliberate: a concurrent extractor can pass a small portable
``responsibility``/``evidence`` mapping without this module importing or
depending on the extractor.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fractal.capability_dot import validate_capability_dot
from fractal.capability_source import SourceValidationError, validate_source

INTEGRATION_RECORD_TYPE = "capability-integration"
INTEGRATION_RECORD_VERSION = 1
COMPARISON_RECORD_TYPE = "capability-comparison"
COMPARISON_RECORD_VERSION = 1
RELATIONS = frozenset({"duplicate", "superset", "complementary", "conflicting", "distinct"})


class CapabilityIntegrationError(ValueError):
    """Raised when comparison evidence or candidate synthesis is unsafe."""


IntegrationError = CapabilityIntegrationError
ComparisonError = CapabilityIntegrationError


_FORBIDDEN_SEED_TYPES = frozenset(
    {
        "action",
        "workflow",
        "legacy-action",
        "legacy-workflow",
        "capability-action",
        "capability-workflow",
    }
)
_FORBIDDEN_SEED_KEYS = frozenset(
    {
        "action",
        "workflow",
        "action_id",
        "workflow_id",
        "action_authority",
        "workflow_authority",
    }
)
_RELATION_ALIASES = {
    "same": "duplicate",
    "equivalent": "duplicate",
    "overlap": "superset",
    "contains": "superset",
    "complements": "complementary",
    "conflict": "conflicting",
    "incompatible": "conflicting",
    "separate": "distinct",
}
_TEXT_KEYS = (
    "responsibility",
    "portable_responsibility",
    "responsibility_statement",
    "capability",
    "capability_statement",
    "description",
)
_COMPONENT_KEYS = (
    "responsibility_components",
    "components",
    "coverage",
    "coverage_set",
    "scope_components",
    "covered_responsibilities",
)
_EVIDENCE_KEYS = (
    "evidence_ids",
    "evidence",
    "portable_evidence",
    "deterministic_evidence",
)
_SEMANTIC_EVIDENCE_KEYS = (
    "semantic_evidence",
    "semantic_evidence_ids",
    "meaning_evidence",
)
_PROVENANCE_KEYS = ("provenance", "source_provenance", "lineage", "source_reference")
_RELATION_KEYS = (
    "relationship",
    "relation",
    "comparison",
    "comparison_hint",
    "relationship_hint",
    "relationships",
    "relations",
    "comparison_hints",
    "relation_type",
    "relationship_type",
)
_SPLIT_KEYS = (
    "responsibilities",
    "portable_responsibilities",
    "responsibility_evidence",
    "responsibility_mappings",
    "candidate_responsibilities",
    "split_into",
    "split_responsibilities",
)


def _canonical(value: Any) -> bytes:
    """Encode JSON-safe values without relying on insertion order."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CapabilityIntegrationError("Integration evidence must be portable JSON") from error


def _digest(value: Any, *, prefix: str, length: int = 32) -> str:
    return f"{prefix}{hashlib.sha256(_canonical(value)).hexdigest()[:length]}"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip())
    return ""


def _identity_text(value: Any) -> str:
    """Normalise lexical identity while retaining semantic word order."""

    text = _text(value).casefold()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s*([,;:])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Terminal punctuation is presentation, not responsibility identity.
    text = re.sub(r"[.!?]+$", "", text).strip()
    return text


def _sentence(value: Any, *, fallback: str) -> str:
    text = _text(value) or fallback
    if text[-1:] not in ".!?":
        text += "."
    return text


def _name(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("name", "id", "value", "text", "label"):
            if key in value:
                found = _text(value[key])
                if found:
                    return found
        return _text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str))
    return _text(value)


def _normalised_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        values: list[Any] = [value]
    elif isinstance(value, Sequence):
        values = list(value)
    else:
        values = [value]
    result = {_identity_text(_name(item)) for item in values if _name(item)}
    return sorted(result)


def _evidence_items(value: Any) -> list[str]:
    """Convert planned evidence records to stable identifiers/text labels."""

    if value is None:
        return []
    values = [value] if isinstance(value, (str, bytes, bytearray)) else list(value) if isinstance(value, Sequence) else [value]
    result: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            candidate = item.get("evidence_id", item.get("id", item.get("reference")))
            if candidate is None:
                candidate = _digest(dict(item), prefix="evidence-")
            else:
                candidate = _text(candidate)
        else:
            candidate = _text(item)
        if candidate:
            result.add(candidate)
    return sorted(result)


def _safe_copy(value: Any, *, path: str = "$") -> Any:
    """Retain portable provenance while excluding authority-bearing fields."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if key in {"method_ref", "action", "workflow", "action_id", "workflow_id", "source_authority", "action_authority", "workflow_authority"}:
                continue
            if key == "source_id":
                # A Source id is evidence, not Dot authority.  The Dot
                # validator allows it at one provenance boundary, but a
                # portable alias is safer for nested observation records.
                key = "source_reference"
            result[key] = _safe_copy(child, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_copy(child, path=f"{path}[]") for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _unique_records(values: Iterable[Any]) -> list[Any]:
    by_key: dict[str, Any] = {}
    for value in values:
        safe = _safe_copy(value)
        by_key[hashlib.sha256(_canonical(safe)).hexdigest()] = safe
    return [by_key[key] for key in sorted(by_key)]


def _source_from(value: Any) -> dict[str, Any] | None:
    """Validate a supplied Source; never intake or persist it."""

    if not isinstance(value, Mapping):
        return None
    candidate: Any = None
    for key in ("source", "source_record", "capability_source"):
        if key in value:
            candidate = value[key]
            break
    if candidate is None and value.get("record_type") == "capability-source":
        candidate = value
    if candidate is None:
        return None
    if not isinstance(candidate, Mapping):
        raise CapabilityIntegrationError("Source evidence must be a mapping")
    try:
        return validate_source(candidate)
    except (SourceValidationError, TypeError) as error:
        raise CapabilityIntegrationError("Invalid Source evidence supplied to integration") from error


def _provider(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("provider_id", value.get("id", value.get("name")))
    text = _text(value)
    return text or None


def _provider_from(value: Mapping[str, Any], source: Mapping[str, Any] | None) -> str:
    for key in (
        "provider",
        "provider_id",
        "provider_hint",
        "implementation_provider",
        "provider_dependency",
    ):
        if key in value:
            found = _provider(value[key])
            if found:
                return found
    implementations = value.get("implementations")
    if isinstance(implementations, Sequence) and not isinstance(implementations, (str, bytes)):
        providers = sorted(
            {_provider(item.get("provider")) for item in implementations if isinstance(item, Mapping)}
            - {None}
        )
        if providers:
            return providers[0]
    implementation = value.get("implementation")
    if isinstance(implementation, Mapping):
        found = _provider(implementation.get("provider"))
        if found:
            return found
    if source is not None:
        hints = source.get("declarations", {}).get("provider_hints", [])
        providers = sorted(
            {
                _provider(item.get("provider", item.get("name")))
                for item in hints
                if isinstance(item, Mapping)
            }
            - {None}
        )
        if providers:
            return providers[0]
    # This is an implementation provider, not Dot/provider authority.
    return "fractal-candidate"


def _intrinsic_scope(value: Mapping[str, Any], provider: str) -> dict[str, Any] | None:
    raw = value.get("provider_specific")
    if raw is None:
        raw = value.get("intrinsic_provider_responsibility", value.get("intrinsic_provider"))
    if raw is None and isinstance(value.get("provider_dependency"), Mapping):
        dependency = value["provider_dependency"]
        if dependency.get("kind") == "intrinsic" or dependency.get("intrinsic") is True:
            raw = dependency
    if raw is None:
        return None
    if isinstance(raw, bool):
        if not raw:
            return None
        raw = {}
    if not isinstance(raw, Mapping):
        raise CapabilityIntegrationError("Intrinsic provider scope must be a mapping")
    reason = raw.get("intrinsic_provider_responsibility", raw)
    if isinstance(reason, Mapping) and "intrinsic_provider_responsibility" in raw:
        scope = dict(raw)
    else:
        scope = {"provider_id": raw.get("provider_id", provider), "intrinsic_provider_responsibility": reason}
    provider_id = _provider(scope.get("provider_id")) or provider
    detail = scope.get("intrinsic_provider_responsibility")
    if not isinstance(detail, Mapping):
        detail = {"reason_code": _text(detail) or "explicit-intrinsic-provider-boundary"}
    reason_code = _text(detail.get("reason_code", detail.get("reason")))
    evidence_ids = _evidence_items(
        detail.get("evidence_ids", detail.get("evidence_refs", detail.get("evidence")))
    )
    if not reason_code or not evidence_ids:
        raise CapabilityIntegrationError(
            "Intrinsic provider responsibility requires a reason and evidence"
        )
    return {
        "provider_id": provider_id,
        "intrinsic_provider_responsibility": {
            "reason_code": reason_code,
            "evidence_ids": evidence_ids,
        },
    }


def _relationship_entries(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract explicit pair hints without assuming a provider architecture."""

    entries: list[dict[str, Any]] = []
    for key in _RELATION_KEYS:
        raw = value.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            entry: dict[str, Any] = {"relation": raw}
            for combined_key in (
                "merged_responsibility",
                "combined_responsibility",
                "coherent_responsibility",
                "merge_sentence",
                "criteria",
                "criterion",
                "basis",
                "evidence",
                "evidence_ids",
                "semantic_evidence",
                "semantic_evidence_ids",
                "target_id",
                "target",
                "stronger_id",
                "stronger_side",
                "superset_id",
            ):
                if combined_key in value:
                    entry[combined_key] = value[combined_key]
            entries.append(entry)
            continue
        if isinstance(raw, Mapping):
            # A dictionary keyed by target id is a convenient extractor form.
            if not any(name in raw for name in ("relation", "type", "kind", "criteria", "evidence")):
                for target, detail in raw.items():
                    if isinstance(detail, str):
                        detail = {"relation": detail}
                    if isinstance(detail, Mapping):
                        entries.append({**dict(detail), "target_id": detail.get("target_id", target)})
                continue
            entries.append(dict(raw))
            continue
        if isinstance(raw, Sequence):
            for item in raw:
                if isinstance(item, str):
                    entries.append({"relation": item})
                elif isinstance(item, Mapping):
                    entries.append(dict(item))
    return entries


def _mapping_id(value: Mapping[str, Any]) -> str | None:
    for key in ("mapping_id", "responsibility_id", "capability_id", "candidate_id", "id", "ref"):
        found = _text(value.get(key))
        if found:
            return found
    return None


def _source_reference(value: Mapping[str, Any]) -> str | None:
    for key in ("source_id", "source_reference", "source_ref", "source_ids", "source_refs"):
        raw = value.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            for item in raw:
                found = _text(item)
                if found:
                    return found
        else:
            found = _text(raw)
            if found:
                return found
    return None


def _extract_text(value: Mapping[str, Any]) -> str:
    for key in _TEXT_KEYS:
        text = _text(value.get(key))
        if text:
            return text
    return ""


def _extract_components(value: Mapping[str, Any]) -> list[str]:
    components: list[str] = []
    for key in _COMPONENT_KEYS:
        if key in value:
            components.extend(_normalised_list(value[key]))
    return sorted(set(components))


def _provenance_for(value: Mapping[str, Any], source: Mapping[str, Any] | None, source_ref: str | None) -> list[dict[str, Any]]:
    records: list[Any] = []
    for key in _PROVENANCE_KEYS:
        if key in value:
            raw = value[key]
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                records.extend(raw)
            else:
                records.append(raw)
    if source is not None:
        records.append(
            {
                "source_reference": source["source_id"],
                "source_type": source["source_type"],
                "donor_reference": source["donor"]["donor_id"],
                "upstream": source["upstream"],
                "licence": source["licence"],
                "constraints": source["constraints"],
                "observations": source["provenance"],
            }
        )
    elif source_ref:
        records.append({"source_reference": source_ref})
    return _unique_records(records)


def _record_evidence(value: Mapping[str, Any], source: Mapping[str, Any] | None) -> list[str]:
    result: set[str] = set()
    for key in _EVIDENCE_KEYS:
        result.update(_evidence_items(value.get(key)))
    for key in ("verification_evidence", "boundary_evidence", "reuse_evidence", "split_evidence"):
        result.update(_evidence_items(value.get(key)))
    if source is not None:
        result.add(f"source:{source['source_id']}")
        for observation in source.get("provenance", []):
            if observation.get("provenance_id"):
                result.add(f"provenance:{observation['provenance_id']}")
    return sorted(result)


def _semantic_evidence(value: Mapping[str, Any]) -> list[str]:
    result: set[str] = set()
    for key in _SEMANTIC_EVIDENCE_KEYS:
        result.update(_evidence_items(value.get(key)))
    for key in (
        "relationship_evidence",
        "coverage_evidence",
        "merge_evidence",
        "conflict_evidence",
        "coherence_evidence",
        "reuse_evidence",
        "split_evidence",
    ):
        result.update(_evidence_items(value.get(key)))
    return sorted(result)


def _expand_input(value: Any) -> list[Mapping[str, Any]]:
    """Expand explicit responsibility lists, never whole Source/Skill content."""

    if not isinstance(value, Mapping):
        return [{"responsibility": value}]
    record_type = _text(value.get("record_type")).casefold()
    if record_type in _FORBIDDEN_SEED_TYPES or _FORBIDDEN_SEED_KEYS.intersection(value):
        return [value]
    for key in _SPLIT_KEYS:
        raw = value.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            # A list is an explicit extractor boundary.  Parent source and
            # evidence context travel to each portable responsibility.
            result: list[Mapping[str, Any]] = []
            parent = {k: v for k, v in value.items() if k != key}
            split_marker = f"split:{key}:explicit-responsibility-boundary"
            parent.setdefault("split_evidence", [split_marker])
            parent.setdefault("semantic_evidence", [split_marker])
            for index, child in enumerate(raw):
                if isinstance(child, Mapping):
                    result.append({**parent, **dict(child)})
                elif isinstance(child, str):
                    result.append({**parent, "responsibility": child, "mapping_id": f"{_mapping_id(value) or 'responsibility'}-{index + 1}"})
            if result:
                return result
    return [value]


def _normalise_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return _normalise_record({"responsibility": value})
    record_type = _text(value.get("record_type")).casefold()
    if record_type in _FORBIDDEN_SEED_TYPES or _FORBIDDEN_SEED_KEYS.intersection(value):
        return None
    source = _source_from(value)
    # Passing a raw Source to integration is intentionally not a candidate
    # seed.  It must be paired with a portable responsibility mapping.
    if source is not None and not _extract_text(value):
        return None
    responsibility = _extract_text(value)
    if not responsibility:
        return None
    provider = _provider_from(value, source)
    intrinsic = _intrinsic_scope(value, provider)
    if intrinsic is not None and provider == "fractal-candidate":
        provider = intrinsic["provider_id"]
    components = _extract_components(value)
    inputs = _normalised_list(value.get("inputs", value.get("input")))
    outputs = _normalised_list(value.get("outputs", value.get("output")))
    preconditions = _normalised_list(value.get("preconditions", value.get("precondition")))
    side_effects = _normalised_list(value.get("side_effects", value.get("effects")))
    if not inputs:
        inputs = ["bounded input"]
    if not outputs:
        outputs = ["auditable output"]
    if not preconditions:
        preconditions = ["the input is present and within the declared boundary"]
    if not side_effects:
        side_effects = ["emit one bounded output to the caller"]
    signature: dict[str, Any] = {
        "responsibility": _identity_text(responsibility),
        "inputs": inputs,
        "outputs": outputs,
        "preconditions": preconditions,
        "side_effects": side_effects,
        "components": components,
    }
    if intrinsic is not None:
        # Provider identity only enters Dot identity when the responsibility is
        # explicitly intrinsic and evidenced.
        signature["intrinsic_provider"] = {
            "provider_id": intrinsic["provider_id"],
            "reason_code": intrinsic["intrinsic_provider_responsibility"]["reason_code"],
        }
    signature["signature_id"] = _digest(signature, prefix="signature-")
    source_ref = source["source_id"] if source is not None else _source_reference(value)
    evidence = _record_evidence(value, source)
    semantic = _semantic_evidence(value)
    mapping_id = _mapping_id(value)
    record_id_payload = {
        "signature": signature,
        "mapping_id": mapping_id,
        "source_reference": source_ref,
        "provider": provider,
        "evidence": evidence,
    }
    record_id = _digest(record_id_payload, prefix="responsibility-")
    relation_entries = _relationship_entries(value)
    explicit_components = bool(components)
    independent_reuse = value.get("independent_reuse", value.get("independently_reusable", False))
    if not isinstance(independent_reuse, bool):
        independent_reuse = bool(independent_reuse)
    return {
        "record_id": record_id,
        "mapping_id": mapping_id,
        "responsibility": responsibility,
        "signature": signature,
        "provider": provider,
        "intrinsic": intrinsic,
        "components": components,
        "inputs": inputs,
        "outputs": outputs,
        "preconditions": preconditions,
        "side_effects": side_effects,
        "evidence": evidence,
        "semantic_evidence": semantic,
        "provenance": _provenance_for(value, source, source_ref),
        "source": source,
        "source_reference": source_ref,
        "licence": copy.deepcopy(source["licence"]) if source is not None else None,
        "constraints": copy.deepcopy(source["constraints"]) if source is not None else None,
        "relations": relation_entries,
        "independent_reuse": independent_reuse,
        "explicit_components": explicit_components,
        "raw": copy.deepcopy(dict(value)),
    }


def _normalise_records(values: Iterable[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: dict[str, dict[str, Any]] = {}
    ignored: dict[str, dict[str, Any]] = {}
    for value in values:
        for expanded in _expand_input(value):
            normalised = _normalise_record(expanded)
            if normalised is None:
                if isinstance(expanded, Mapping):
                    ignored_id = _digest(_safe_copy(expanded), prefix="ignored-")
                    ignored[ignored_id] = {
                        "record_id": ignored_id,
                        "reason": "forbidden-seed-or-no-portable-responsibility",
                        "record_type": _text(expanded.get("record_type")) or None,
                    }
                continue
            key = normalised["record_id"]
            existing = records.get(key)
            if existing is None:
                records[key] = normalised
                continue
            # Exact repeated extraction output is idempotently collapsed while
            # preserving all independently observed evidence/provenance.
            existing["evidence"] = sorted(set(existing["evidence"]) | set(normalised["evidence"]))
            existing["semantic_evidence"] = sorted(
                set(existing["semantic_evidence"]) | set(normalised["semantic_evidence"])
            )
            existing["provenance"] = _unique_records(existing["provenance"] + normalised["provenance"])
            existing["relations"] = existing["relations"] + normalised["relations"]
    return [records[key] for key in sorted(records)], [ignored[key] for key in sorted(ignored)]


def _attach_source_context(values: Sequence[Any]) -> list[Any]:
    """Attach already-supplied Source records to mappings by stable id.

    This is an in-memory convenience for callers that keep extraction output
    and Source metadata in separate collections.  It is not Source intake: no
    record is written, retrieved, or merged at this boundary.
    """

    source_by_id: dict[str, dict[str, Any]] = {}
    for value in values:
        source = _source_from(value)
        if source is not None:
            source_by_id[source["source_id"]] = source

    def enrich(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        result = dict(value)
        source = _source_from(result)
        if source is None:
            source_ref = _source_reference(result)
            if source_ref in source_by_id:
                result["source"] = copy.deepcopy(source_by_id[source_ref])
        for key in _SPLIT_KEYS:
            children = result.get(key)
            if isinstance(children, Sequence) and not isinstance(children, (str, bytes, bytearray)):
                result[key] = [enrich(child) for child in children]
        return result

    return [enrich(value) for value in values]


def _flatten_collection(value: Any) -> list[Any]:
    """Accept list-like extractor output, including id-keyed dictionaries."""

    if isinstance(value, Mapping):
        if any(
            key in value
            for key in (
                "responsibility",
                "responsibilities",
                "responsibility_evidence",
                "source",
                "record_type",
            )
        ):
            return [value]
        flattened: list[Any] = []
        for key, child in value.items():
            children = child if isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)) else [child]
            for item in children:
                if isinstance(item, Mapping) and not any(
                    name in item for name in ("source_id", "source_reference", "source")
                ):
                    flattened.append({**dict(item), "source_reference": str(key)})
                else:
                    flattened.append(item)
        return flattened
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def normalize_responsibility(value: Any) -> dict[str, Any]:
    """Return the deterministic identity signature for a planned mapping."""

    record = _normalise_record(value)
    if record is None:
        raise CapabilityIntegrationError("A portable responsibility mapping is required")
    return copy.deepcopy(record["signature"])


normalise_responsibility = normalize_responsibility
normalize_signature = normalize_responsibility
normalise_signature = normalize_responsibility


def _relation_name(value: Any) -> str | None:
    text = _text(value).casefold().replace("_", "-")
    text = _RELATION_ALIASES.get(text, text)
    if text == "distinct":
        return "distinct"
    return text if text in RELATIONS else None


def _hint_applies(hint: Mapping[str, Any], left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    targets = [
        _text(hint.get(key)).casefold()
        for key in (
            "target_id",
            "target",
            "right_id",
            "other_id",
            "with_id",
            "left_id",
        )
        if _text(hint.get(key))
    ]
    if not targets:
        return True
    ids = {
        left["record_id"].casefold(),
        right["record_id"].casefold(),
        _text(left.get("mapping_id")).casefold(),
        _text(right.get("mapping_id")).casefold(),
    }
    return bool(set(targets) & ids)


def _explicit_hint(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any] | None:
    hints: list[dict[str, Any]] = []
    for record in (left, right):
        for raw in record.get("relations", []):
            if isinstance(raw, Mapping) and _hint_applies(raw, left, right):
                hints.append(dict(raw))
    if not hints:
        return None
    hints.sort(key=lambda item: hashlib.sha256(_canonical(_safe_copy(item))).hexdigest())
    return hints[0]


def _hint_field(hint: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in hint:
            return hint[key]
    return None


def _stronger_reference(
    hint: Mapping[str, Any] | None,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> str | None:
    """Resolve an explicit superset direction to the stable record id."""

    if hint is None:
        return None
    raw = _hint_field(
        hint,
        "stronger_id",
        "superset_id",
        "covers_id",
        "stronger_record_reference",
        "superset_of",
        "covers",
    )
    text = _text(raw).casefold()
    if text in {
        left["record_id"].casefold(),
        _text(left.get("mapping_id")).casefold(),
    }:
        return left["record_id"]
    if text in {
        right["record_id"].casefold(),
        _text(right.get("mapping_id")).casefold(),
    }:
        return right["record_id"]
    side = _text(_hint_field(hint, "superset_side", "stronger_side")).casefold()
    if side == "left":
        return left["record_id"]
    if side == "right":
        return right["record_id"]
    return None


def _comparison_provenance(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for record, side in ((left, "left"), (right, "right")):
        values.append(
            {
                "side": side,
                "record_reference": record["record_id"],
                "mapping_reference": record.get("mapping_id"),
                "source_reference": record.get("source_reference"),
                "provenance": copy.deepcopy(record.get("provenance", [])),
            }
        )
    return _unique_records(values)


def _semantic_for_relation(
    relation: str,
    hint: Mapping[str, Any] | None,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> list[str]:
    result: set[str] = set(left.get("semantic_evidence", [])) | set(right.get("semantic_evidence", []))
    if hint is not None:
        for key in ("semantic_evidence", "semantic_evidence_ids", "meaning_evidence", "reason", "rationale"):
            raw = hint.get(key)
            if isinstance(raw, str):
                result.add(raw)
            else:
                result.update(_evidence_items(raw))
    if relation == "superset":
        result.update(f"coverage:{component}" for component in sorted(set(left.get("components", [])) | set(right.get("components", []))))
    if relation == "distinct":
        result.add("semantic:responsibility-boundary-remains-separate")
    return sorted(result)


def _classify_relation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    relation: str | None,
    criteria: Sequence[Any] | None,
    evidence: Sequence[Any] | None,
    semantic_evidence: Sequence[Any] | None,
) -> tuple[str, list[str], list[str], list[str], str, float, list[str], str | None]:
    """Return relation plus evidence and optional complementary sentence."""

    # Identity is deliberately first.  Provider identity is absent from both
    # signatures unless an intrinsic-provider gate was explicitly evidenced.
    if left["signature"] == right["signature"]:
        return (
            "duplicate",
            ["criterion:normalized-signature-equal", "criterion:provider-independent-identity"],
            sorted(set(left["evidence"]) | set(right["evidence"])),
            sorted(set(left.get("semantic_evidence", [])) | set(right.get("semantic_evidence", []))),
            "high",
            1.0,
            [],
            None,
        )

    hint = _explicit_hint(left, right)
    requested = _relation_name(relation) or (
        _relation_name(_hint_field(hint or {}, "relation", "relation_type", "type", "kind"))
        if hint
        else None
    )
    chosen = requested
    chosen_criteria = _evidence_items(criteria) if criteria is not None else []
    if hint is not None and not chosen_criteria:
        chosen_criteria = _evidence_items(_hint_field(hint, "criteria", "criterion", "basis"))
    chosen_evidence = _evidence_items(evidence) if evidence is not None else []
    if hint is not None and not chosen_evidence:
        chosen_evidence = _evidence_items(_hint_field(hint, "evidence", "evidence_ids", "deterministic_evidence"))
    combined: str | None = None
    if hint is not None:
        combined = _text(
            _hint_field(
                hint,
                "merged_responsibility",
                "combined_responsibility",
                "coherent_responsibility",
                "merge_sentence",
            )
        ) or None

    if chosen is None:
        left_components = set(left.get("components", []))
        right_components = set(right.get("components", []))
        # Coverage is a structural claim supplied by extraction, not a lexical
        # word-count/size heuristic.  It is safe to infer only from explicit
        # non-empty component sets.
        if left_components and right_components and left_components > right_components:
            chosen = "superset"
            chosen_criteria = ["criterion:explicit-component-coverage"]
            chosen_evidence = ["coverage:left-strict-component-superset"]
        elif left_components and right_components and right_components > left_components:
            chosen = "superset"
            chosen_criteria = ["criterion:explicit-component-coverage"]
            chosen_evidence = ["coverage:right-strict-component-superset"]
        else:
            chosen = "distinct"
            chosen_criteria = ["criterion:normalized-signatures-differ"]
            chosen_evidence = ["boundary:distinct-normalized-signatures"]

    if chosen == "duplicate" and left["signature"] != right["signature"]:
        # A semantic hint may not bypass deterministic duplicate identity.
        # Keep the alternatives separate until an explicit non-duplicate
        # relationship is reviewed.
        chosen = "distinct"
        chosen_criteria = sorted(set(chosen_criteria) | {"criterion:duplicate-identity-mismatch"})
        chosen_evidence = sorted(set(chosen_evidence) | {"boundary:duplicate-identity-mismatch"})
    if chosen not in RELATIONS:
        raise CapabilityIntegrationError(f"Unsupported responsibility relation: {chosen}")
    if not chosen_criteria:
        chosen_criteria = [f"criterion:explicit-relation-{chosen}"]
    if not chosen_evidence:
        chosen_evidence = sorted(set(left.get("evidence", [])) | set(right.get("evidence", [])))
    if not chosen_evidence:
        chosen_evidence = [f"evidence:relation-{chosen}"]
    if semantic_evidence is not None:
        chosen_semantic = _evidence_items(semantic_evidence)
    else:
        chosen_semantic = _semantic_for_relation(chosen, hint, left, right)

    uncertainty: list[str] = []
    if "criterion:duplicate-identity-mismatch" in chosen_criteria:
        uncertainty.append("duplicate-identity-mismatch")
    if chosen != "duplicate" and not chosen_semantic:
        uncertainty.append("semantic-evidence-missing")
    if chosen == "complementary" and not combined:
        uncertainty.append("coherent-merge-sentence-missing")
    if chosen == "conflicting":
        uncertainty.append("conflict-remains-unresolved")
    if chosen == "superset" and not left.get("components") and not right.get("components") and not hint:
        uncertainty.append("superset-coverage-not-explicit")

    score = 1.0 if chosen in {"duplicate", "superset", "complementary", "conflicting"} else 0.75
    confidence = "high" if score >= 1 else "medium"
    return chosen, sorted(set(chosen_criteria)), sorted(set(chosen_evidence)), sorted(set(chosen_semantic)), confidence, score, sorted(set(uncertainty)), combined


def compare_responsibilities(
    left: Mapping[str, Any] | str,
    right: Mapping[str, Any] | str,
    *,
    relation: str | None = None,
    criteria: Sequence[Any] | None = None,
    evidence: Sequence[Any] | None = None,
    semantic_evidence: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Compare two portable mappings and return a stable comparison record."""

    left_record = _normalise_record(left)
    right_record = _normalise_record(right)
    if left_record is None or right_record is None:
        raise CapabilityIntegrationError("Comparison requires two portable responsibility mappings")
    # Stable left/right ordering makes source input order irrelevant.
    if (left_record["record_id"], left_record["signature"]) > (right_record["record_id"], right_record["signature"]):
        left_record, right_record = right_record, left_record
    hint = _explicit_hint(left_record, right_record)
    chosen, chosen_criteria, deterministic, semantic, confidence, score, uncertainty, combined = _classify_relation(
        left_record,
        right_record,
        relation=relation,
        criteria=criteria,
        evidence=evidence,
        semantic_evidence=semantic_evidence,
    )
    payload = {
        "left_signature": left_record["signature"],
        "right_signature": right_record["signature"],
        "relation": chosen,
        "criteria": chosen_criteria,
        "deterministic_evidence": deterministic,
        "semantic_evidence": semantic,
        "uncertainty": uncertainty,
        "provenance": _comparison_provenance(left_record, right_record),
        "combined_responsibility": combined,
        "stronger_record_reference": _stronger_reference(hint, left_record, right_record),
    }
    comparison_id = _digest(payload, prefix="comparison-")
    result = {
        "record_type": COMPARISON_RECORD_TYPE,
        "record_version": COMPARISON_RECORD_VERSION,
        "comparison_id": comparison_id,
        "left": {
            "record_reference": left_record["record_id"],
            "mapping_reference": left_record.get("mapping_id"),
            "normalized_signature": copy.deepcopy(left_record["signature"]),
            "normalised_signature": copy.deepcopy(left_record["signature"]),
        },
        "right": {
            "record_reference": right_record["record_id"],
            "mapping_reference": right_record.get("mapping_id"),
            "normalized_signature": copy.deepcopy(right_record["signature"]),
            "normalised_signature": copy.deepcopy(right_record["signature"]),
        },
        "left_signature": copy.deepcopy(left_record["signature"]),
        "right_signature": copy.deepcopy(right_record["signature"]),
        "left_normalized_signature": copy.deepcopy(left_record["signature"]),
        "right_normalized_signature": copy.deepcopy(right_record["signature"]),
        "left_normalised_signature": copy.deepcopy(left_record["signature"]),
        "right_normalised_signature": copy.deepcopy(right_record["signature"]),
        "relation": chosen,
        "criteria": chosen_criteria,
        "deterministic_evidence": deterministic,
        "semantic_evidence": semantic,
        "confidence": confidence,
        "confidence_score": score,
        "uncertainty": uncertainty,
        "requires_review": bool(uncertainty),
        "provenance": _comparison_provenance(left_record, right_record),
        "combined_responsibility": combined,
        "stronger_record_reference": _stronger_reference(hint, left_record, right_record),
        "merge_eligible": chosen == "duplicate"
        or chosen == "superset"
        or (chosen == "complementary" and bool(combined) and not uncertainty),
    }
    return result


compare_capabilities = compare_responsibilities
compare_responsibility = compare_responsibilities
compare_capability = compare_responsibilities
compare_capability_responsibilities = compare_responsibilities
compare_capability_sources = compare_responsibilities


def deterministic_comparison_id(
    value: Mapping[str, Any] | str,
    right: Mapping[str, Any] | str | None = None,
    **options: Any,
) -> str:
    """Return the stable id of a comparison-shaped record."""

    if right is not None:
        return compare_responsibilities(value, right, **options)["comparison_id"]
    if not isinstance(value, Mapping):
        raise CapabilityIntegrationError("A comparison record is required")
    if value.get("record_type") == COMPARISON_RECORD_TYPE and value.get("comparison_id"):
        payload = {
            key: value.get(key)
            for key in (
                "left_signature",
                "right_signature",
                "relation",
                "criteria",
                "deterministic_evidence",
                "semantic_evidence",
                "uncertainty",
                "provenance",
                "combined_responsibility",
                "stronger_record_reference",
            )
        }
        return _digest(payload, prefix="comparison-")
    raise CapabilityIntegrationError("A comparison record is required")


def validate_comparison_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the portable shape and deterministic id of a comparison."""

    if not isinstance(value, Mapping):
        raise CapabilityIntegrationError("Comparison record must be a mapping")
    if value.get("record_type") != COMPARISON_RECORD_TYPE:
        raise CapabilityIntegrationError("Comparison record type is invalid")
    if value.get("record_version") != COMPARISON_RECORD_VERSION:
        raise CapabilityIntegrationError("Comparison record version is invalid")
    relation = _relation_name(value.get("relation"))
    if relation is None:
        raise CapabilityIntegrationError("Comparison relation is invalid")
    for key in ("left_signature", "right_signature", "deterministic_evidence", "semantic_evidence", "provenance"):
        if key not in value:
            raise CapabilityIntegrationError(f"Comparison record requires {key}")
    expected = deterministic_comparison_id(value)
    if value.get("comparison_id") != expected:
        raise CapabilityIntegrationError("comparison_id is not deterministic")
    result = copy.deepcopy(dict(value))
    result["relation"] = relation
    return result


validate_comparison = validate_comparison_record


def _relation_for_pair(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    hint = _explicit_hint(left, right)
    relation = (
        _relation_name(_hint_field(hint or {}, "relation", "relation_type", "type", "kind"))
        if hint
        else None
    )
    criteria = _hint_field(hint or {}, "criteria", "criterion", "basis") if hint else None
    evidence = _hint_field(hint or {}, "evidence", "evidence_ids", "deterministic_evidence") if hint else None
    semantic = _hint_field(hint or {}, "semantic_evidence", "semantic_evidence_ids", "meaning_evidence") if hint else None
    return compare_responsibilities(
        left.get("raw", left),
        right.get("raw", right),
        relation=relation,
        criteria=criteria if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)) else ([criteria] if criteria is not None else None),
        evidence=evidence if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)) else ([evidence] if evidence is not None else None),
        semantic_evidence=semantic if isinstance(semantic, Sequence) and not isinstance(semantic, (str, bytes)) else ([semantic] if semantic is not None else None),
    )


def _record_by_id(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {record["record_id"]: record for record in records}


class _UnionFind:
    def __init__(self, keys: Iterable[str]) -> None:
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def union(self, left: str, right: str) -> None:
        first, second = self.find(left), self.find(right)
        if first == second:
            return
        self.parent[max(first, second)] = min(first, second)


def _pair_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compare the meaningful bounded pair set without quadratic broad-intake debt.

    Small collections retain exhaustive classification.  Broad Genesis intake
    compares exact identity candidates and explicitly evidenced semantic pairs;
    all other records remain separate by the Distinct invariant without
    materialising hundreds of thousands of vacuous pair receipts.
    """

    pair_indexes: set[tuple[int, int]] = set()
    if len(records) <= 128:
        pair_indexes.update(
            (left, right)
            for left in range(len(records))
            for right in range(left + 1, len(records))
        )
    else:
        by_signature: dict[str, list[int]] = {}
        by_reference: dict[str, list[int]] = {}
        for index, record in enumerate(records):
            signature_id = _text(record.get("signature", {}).get("signature_id"))
            by_signature.setdefault(signature_id, []).append(index)
            for reference in (record.get("record_id"), record.get("mapping_id")):
                if _text(reference):
                    by_reference.setdefault(_text(reference), []).append(index)
        for indexes in by_signature.values():
            pair_indexes.update(
                (left, right)
                for offset, left in enumerate(indexes)
                for right in indexes[offset + 1 :]
            )
        for left, record in enumerate(records):
            for hint in record.get("relations", []):
                target = next(
                    (
                        _text(hint.get(key))
                        for key in ("target_id", "target", "right_id", "other_id", "with_id")
                        if _text(hint.get(key))
                    ),
                    "",
                )
                for right in by_reference.get(target, []):
                    if left != right:
                        pair_indexes.add(tuple(sorted((left, right))))

    comparisons = [
        _relation_for_pair(records[left], records[right])
        for left, right in sorted(pair_indexes)
    ]
    comparisons.sort(key=lambda item: item["comparison_id"])
    return comparisons


def _comparison_members(comparison: Mapping[str, Any]) -> tuple[str, str]:
    return comparison["left"]["record_reference"], comparison["right"]["record_reference"]


def _superset_merge_allowed(comparison: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    left_id, right_id = _comparison_members(comparison)
    left, right = by_id[left_id], by_id[right_id]
    if left.get("independent_reuse") or right.get("independent_reuse"):
        return False
    left_components, right_components = set(left.get("components", [])), set(right.get("components", []))
    if left_components > right_components or right_components > left_components:
        return True
    if comparison.get("stronger_record_reference") in {left_id, right_id}:
        return True
    # An explicit hint can name the stronger side.  Without explicit coverage
    # evidence the relationship remains visible but does not silently merge.
    return bool(comparison.get("semantic_evidence")) and any(
        item.startswith("coverage:") for item in comparison.get("semantic_evidence", [])
    )


def _complementary_merge_allowed(comparison: Mapping[str, Any]) -> bool:
    return bool(
        comparison.get("combined_responsibility")
        and comparison.get("semantic_evidence")
        and not comparison.get("uncertainty")
    )


def _merge_responsibility_sentence(members: Sequence[Mapping[str, Any]], comparisons: Sequence[Mapping[str, Any]]) -> str:
    sentences = sorted(
        {
            _sentence(item.get("combined_responsibility"), fallback="")
            for item in comparisons
            if _text(item.get("combined_responsibility"))
        }
    )
    if sentences:
        return sentences[0]
    # Duplicate/superset groups intentionally retain the stronger/member
    # sentence.  This path is never used for an un-evidenced complementary
    # merge.
    stronger_references = {
        item.get("stronger_record_reference")
        for item in comparisons
        if item.get("relation") == "superset" and item.get("stronger_record_reference")
    }
    selected_by_reference = [item for item in members if item["record_id"] in stronger_references]
    if selected_by_reference:
        selected = max(selected_by_reference, key=lambda item: item["record_id"])
    elif any(item.get("relation") == "superset" for item in comparisons):
        selected = max(
            members,
            key=lambda item: (
                len(item.get("components", [])),
                len(_identity_text(item["responsibility"])),
                _identity_text(item["responsibility"]),
            ),
        )
    else:
        selected = max(
            members,
            key=lambda item: (_identity_text(item["responsibility"]), len(item["responsibility"])),
        )
    return _sentence(selected["responsibility"], fallback="Perform the bounded responsibility")


def _candidate_human_name(responsibility: str) -> str:
    """Use the governed responsibility as the human name, never an opaque hash."""

    name = re.sub(r"[.!?]+$", "", re.sub(r"\s+", " ", responsibility)).strip()
    if len(name) > 96:
        shortened = re.split(r"\s+(?:for|with|using|while|when)\s+", name, maxsplit=1)[0]
        name = shortened if len(shortened) >= 12 else name[:96].rsplit(" ", 1)[0]
    return name or "Perform the bounded responsibility"


def _choose_provider_scope(members: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    scopes = [item.get("intrinsic") for item in members if item.get("intrinsic") is not None]
    if not scopes:
        return None
    keys = {_canonical(scope) for scope in scopes}
    if len(keys) != 1:
        raise CapabilityIntegrationError("Conflicting intrinsic provider scopes cannot form one Dot")
    return copy.deepcopy(scopes[0])


def _candidate_provenance(members: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for member in members:
        for value in member.get("provenance", []):
            result.append(value)
    return _unique_records(result)


def _implementation_target(member: Mapping[str, Any], candidate_id: str) -> tuple[str, Any]:
    raw = member.get("raw", {})
    implementation = raw.get("implementation")
    if isinstance(implementation, Mapping):
        if "procedure_ref" in implementation:
            return "procedure_ref", copy.deepcopy(implementation["procedure_ref"])
        if "executable_target" in implementation:
            return "executable_target", copy.deepcopy(implementation["executable_target"])
    if "procedure_ref" in raw:
        return "procedure_ref", copy.deepcopy(raw["procedure_ref"])
    if "executable_target" in raw:
        return "executable_target", copy.deepcopy(raw["executable_target"])
    reference = _text(
        raw.get("implementation_ref", raw.get("implementation_target", raw.get("entrypoint")))
    )
    if reference:
        return "procedure_ref", {"kind": "portable-candidate-procedure", "ref": reference}
    return "procedure_ref", {"kind": "portable-candidate-procedure", "ref": f"candidate://{candidate_id}"}


def _candidate_implementation(member: Mapping[str, Any], candidate_id: str, evidence_ids: Sequence[str]) -> dict[str, Any]:
    provider = member.get("provider") or "fractal-candidate"
    target_kind, target = _implementation_target(member, candidate_id)
    implementation_key = {
        "provider": provider,
        "target_kind": target_kind,
        "target": target,
        "source_reference": member.get("source_reference"),
    }
    implementation_id = _digest(implementation_key, prefix="implementation-")
    provenance: dict[str, Any] = {
        "origin": "genesis-capability-integration",
        "source_ids": sorted({item["source_reference"] for item in member.get("provenance", []) if item.get("source_reference")}),
        "source_reference": member.get("source_reference"),
        "evidence_ids": sorted(set(evidence_ids) | set(member.get("evidence", []))),
        "provenance_records": copy.deepcopy(member.get("provenance", [])),
    }
    # Preserve the source licence and restrictions as provenance, never as an
    # execution or activation decision.
    if member.get("licence") is not None:
        provenance["licence"] = copy.deepcopy(member["licence"])
    if member.get("constraints") is not None:
        provenance["source_constraints"] = copy.deepcopy(member["constraints"])
    implementation: dict[str, Any] = {
        "implementation_id": implementation_id,
        "version": "1.0.0",
        "provider": provider,
        "dependencies": [],
        "capability_requirements": ["bounded-portable-responsibility"],
        "permissions": {"operations": ["read-bounded-input", "emit-bounded-output"]},
        target_kind: target,
        "provenance": provenance,
        "compatibility": {"compatible": True, "dot_version": "1.0.0"},
        "verification": {"status": "unverified", "evidence_ids": []},
        "recovery": {
            "strategy": "disable this inactive Candidate Dot and restore the prior implementation",
            "evidence_ids": [f"recovery:{candidate_id}"],
        },
        "evidence": sorted(set(evidence_ids) | set(member.get("evidence", []))),
    }
    return implementation


def _synthesise_candidate(members: Sequence[Mapping[str, Any]], comparisons: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(members, key=lambda item: item["record_id"])
    merged_responsibility = _merge_responsibility_sentence(ordered, comparisons)
    # The identity intentionally excludes evidence, source order, and provider
    # labels.  Provider only participates when the Dot contract says it is
    # intrinsic.
    identity = {
        "responsibility": _identity_text(merged_responsibility),
        "inputs": sorted({value for item in ordered for value in item["inputs"]}),
        "outputs": sorted({value for item in ordered for value in item["outputs"]}),
        "preconditions": sorted({value for item in ordered for value in item["preconditions"]}),
        "side_effects": sorted({value for item in ordered for value in item["side_effects"]}),
        "components": sorted({value for item in ordered for value in item.get("components", [])}),
        "intrinsic": _choose_provider_scope(ordered),
    }
    candidate_id = _digest(identity, prefix="dot-")
    all_evidence = sorted(
        set(item for member in ordered for item in member.get("evidence", []))
        | set(item for comparison in comparisons for item in comparison.get("deterministic_evidence", []))
        | set(item for comparison in comparisons for item in comparison.get("semantic_evidence", []))
    )
    if not all_evidence:
        all_evidence = [f"integration:{candidate_id}"]
    recovery_evidence = [f"recovery:{candidate_id}"]
    source_ids = sorted({member["source_reference"] for member in ordered if member.get("source_reference")})
    provenance = _candidate_provenance(ordered)
    evidence_provenance = [
        {
            "source_reference": source_id,
            "records": [item for item in provenance if item.get("source_reference") == source_id],
        }
        for source_id in source_ids
    ]
    implementations: list[dict[str, Any]] = []
    implementations_by_id: dict[str, dict[str, Any]] = {}
    for member in ordered:
        implementation = _candidate_implementation(member, candidate_id, all_evidence)
        implementation_id = implementation["implementation_id"]
        existing = implementations_by_id.get(implementation_id)
        if existing is None:
            implementations_by_id[implementation_id] = implementation
            continue
        existing["evidence"] = sorted(set(existing["evidence"]) | set(implementation["evidence"]))
        existing_provenance = existing["provenance"]
        incoming_provenance = implementation["provenance"]
        existing_provenance["evidence_ids"] = sorted(
            set(existing_provenance.get("evidence_ids", []))
            | set(incoming_provenance.get("evidence_ids", []))
        )
        existing_provenance["source_ids"] = sorted(
            set(existing_provenance.get("source_ids", []))
            | set(incoming_provenance.get("source_ids", []))
        )
        existing_provenance["provenance_records"] = _unique_records(
            existing_provenance.get("provenance_records", [])
            + incoming_provenance.get("provenance_records", [])
        )
        if existing_provenance.get("licence") != incoming_provenance.get("licence"):
            existing_provenance["licences"] = _unique_records(
                [
                    item
                    for item in (
                        existing_provenance.get("licence"),
                        incoming_provenance.get("licence"),
                    )
                    if item is not None
                ]
            )
            existing_provenance.pop("licence", None)
    implementations = [implementations_by_id[key] for key in sorted(implementations_by_id)]
    relation_names = {comparison.get("relation") for comparison in comparisons}
    change_type = "merge" if len(ordered) > 1 else "material"
    if "complementary" in relation_names:
        change_type = "merge"
    if "superset" in relation_names and len(ordered) > 1:
        change_type = "merge"
    predecessor_seed = {
        "members": [member["record_id"] for member in ordered],
        "sources": source_ids,
    }
    predecessor_id = _digest(predecessor_seed, prefix="integration-origin-")
    lineage: dict[str, Any] = {
        "predecessor_dot_id": predecessor_id,
        "predecessor_version": "0.0.0",
        "change_type": change_type,
        "reason": "Candidate Dot synthesized from explicit portable responsibility evidence.",
        "evidence_ids": all_evidence,
        "recovery_evidence_ids": recovery_evidence,
        "source_ids": source_ids,
        "source_references": source_ids,
        "provenance": provenance,
        "provenance_records": provenance,
        "unresolved_relations": sorted(
            {
                comparison["relation"]
                for comparison in comparisons
                if comparison.get("relation") in {"conflicting", "complementary", "superset"}
                and comparison.get("uncertainty")
            }
        ),
    }
    hooks = [
        {
            "hook_id": "responsibility-boundary",
            "type": "boundary",
            "reason": "The Candidate Dot retains an explicit input/output responsibility boundary.",
            "evidence_ids": all_evidence,
        },
        {
            "hook_id": "reuse-boundary",
            "type": "merge" if len(ordered) > 1 else "not-applicable",
            "reason": "Merge is retained only where responsibility coherence and reuse evidence are present.",
            "evidence_ids": all_evidence,
        },
    ]
    if any(member.get("raw", {}).get("split_evidence") for member in ordered):
        hooks.append(
            {
                "hook_id": "explicit-split-boundary",
                "type": "split",
                "reason": "The extractor supplied separate reusable responsibility evidence; each remains its own Dot.",
                "evidence_ids": all_evidence,
            }
        )
    candidate: dict[str, Any] = {
        "record_type": "capability-dot",
        "record_version": 1,
        "dot_id": candidate_id,
        "version": "1.0.0",
        "human_name": _candidate_human_name(merged_responsibility),
        "responsibility": _sentence(merged_responsibility, fallback="Perform the bounded responsibility"),
        "inputs": identity["inputs"],
        "outputs": identity["outputs"],
        "preconditions": [_sentence(value, fallback="The declared boundary is present") for value in identity["preconditions"]],
        "side_effects": [_sentence(value, fallback="Emit one bounded output") for value in identity["side_effects"]],
        "lifecycle": {
            "state": "candidate",
            "candidate": True,
            "active": False,
            "active_surface": False,
            "material_change": True,
            "transition_evidence": [f"candidate-created:{candidate_id}"],
            "change_reason": "Genesis integration produced an inactive Candidate Dot.",
        },
        "evidence": {
            "evidence_ids": all_evidence,
            "provenance": evidence_provenance,
            "source_ids": source_ids,
            "source_references": source_ids,
            "comparison_ids": sorted(item["comparison_id"] for item in comparisons),
            "notes": "Portable evidence and licence observations are retained for review.",
        },
        "recovery": {
            "strategy": "Disable this inactive Candidate Dot and restore the prior implementation or retain the separate alternatives.",
            "evidence_ids": recovery_evidence,
            "restore_ref": predecessor_id,
        },
        "coherence": {
            "coherent_responsibility": True,
            "reuse_rationale": "The responsibility has an explicit boundary and remains reusable independently of its providers.",
            "boundary_evidence_hooks": hooks,
        },
        "implementations": implementations,
        "trial": {"status": "pending"},
        "verification": {"status": "unverified"},
        "system_review": {"status": "pending"},
        "human_decision": {"status": "pending"},
        "activation": {"status": "inactive", "authorised": False},
        "lineage": lineage,
    }
    intrinsic = _choose_provider_scope(ordered)
    if intrinsic is not None:
        candidate["provider_specific"] = intrinsic
    try:
        return validate_capability_dot(candidate)
    except Exception as error:  # keep the public error independent of Dot internals
        raise CapabilityIntegrationError("Synthesized Candidate Dot failed the Dot contract") from error


def synthesize_candidate_dot(
    values: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    comparisons: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one inactive Candidate Dot from already portable mappings."""

    if isinstance(values, Mapping):
        values = [values]
    records, _ = _normalise_records(values)
    if not records:
        raise CapabilityIntegrationError("At least one portable responsibility mapping is required")
    if comparisons is None:
        comparisons = _pair_records(records)
    return _synthesise_candidate(records, comparisons)


synthesise_candidate_dot = synthesize_candidate_dot
build_candidate_dot = synthesize_candidate_dot


def integrate_capabilities(
    values: Iterable[Any] | Mapping[str, Any],
    mappings: Iterable[Any] | None = None,
    *,
    responsibility_mappings: Iterable[Any] | None = None,
    responsibility_evidence: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Compare and synthesize deterministic inactive Candidate Dots.

    ``mappings`` is optional convenience input for a caller that keeps Source
    records and extraction output in separate collections.  The function only
    validates supplied Source metadata; it never performs Source intake or
    Workplace writes.
    """

    if mappings is not None and responsibility_mappings is not None:
        raise CapabilityIntegrationError("Provide one mappings collection")
    if responsibility_mappings is not None:
        mappings = responsibility_mappings
    if responsibility_evidence is not None:
        if mappings is not None:
            mappings = list(_flatten_collection(mappings)) + list(
                _flatten_collection(responsibility_evidence)
            )
        else:
            mappings = responsibility_evidence

    if isinstance(values, Mapping):
        if "mappings" in values and mappings is None:
            mappings = values["mappings"] if isinstance(values["mappings"], Iterable) else None
        if "records" in values and mappings is None:
            values = values["records"]
        elif "sources" in values:
            values = values["sources"]
        else:
            values = [values]
    base_values = _flatten_collection(values)
    if mappings is not None:
        base_values.extend(_flatten_collection(mappings))
    base_values = _attach_source_context(base_values)
    records, ignored = _normalise_records(base_values)
    comparisons = _pair_records(records)
    by_id = _record_by_id(records)
    union = _UnionFind(by_id)
    eligible_comparisons: list[dict[str, Any]] = []
    for comparison in comparisons:
        left_id, right_id = _comparison_members(comparison)
        relation = comparison["relation"]
        if (
            relation == "duplicate"
            or relation == "superset"
            and _superset_merge_allowed(comparison, by_id)
            or relation == "complementary"
            and _complementary_merge_allowed(comparison)
        ):
            union.union(left_id, right_id)
            eligible_comparisons.append(comparison)

    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        groups.setdefault(union.find(record["record_id"]), []).append(record)
    candidates: list[dict[str, Any]] = []
    for root in sorted(groups):
        members = sorted(groups[root], key=lambda item: item["record_id"])
        member_ids = {item["record_id"] for item in members}
        group_comparisons = [
            comparison
            for comparison in comparisons
            if set(_comparison_members(comparison)).issubset(member_ids)
        ]
        candidates.append(_synthesise_candidate(members, group_comparisons))
    candidates.sort(key=lambda item: item["dot_id"])
    result = {
        "record_type": INTEGRATION_RECORD_TYPE,
        "record_version": INTEGRATION_RECORD_VERSION,
        "comparisons": sorted(comparisons, key=lambda item: item["comparison_id"]),
        "candidates": candidates,
        "candidate_dots": copy.deepcopy(candidates),
        "ignored": ignored,
        "comparison_scope": {
            "mode": "exhaustive" if len(records) <= 128 else "bounded-identity-and-evidence",
            "record_count": len(records),
            "materialized_pair_count": len(comparisons),
            "possible_pair_count": len(records) * max(0, len(records) - 1) // 2,
            "unmaterialized_pairs_classification": "distinct-by-preserved-boundary",
        },
        "inactive": True,
        "workplace_owner": "workplace",
        "lineage": {
            "source_count": len({item.get("source_reference") for item in records if item.get("source_reference")}),
            "responsibility_count": len(records),
            "candidate_ids": [item["dot_id"] for item in candidates],
        },
    }
    # Every Candidate was validated at construction.  Sorting and deep-copying
    # do not mutate its contract, so a second full schema traversal here would
    # add broad-intake latency without adding an independent boundary.
    return result


integrate_responsibilities = integrate_capabilities
integrate_sources = integrate_capabilities
integrate_capability_sources = integrate_capabilities
compare_and_integrate = integrate_capabilities
integrate_candidate_dots = integrate_capabilities
integrate_capability_responsibilities = integrate_capabilities


def synthesize_candidate_dots(
    values: Iterable[Any],
    mappings: Iterable[Any] | None = None,
    **options: Any,
) -> list[dict[str, Any]]:
    """Small list-returning convenience wrapper around integration."""

    return integrate_capabilities(values, mappings, **options)["candidates"]


synthesise_candidate_dots = synthesize_candidate_dots
synthesize_candidates = synthesize_candidate_dots
synthesise_candidates = synthesize_candidate_dots
build_candidate_dots = synthesize_candidate_dots
RESPONSIBILITY_RELATIONS = RELATIONS


__all__ = [
    "COMPARISON_RECORD_TYPE",
    "COMPARISON_RECORD_VERSION",
    "INTEGRATION_RECORD_TYPE",
    "INTEGRATION_RECORD_VERSION",
    "RELATIONS",
    "RESPONSIBILITY_RELATIONS",
    "CapabilityIntegrationError",
    "IntegrationError",
    "ComparisonError",
    "normalize_responsibility",
    "normalise_responsibility",
    "normalize_signature",
    "normalise_signature",
    "compare_responsibilities",
    "compare_capabilities",
    "compare_responsibility",
    "compare_capability",
    "compare_capability_responsibilities",
    "compare_capability_sources",
    "deterministic_comparison_id",
    "validate_comparison_record",
    "validate_comparison",
    "synthesize_candidate_dot",
    "synthesise_candidate_dot",
    "build_candidate_dot",
    "synthesize_candidate_dots",
    "synthesise_candidate_dots",
    "integrate_capabilities",
    "integrate_responsibilities",
    "integrate_sources",
    "integrate_capability_sources",
    "compare_and_integrate",
    "integrate_candidate_dots",
    "integrate_capability_responsibilities",
    "synthesize_candidates",
    "synthesise_candidates",
    "build_candidate_dots",
]
