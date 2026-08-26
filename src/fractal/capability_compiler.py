# ruff: noqa: E501,SIM102

"""Deterministic bottom-up genesis compiler for capability candidates.

This module is deliberately a compiler boundary, rather than another
capability contract.  It accepts an explicitly supplied Source catalogue and
small evidence inputs, calls the maintained genesis engines in a fixed order,
and returns one detached candidate graph.  It never retrieves a Source,
executes a provider, writes Workplace state, registers a candidate, or
activates a surface.

The compiler is intentionally conservative at its edges:

* Source records are validated as raw, non-callable Source records.
* Documents and claims are consumed only by the extraction engine and are not
  copied into the graph.
* Old Actions, categories, dot groups, and active/persistent records are
  rejected as inputs instead of being used as seeds.
* Provider identity is allowed below a Candidate Dot only where the Dot
  engine's explicit intrinsic boundary permits it.  It cannot leak into a
  Workflow or Action human surface.

The public function is pure.  A normal runtime path must call it explicitly;
there is no import-time registration or automatic hook in this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from fractal.capability_action import validate_action_graph
from fractal.capability_action_induction import (
    compress_candidate_actions,
    induce_candidate_actions,
)
from fractal.capability_dot import validate_capability_dot
from fractal.capability_extraction import (
    ExtractionValidationError,
    extract_responsibilities,
    validate_responsibility_record,
)
from fractal.capability_integration import (
    CapabilityIntegrationError,
    integrate_capabilities,
)
from fractal.capability_source import (
    SourceValidationError,
    validate_source_catalogue,
)
from fractal.capability_workflow import validate_workflow
from fractal.capability_workflow_synthesis import synthesize_candidate_workflows

GRAPH_SCHEMA_URI = "https://fractal.local/schemas/capability-candidate-graph.schema.json"
GRAPH_SCHEMA_FILENAME = "capability-candidate-graph.schema.json"
GRAPH_RECORD_TYPE = "capability-candidate-graph"
GRAPH_RECORD_VERSION = 1
GRAPH_VERSION = "1.0.0"
COMPILER_METHOD = "deterministic-bottom-up-genesis-compiler"
COMPILER_METHOD_VERSION = "1.0.0"


class CapabilityCompilerError(ValueError):
    """Base error raised at the explicit compiler boundary."""


class CompilerInputError(CapabilityCompilerError):
    """The supplied genesis evidence is missing, malformed, or unsafe."""


class CompilerBoundaryError(CapabilityCompilerError):
    """An input attempted to cross the candidate-only compiler boundary."""


class CompilerDependencyError(CapabilityCompilerError):
    """A required maintained genesis engine is unavailable."""


# Keys are checked as keys, never as words in a human sentence.  In
# particular ``workflow_id`` is legitimate in human-intent evidence; the
# old-surface forms below are not.
_LEGACY_KEYS = frozenset(
    {
        "action",
        "actions",
        "action_id",
        "action_ref",
        "action_refs",
        "category",
        "categories",
        "dot_group",
        "dot_groups",
        "dot_group_id",
        "dot_group_ref",
        "legacy_action",
        "legacy_actions",
        "legacy_workflow",
        "legacy_workflows",
        "old_action",
        "old_actions",
        "old_workflow",
        "old_workflows",
        "existing_actions",
        "existing_workflows",
        "user_surface",
        "user_surface_action",
        "active_surface",
        "persistent_output",
        "persistent_outputs",
        "persistence_authority",
        "execution_authority",
        "activation_authority",
    }
)
_CANDIDATE_RECORD_TYPES = frozenset(
    {
        "capability-dot",
        "capability_dot",
        "capability-workflow",
        "capability_workflow",
        "capability-action",
        "capability_action",
        "dot",
        "workflow",
        "action",
    }
)
_ACTIVE_STATES = frozenset({"active", "activated", "registered", "published", "persistent"})
_PROVIDER_KEYS = frozenset(
    {
        "provider",
        "provider_id",
        "provider_ref",
        "provider_refs",
        "provider_specific",
        "provider_selection",
        "provider_implementation",
        "provider_implementations",
        "implementation_provider",
        "vendor",
        "connector",
        "platform_skill",
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
_RELATIONS = frozenset({"duplicate", "superset", "complementary", "conflicting", "distinct"})
_DEFAULT_SIDE_EFFECT = "no provider execution or persistence side effect is established by extraction."
_DEFAULT_PRECONDITION = "the referenced source evidence is available."
_SAFE_SIDE_EFFECT = "emit one bounded output."
_SAFE_PRECONDITION = "the input is present."


def _canonical(value: Any, *, path: str = "$", sort_lists: bool = True, key: str | None = None) -> Any:
    """Return a JSON-safe, order-independent value for digests and sorting.

    List order is significant for an observed path or explicitly sequenced
    steps, but collection order is not.  Naming proposals are a collection of
    alternatives rather than a priority order at this boundary; sorting them
    makes the compiler's digest independent of caller ordering.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: (str(item).casefold(), str(item))):
            if not isinstance(raw_key, str):
                raise CompilerInputError(f"Input at {path} contains a non-text key")
            result[raw_key] = _canonical(
                value[raw_key], path=f"{path}.{raw_key}", key=raw_key
            )
        return result
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        items = [
            _canonical(item, path=f"{path}[{index}]", key=key)
            for index, item in enumerate(value)
        ]
        ordered_keys = {
            "dot_ids",
            "dot_sequence",
            "path",
            "steps",
            "sequence",
            "workflow_refs",
            "dot_refs",
        }
        if sort_lists and key not in ordered_keys:
            items.sort(key=_json_sort_key)
        return items
    raise CompilerInputError(f"Input at {path} is not portable JSON")


def _json_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(
            _canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CompilerInputError("Compiler input must be portable JSON") from error
    return hashlib.sha256(payload).hexdigest()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _key(value: Any) -> str:
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _walk_input(
    value: Any,
    *,
    path: str = "$",
    allow_workflow_target: bool = False,
    allow_provider: bool = False,
    reject_candidate_records: bool = True,
) -> None:
    """Reject authority-bearing input fields without rejecting prose words."""

    if isinstance(value, Mapping):
        record_type = _key(value.get("record_type"))
        if reject_candidate_records and record_type in _CANDIDATE_RECORD_TYPES:
            raise CompilerBoundaryError(
                f"Candidate/legacy {record_type} record cannot seed the compiler at {path}"
            )
        lifecycle = value.get("lifecycle")
        states: list[Any] = [value.get("status"), value.get("state")]
        if isinstance(lifecycle, Mapping):
            states.extend((lifecycle.get("status"), lifecycle.get("state")))
            if lifecycle.get("active") is True or lifecycle.get("active_surface") is True:
                raise CompilerBoundaryError(f"active candidate input is forbidden at {path}")
        activation = value.get("activation")
        if isinstance(activation, Mapping):
            if activation.get("status") not in (None, "inactive", "candidate"):
                raise CompilerBoundaryError(f"active/persistent candidate input is forbidden at {path}")
            if activation.get("authorised") is True or activation.get("authorized") is True:
                raise CompilerBoundaryError(f"authorised candidate input is forbidden at {path}")
        if any(_key(item) in _ACTIVE_STATES for item in states if item is not None):
            raise CompilerBoundaryError(f"active/persistent input is forbidden at {path}")
        for raw_name, child in value.items():
            name = _key(raw_name)
            if name in _LEGACY_KEYS:
                raise CompilerBoundaryError(f"legacy Action/category/surface field is forbidden: {path}.{raw_name}")
            if name in {"workflow_id", "workflow_ids", "workflow_ref", "workflow_refs", "workflow_version"}:
                if not allow_workflow_target:
                    raise CompilerBoundaryError(f"Workflow surface reference is forbidden at {path}.{raw_name}")
            if name in _PROVIDER_KEYS and not allow_provider:
                raise CompilerBoundaryError(f"provider leakage is forbidden at {path}.{raw_name}")
            _walk_input(
                child,
                path=f"{path}.{raw_name}",
                allow_workflow_target=allow_workflow_target,
                allow_provider=allow_provider,
                reject_candidate_records=reject_candidate_records,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _walk_input(
                child,
                path=f"{path}[{index}]",
                allow_workflow_target=allow_workflow_target,
                allow_provider=allow_provider,
                reject_candidate_records=reject_candidate_records,
            )


def _source_catalogue(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerInputError("source_catalogue must be a mapping")
    # A caller may hand us a catalogue whose only non-canonical property is
    # list order.  Canonicalise that collection before applying the strict
    # Source schema; all Source records themselves remain strictly validated.
    raw = copy.deepcopy(dict(value))
    sources = raw.get("sources")
    if isinstance(sources, list):
        raw["sources"] = sorted(
            sources,
            key=lambda item: _text(item.get("source_id"))
            if isinstance(item, Mapping)
            else _json_sort_key(item),
        )
    try:
        catalogue = validate_source_catalogue(raw)
    except SourceValidationError as error:
        if any(
            token in str(error).casefold()
            for token in ("callable", "resolvable", "authoritative", "execution_authority", "persistence_authority")
        ):
            raise CompilerBoundaryError("Callable, active, or authoritative Sources cannot seed genesis") from error
        raise CompilerInputError(f"Invalid validated Source catalogue: {error}") from error
    except TypeError as error:
        raise CompilerInputError(f"Invalid validated Source catalogue: {error}") from error
    for source in catalogue["sources"]:
        source_only = source.get("source_only", {})
        if any(source_only.get(name) is not False for name in (
            "callable",
            "resolvable",
            "active",
            "execution_authority",
            "persistence_authority",
        )):
            raise CompilerBoundaryError("Callable, active, or authoritative Sources cannot seed genesis")
    return catalogue


def _as_values(value: Any, *, label: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in (
            "items",
            "records",
            "values",
            "evidence",
            "claims",
            "documents",
            "observations",
            "signatures",
            "outcomes",
            "comparisons",
        ):
            if key in value:
                child = value[key]
                if isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                    return list(child)
                return [child]
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, str):
        return [value]
    raise CompilerInputError(f"{label} must be a compact mapping, list, or text")


def _source_material(
    value: Any,
    source_ids: Sequence[str],
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Normalise source-id keyed documents and claims without retaining them."""

    if value is None:
        raise CompilerInputError("source_documents (compact documents/claims) is required")
    ids = set(source_ids)
    documents: dict[str, Any] = {}
    claims: dict[str, list[Any]] = {}

    def add(source_id: Any, *, document: Any = None, source_claims: Any = None) -> None:
        sid = _text(source_id)
        if sid not in ids:
            raise CompilerInputError(f"source material references unknown Source: {sid or source_id!r}")
        if document is not None:
            if sid in documents and _json_sort_key(_canonical(documents[sid])) != _json_sort_key(_canonical(document)):
                raise CompilerInputError(f"multiple conflicting documents supplied for Source {sid}")
            documents[sid] = copy.deepcopy(document)
        if source_claims is not None:
            values = _as_values(source_claims, label=f"claims[{sid}]")
            claims.setdefault(sid, []).extend(copy.deepcopy(values))

    # ``{documents: {...}, claims: {...}}`` is the most explicit form.
    if isinstance(value, Mapping) and any(
        name in value for name in ("documents", "source_documents", "claims", "source_claims")
    ):
        docs = value.get("documents", value.get("source_documents"))
        if docs is not None:
            if isinstance(docs, Mapping) and not any(name in docs for name in ("frontmatter", "text", "body", "content")):
                for sid, document in docs.items():
                    add(sid, document=document)
            elif len(source_ids) == 1:
                add(source_ids[0], document=docs)
            else:
                raise CompilerInputError("source_documents must be keyed by Source id")
        raw_claims = value.get("claims", value.get("source_claims"))
        if raw_claims is not None:
            if isinstance(raw_claims, Mapping) and not any(
                name in raw_claims for name in ("responsibility", "responsibilities", "capability")
            ):
                for sid, source_claims in raw_claims.items():
                    add(sid, source_claims=source_claims)
            elif len(source_ids) == 1:
                add(source_ids[0], source_claims=raw_claims)
            else:
                raise CompilerInputError("claims must be keyed by Source id")
        return documents, claims

    if isinstance(value, Mapping):
        # A single compact document (frontmatter/text) belongs to the one
        # Source.  Otherwise each top-level key is a Source id.
        if any(name in value for name in ("frontmatter", "text", "body", "content")):
            if len(source_ids) != 1:
                raise CompilerInputError("an unkeyed compact document requires exactly one Source")
            add(source_ids[0], document=value)
            return documents, claims
        for sid, item in value.items():
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                # A source-id keyed list is the compact claims form.  A
                # document itself is a string or mapping, never an array.
                add(sid, source_claims=item)
                continue
            if isinstance(item, Mapping) and any(
                name in item for name in ("document", "source_document", "text", "body", "content", "claims", "source_claims")
            ):
                document = item.get("document", item.get("source_document"))
                if document is None and any(name in item for name in ("text", "body", "content")):
                    document = item
                add(sid, document=document, source_claims=item.get("claims", item.get("source_claims")))
            else:
                add(sid, document=item)
        return documents, claims

    values = list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
    if len(source_ids) == 1 and all(
        not isinstance(item, Mapping) or "source_id" not in item for item in values
    ):
        if all(isinstance(item, (str, Mapping)) for item in values):
            if any(
                isinstance(item, str)
                and ("\n" in item or item.lstrip().startswith(("---", "#")))
                for item in values
            ) or any(
                isinstance(item, Mapping)
                and any(name in item for name in ("frontmatter", "text", "body", "content"))
                for item in values
            ):
                add(source_ids[0], document=values[0] if len(values) == 1 else {"text": "\n".join(item for item in values if isinstance(item, str))})
            else:
                add(source_ids[0], source_claims=values)
        return documents, claims
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            raise CompilerInputError(f"source_documents[{index}] must name a source_id")
        sid = item.get("source_id", item.get("source_ref"))
        document = item.get("document", item.get("source_document"))
        if document is None and any(name in item for name in ("frontmatter", "text", "body", "content")):
            document = item
        add(sid, document=document, source_claims=item.get("claims", item.get("source_claims")))
    return documents, claims


def _record_key(record: Mapping[str, Any]) -> str:
    digest = _text(record.get("evidence_digest"))
    if digest:
        return f"responsibility-{digest[:24]}"
    signature = _text(record.get("normalized_signature", record.get("normalised_signature")))
    if signature:
        return f"responsibility-{_digest(signature)[:24]}"
    return f"responsibility-{_digest(record)[:24]}"


def _comparison_items(value: Any) -> list[dict[str, Any]]:
    if value is None:
        raise CompilerInputError("semantic_comparison_evidence is required")
    raw: Any = value
    if isinstance(value, Mapping):
        for key in (
            "comparisons",
            "comparison_evidence",
            "semantic_comparisons",
            "relations",
            "evidence",
        ):
            if key in value:
                raw = value[key]
                break
    items = _as_values(raw, label="semantic_comparison_evidence")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise CompilerInputError(f"semantic_comparison_evidence[{index}] must be a mapping")
        result.append(copy.deepcopy(dict(item)))
    return result


def _relation(value: Any) -> str | None:
    name = _key(value).replace("_", "-")
    name = _RELATION_ALIASES.get(name, name)
    return name if name in _RELATIONS else None


def _endpoint_matches(value: Any, records: Sequence[Mapping[str, Any]]) -> list[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return [value] if 0 <= value < len(records) else []
    if isinstance(value, Mapping):
        candidates = [
            value.get("mapping_id"),
            value.get("responsibility_id"),
            value.get("record_id"),
            value.get("normalized_signature"),
            value.get("normalised_signature"),
            value.get("evidence_digest"),
            value.get("responsibility"),
            value.get("text"),
            value.get("id"),
        ]
        result: set[int] = set()
        for candidate in candidates:
            result.update(_endpoint_matches(candidate, records))
        return sorted(result)
    text = _text(value)
    if not text:
        return []
    lowered = re.sub(r"\s+", " ", text).strip().casefold().rstrip(".!?")
    result: list[int] = []
    for index, record in enumerate(records):
        values = {
            _record_key(record),
            _text(record.get("mapping_id")),
            _text(record.get("evidence_digest")),
            _text(record.get("normalized_signature")),
            _text(record.get("normalised_signature")),
            re.sub(r"\s+", " ", _text(record.get("responsibility"))).strip().casefold().rstrip(".!?"),
        }
        if lowered in {item.casefold().rstrip(".!?") for item in values if item}:
            result.append(index)
    return result


def _semantic_overlays(
    records: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach bounded relation hints to extraction records and retain misses."""

    enriched = [copy.deepcopy(dict(record)) for record in records]
    by_key = [_record_key(item) for item in enriched]
    for record, mapping_id in zip(enriched, by_key, strict=True):
        record["mapping_id"] = mapping_id
    unresolved: list[dict[str, Any]] = []
    for index, raw in enumerate(evidence):
        _walk_input(raw, path=f"$.semantic_comparison_evidence[{index}]")
        relation_name = _relation(raw.get("relation", raw.get("relation_type", raw.get("kind"))))
        if relation_name is None:
            unresolved.append(
                {
                    "kind": "semantic-input",
                    "reasons": ["comparison evidence does not name one of the five bounded relations"],
                    "evidence": _canonical(raw),
                }
            )
            continue
        left_value = raw.get("left", raw.get("left_id", raw.get("source")))
        right_value = raw.get("right", raw.get("right_id", raw.get("target")))
        if left_value is None and "members" in raw:
            members = raw["members"]
            if isinstance(members, Sequence) and not isinstance(members, (str, bytes, bytearray)) and len(members) == 2:
                left_value, right_value = members
        left = _endpoint_matches(left_value, enriched)
        right = _endpoint_matches(right_value, enriched)
        if len(left) != 1 or len(right) != 1 or left[0] == right[0]:
            unresolved.append(
                {
                    "kind": "semantic-input",
                    "reasons": ["comparison endpoints do not resolve to exactly two extracted responsibilities"],
                    "relation": relation_name,
                    "evidence": _canonical(raw),
                }
            )
            continue
        left_index, right_index = left[0], right[0]
        relation_payload = {
            key: copy.deepcopy(raw[key])
            for key in (
                "criteria",
                "criterion",
                "basis",
                "evidence",
                "evidence_ids",
                "semantic_evidence",
                "semantic_evidence_ids",
                "merged_responsibility",
                "combined_responsibility",
                "stronger_id",
                "stronger_side",
                "superset_id",
            )
            if key in raw
        }
        relation_payload["relation"] = relation_name
        relation_payload["target_id"] = by_key[right_index]
        enriched[left_index].setdefault("relationship", []).append(relation_payload)
        reverse = copy.deepcopy(relation_payload)
        reverse["target_id"] = by_key[left_index]
        enriched[right_index].setdefault("relationship", []).append(reverse)
    for record in enriched:
        if isinstance(record.get("relationship"), list):
            record["relationship"] = sorted(
                record["relationship"], key=lambda item: _json_sort_key(_canonical(item))
            )
    return enriched, unresolved


def _extract_records(
    catalogue: Mapping[str, Any],
    documents: Mapping[str, Any],
    claims: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in catalogue["sources"]:
        source_id = source["source_id"]
        metadata = None
        if claims.get(source_id):
            metadata = {"responsibilities": list(claims[source_id])}
        try:
            extracted = extract_responsibilities(
                source,
                documents.get(source_id),
                metadata=metadata,
            )
        except (ExtractionValidationError, ValueError) as error:
            raise CompilerInputError(f"Responsibility extraction failed for Source {source_id}") from error
        for item in extracted:
            try:
                record = validate_responsibility_record(item, source=source)
            except (ExtractionValidationError, ValueError) as error:
                raise CompilerInputError(f"Extraction returned invalid evidence for Source {source_id}") from error
            records.append(record)
    # The engine already sorts, but the compiler owns the cross-Source order.
    return sorted(
        records,
        key=lambda item: (
            _text(item.get("normalized_signature")),
            tuple(sorted(_text(ref) for ref in item.get("source_refs", []))),
            _text(item.get("evidence_digest")),
        ),
    )


def _candidate_contribution_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split retained extraction evidence at the candidate-contribution gate.

    Extraction findings from a research-only or licence-blocked Source remain
    evidence in the returned graph, but they are not portable candidate seeds.
    The gate is deliberately strict here as a second compiler-boundary check:
    a missing or non-boolean decision must never be interpreted as permission
    to integrate a finding.
    """

    contributing: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise CompilerInputError(
                f"extraction evidence[{index}] must be a mapping before candidate contribution"
            )
        allowed = raw.get("candidate_contribution_allowed")
        if not isinstance(allowed, bool):
            raise CompilerInputError(
                "candidate contribution gate is missing or invalid on extraction evidence "
                f"[{index}]"
            )
        target = contributing if allowed else blocked
        target.append(copy.deepcopy(dict(raw)))
    return contributing, blocked


def _safe_workflow_dots(dots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Adapt extractor's explicit 'no effect established' defaults.

    The Workflow engine quite correctly rejects words such as ``persistence``
    in a declared side effect.  Extraction's compact absence statement is not
    an effect, so it is represented as a bounded caller output for this
    downstream structural check only.  The Candidate Dot retained in the
    graph is never changed.
    """

    result: list[dict[str, Any]] = []
    for raw in dots:
        item = copy.deepcopy(dict(raw))
        if item.get("side_effects") == [_DEFAULT_SIDE_EFFECT]:
            item["side_effects"] = [_SAFE_SIDE_EFFECT]
        if item.get("preconditions") == [_DEFAULT_PRECONDITION]:
            item["preconditions"] = [_SAFE_PRECONDITION]
        result.append(item)
    return result


def _infer_contracts(dots: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    produced = {
        _key(port.get("id", port.get("name"))) if isinstance(port, Mapping) else _key(port)
        for dot in dots
        for port in (dot.get("outputs") or [])
    }
    external: dict[str, Any] = {}
    for dot in dots:
        for port in dot.get("inputs") or []:
            name = port.get("id", port.get("name")) if isinstance(port, Mapping) else port
            if _key(name) not in produced:
                external.setdefault(_key(name), copy.deepcopy(port))
    values = [external[key] for key in sorted(external)]
    return {"inputs": values} if values else None


def _rewrite_observed_paths(value: Any, dots: Sequence[Mapping[str, Any]]) -> Any:
    """Resolve human-readable observed paths to the generated Dot ids."""

    by_token: dict[str, set[str]] = {}
    for dot in dots:
        dot_id = _text(dot.get("dot_id"))
        tokens = {
            dot_id,
            _text(dot.get("human_name")),
            _text(dot.get("responsibility")),
        }
        tokens.update(
            _text(port.get("id", port.get("name"))) if isinstance(port, Mapping) else _text(port)
            for port in (dot.get("inputs") or []) + (dot.get("outputs") or [])
        )
        for token in tokens:
            if token:
                by_token.setdefault(token.casefold().rstrip(".!?"), set()).add(dot_id)

    def resolve(token: Any) -> Any:
        text = _text(token)
        if not text:
            return token
        matches = by_token.get(text.casefold().rstrip(".!?"), set())
        return next(iter(matches)) if len(matches) == 1 else token

    result = copy.deepcopy(value)
    if isinstance(result, Mapping):
        # Keep wrappers such as {observations: [...]}; only path-bearing
        # records are rewritten.
        result = {key: _rewrite_observed_paths(child, dots) for key, child in result.items()}
        for key in ("dot_ids", "dot_sequence", "path"):
            if isinstance(result.get(key), Sequence) and not isinstance(result[key], (str, bytes, bytearray)):
                result[key] = [resolve(item) for item in result[key]]
        steps = result.get("steps")
        if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes, bytearray)):
            for step in steps:
                if isinstance(step, Mapping):
                    ref = step.get("ref", step)
                    if isinstance(ref, Mapping) and "dot_id" in ref:
                        ref["dot_id"] = resolve(ref["dot_id"])
                    elif isinstance(ref, str):
                        step["ref"] = resolve(ref)
        return result
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return [_rewrite_observed_paths(child, dots) for child in result]
    return result


def _rewrite_workflow_contract_targets(
    value: Any,
    dots: Sequence[Mapping[str, Any]],
) -> Any:
    """Resolve pre-synthesis responsibility labels to exact generated Dot ids."""

    if value is None:
        return None
    labels: dict[str, set[str]] = {}
    for dot in dots:
        dot_id = _text(dot.get("dot_id"))
        for raw in (dot_id, dot.get("responsibility"), dot.get("human_name")):
            label = _text(raw).casefold().rstrip(".!?")
            if label:
                labels.setdefault(label, set()).add(dot_id)

    def resolve(raw: Any) -> str | None:
        text = _text(raw).casefold().rstrip(".!?")
        matches = labels.get(text, set())
        return next(iter(matches)) if len(matches) == 1 else None

    def rewrite(node: Any) -> Any:
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            return [rewrite(item) for item in node]
        if not isinstance(node, Mapping):
            return copy.deepcopy(node)
        result = {key: rewrite(child) for key, child in node.items()}
        if "responsibility" in result and "dot_id" not in result:
            dot_id = resolve(result["responsibility"])
            if dot_id:
                result["dot_id"] = dot_id
        if "from_responsibility" in result:
            dot_id = resolve(result.pop("from_responsibility"))
            if dot_id:
                result["from_dot"] = dot_id
        if "to_responsibility" in result:
            dot_id = resolve(result.pop("to_responsibility"))
            if dot_id:
                result["to_dot"] = dot_id
        return result

    return rewrite(value)


def _workflow_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return [copy.deepcopy(dict(item)) for item in value] if isinstance(value, Sequence) else []
    for key in ("candidate_workflows", "candidates", "workflows"):
        if key in value:
            return _workflow_records(value[key])
    return [copy.deepcopy(dict(value))]


def _rewrite_human_intent_targets(value: Any, workflows: Sequence[Mapping[str, Any]]) -> Any:
    """Resolve optional human labels to exact generated Workflow references.

    The Action engine intentionally requires exact ``workflow_id`` and
    ``version`` pairs.  Genesis evidence, however, is collected before those
    deterministic ids exist, so a compact evidence record may name the
    Workflow by its outcome or human name.  A unique match is rewritten; an
    ambiguous label is left intact so induction fails closed rather than
    guessing.
    """

    identities = {
        _text(item.get("workflow_id")): (_text(item.get("workflow_id")), _text(item.get("version")))
        for item in workflows
        if _text(item.get("workflow_id"))
    }
    labels: dict[str, set[tuple[str, str]]] = {}
    for workflow in workflows:
        identity = (
            _text(workflow.get("workflow_id")),
            _text(workflow.get("version")),
        )
        values = [
            workflow.get("workflow_id"),
            workflow.get("human_name"),
            workflow.get("success_contract", {}).get("outcome")
            if isinstance(workflow.get("success_contract"), Mapping)
            else None,
        ]
        for raw in values:
            text = _text(raw)
            if text:
                labels.setdefault(text.casefold().rstrip(".!?"), set()).add(identity)

    def target(raw: Any) -> tuple[str, str] | None:
        text = _text(raw)
        if not text:
            return None
        if "@" in text:
            workflow_id, version = text.rsplit("@", 1)
            if workflow_id in identities and (workflow_id, version) in identities.values():
                return workflow_id, version
        if text in identities:
            return identities[text]
        matches = labels.get(text.casefold().rstrip(".!?"), set())
        return next(iter(matches)) if len(matches) == 1 else None

    target_keys = {
        "workflow_id",
        "workflow_ids",
        "workflow_ref",
        "workflow_refs",
        "workflow_version",
        "workflow_versions",
    }

    def rewrite(node: Any) -> Any:
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            return [rewrite(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        result = {key: rewrite(child) for key, child in node.items()}
        if "workflow_id" in result:
            resolved = target(result["workflow_id"])
            if resolved:
                result["workflow_id"] = resolved[0]
                result.setdefault("version", resolved[1])
        if isinstance(result.get("workflow_ids"), Sequence) and not isinstance(result["workflow_ids"], (str, bytes, bytearray)):
            result["workflow_ids"] = [target(item)[0] if target(item) else item for item in result["workflow_ids"]]
        # A keyed natural label is transformed into an explicit child entry so
        # the engine does not interpret it as an unknown exact Workflow id.
        if not any(key in result for key in target_keys):
            converted: list[Any] = []
            for raw_key, child in result.items():
                if raw_key in {"entries", "intents", "evidence", "items", "records", "human_intents"}:
                    continue
                resolved = target(raw_key)
                if resolved and isinstance(child, Mapping):
                    converted.append({"workflow_id": resolved[0], "version": resolved[1], **dict(child)})
            if converted and len(converted) == len(result):
                return converted
        return result

    return rewrite(copy.deepcopy(value))


def _action_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return [copy.deepcopy(dict(item)) for item in value] if isinstance(value, Sequence) else []
    for key in ("candidate_actions", "candidates", "actions"):
        if key in value:
            return _action_records(value[key])
    return [copy.deepcopy(dict(value))]


def _dot_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("candidate_dots", "candidates", "dots"):
            if key in value:
                return _dot_records(value[key])
        return [copy.deepcopy(dict(value))]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [copy.deepcopy(dict(item)) for item in value]
    return []


def _provider_leaks(records: Sequence[Mapping[str, Any]], *, kind: str) -> list[str]:
    leaks: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                name = _key(raw_key)
                if name in _PROVIDER_KEYS:
                    if kind == "workflow" and name == "provider_specific":
                        # This is allowed only when the Workflow states the
                        # provider itself is the human outcome.
                        if isinstance(value, Mapping) and isinstance(value.get("provider_semantics"), Mapping) and value["provider_semantics"].get("is_outcome") is True:
                            continue
                    leaks.append(f"{path}.{raw_key}")
                walk(child, f"{path}.{raw_key}")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    for index, record in enumerate(records):
        walk(record, f"$.{kind}[{index}]")
    return sorted(set(leaks))


def _candidate_gates(
    dots: Sequence[Mapping[str, Any]],
    workflows: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    provider_leaks: Sequence[str],
    *,
    candidate_contributing_responsibilities: int,
    blocked_findings: int,
) -> dict[str, Any]:
    active = False
    persistent = False
    execution = False
    platform_projection = False
    for record in (*dots, *workflows, *actions):
        lifecycle = record.get("lifecycle")
        if isinstance(lifecycle, Mapping) and (
            lifecycle.get("active") is True
            or lifecycle.get("active_surface") is True
            or lifecycle.get("status") == "active"
        ):
            active = True
        activation = record.get("activation")
        if isinstance(activation, Mapping) and activation.get("status") not in (None, "inactive", "candidate"):
            active = True
        if isinstance(activation, Mapping) and (
            activation.get("authorised") is True or activation.get("authorized") is True
        ):
            active = True
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).casefold()
        persistent = persistent or any(token in encoded for token in ("persistence_authority", "canonical-write", "system-version"))
        execution = execution or any(token in encoded for token in ("execution_authority", "execute_source", "invoke_source"))
        platform_projection = platform_projection or "platform_projections" in record
    return {
        "candidate_only": {"passed": not active, "reason": "Every emitted record remains an inactive Candidate."},
        "no_persistence": {"passed": not persistent, "reason": "The compiler emits no canonical or Workplace write authority."},
        "no_execution": {"passed": not execution, "reason": "The compiler emits no provider execution authority."},
        "no_activation": {"passed": not active and not platform_projection, "reason": "Activation and platform projections are absent."},
        "no_provider_leakage": {"passed": not bool(provider_leaks), "reason": "Provider identity stays below the human Action surface."},
        "candidate_contribution": {
            "passed": True,
            "enabled": True,
            "allowed_findings": candidate_contributing_responsibilities,
            "blocked_findings": blocked_findings,
            "reason": "Only extraction findings with an explicit true candidate-contribution decision enter synthesis.",
        },
        "automatic_runtime_invocation": {"passed": True, "enabled": False, "reason": "Compilation is explicit and pure; normal runtime does not call it automatically."},
    }


def _unverified_implementations(dots: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for dot in dots:
        implementations = dot.get("implementations", [])
        if not isinstance(implementations, Sequence) or isinstance(implementations, (str, bytes, bytearray)):
            continue
        for implementation in implementations:
            status = implementation.get("verification", {}).get("status") if isinstance(implementation, Mapping) else None
            if status != "verified":
                count += 1
    return count


def _metrics(
    source_count: int,
    responsibilities: Sequence[Mapping[str, Any]],
    candidate_contributing_responsibilities: int,
    blocked_findings: int,
    dots: Sequence[Mapping[str, Any]],
    workflows: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
    unresolved: Sequence[Mapping[str, Any]],
    provider_leaks: Sequence[str],
    observed: Any,
) -> dict[str, Any]:
    dot_count = len(dots)
    workflow_count = len(workflows)
    action_count = len(actions)
    duplicate_collapses = max(0, candidate_contributing_responsibilities - dot_count)
    relation_edge_counts = {
        relation: sum(1 for item in comparisons if item.get("relation") == relation)
        for relation in ("duplicate", "superset", "complementary", "conflicting", "distinct")
    }
    dot_reuse = len(responsibilities) / dot_count if dot_count else 0.0
    workflow_dot_reuse = (
        sum(len(item.get("dot_refs", [])) for item in workflows) / workflow_count
        if workflow_count
        else 0.0
    )
    action_workflow_reuse = (
        sum(len(item.get("workflow_refs", [])) for item in actions) / action_count
        if action_count
        else 0.0
    )
    observed_items = _as_values(observed, label="observed_compositions") if observed is not None else []
    repeated = 0
    for item in observed_items:
        if isinstance(item, Mapping):
            occurrences = item.get("occurrences", item.get("count", 1))
            try:
                repeated += max(0, int(occurrences) - 1)
            except (TypeError, ValueError):
                continue
    materialized_comparisons = len(comparisons)
    possible_comparisons = len(responsibilities) * max(0, len(responsibilities) - 1) // 2
    lookup_estimate = (
        source_count
        + len(responsibilities)
        + materialized_comparisons
        + dot_count
        + workflow_count
        + action_count
    )
    lookup_estimate_detail = {
        "source_lookups": source_count,
        "responsibility_lookups": len(responsibilities),
        "responsibility_pair_comparisons": materialized_comparisons,
        "possible_responsibility_pairs": possible_comparisons,
        "candidate_record_lookups": dot_count + workflow_count + action_count,
        "unit": "deterministic candidate-record lookup estimate",
    }
    if lookup_estimate != sum(
        value
        for key, value in lookup_estimate_detail.items()
        if key not in {"unit", "possible_responsibility_pairs"}
    ):
        raise CompilerInputError("lookup estimate detail does not sum to lookup estimate")
    return {
        "source_count": source_count,
        "responsibility_count": len(responsibilities),
        "candidate_contributing_responsibilities": candidate_contributing_responsibilities,
        "blocked_findings": blocked_findings,
        "dot_count": dot_count,
        "duplicate_collapses": duplicate_collapses,
        "relation_edge_counts": relation_edge_counts,
        "workflow_count": workflow_count,
        "action_count": action_count,
        "average_responsibilities_per_dot": round(dot_reuse, 6),
        "average_dots_per_workflow": round(workflow_dot_reuse, 6),
        "average_workflows_per_action": round(action_workflow_reuse, 6),
        "reuse": {
            "responsibilities_per_dot": round(dot_reuse, 6),
            "dots_per_workflow": round(workflow_dot_reuse, 6),
            "workflows_per_action": round(action_workflow_reuse, 6),
            "repeated_observations": repeated,
        },
        "provider_leakage": len(provider_leaks),
        "provider_leakage_paths": list(provider_leaks),
        "unresolved_conflicts": len(unresolved),
        "unverified_implementations": _unverified_implementations(dots),
        "lookup_estimate": lookup_estimate,
        "lookup_estimate_detail": lookup_estimate_detail,
        "counts_are_evidence": True,
        "optimisation_target": None,
    }


def _conflicts(
    integration: Mapping[str, Any],
    workflow_synthesis: Mapping[str, Any],
    semantic_unresolved: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [copy.deepcopy(dict(item)) for item in semantic_unresolved]
    for item in integration.get("comparisons", []):
        if item.get("relation") == "conflicting" or "conflict-remains-unresolved" in item.get("uncertainty", []):
            values.append({"stage": "integration", **copy.deepcopy(dict(item))})
    has_workflow_candidate = bool(workflow_synthesis.get("candidates"))
    for key in ("conflicts", "rejected"):
        for item in workflow_synthesis.get(key, []):
            if isinstance(item, Mapping):
                # The Workflow engine explores every deterministic ordering
                # of a Dot graph.  A reverse ordering that cannot connect is
                # an expected rejected branch when another path already has
                # a reusable candidate, not an unresolved semantic conflict.
                if has_workflow_candidate and item.get("kind") == "compatibility":
                    continue
                values.append({"stage": "workflow-synthesis", **copy.deepcopy(dict(item))})
    dedup: dict[str, dict[str, Any]] = {}
    for item in values:
        dedup[_json_sort_key(_canonical(item))] = item
    return [dedup[key] for key in sorted(dedup)]


def _source_refs(catalogue: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source in catalogue["sources"]:
        refs.append(
            {
                "source_id": source["source_id"],
                "provenance_refs": sorted(
                    item["provenance_id"]
                    for item in source.get("provenance", [])
                    if isinstance(item, Mapping) and item.get("provenance_id")
                ),
            }
        )
    return refs


def _stage(name: str, status: str, *, count: int = 0, digest: str | None = None, reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": name, "status": status, "count": count}
    if digest is not None:
        result["output_digest"] = digest
    if reason is not None:
        result["reason"] = reason
    return result


@lru_cache(maxsize=1)
def _candidate_graph_validator() -> Draft202012Validator:
    """Load and check the packaged Candidate Graph schema by package path."""

    try:
        schema_path = files("fractal.schemas").joinpath(GRAPH_SCHEMA_FILENAME)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as error:
        raise CompilerInputError(
            f"Candidate Graph schema is unavailable or invalid: {GRAPH_SCHEMA_FILENAME}"
        ) from error
    return Draft202012Validator(schema)


def _validate_candidate_graph_schema(value: Mapping[str, Any]) -> None:
    """Run the portable schema gate before any semantic or authority gates."""

    try:
        _candidate_graph_validator().validate(value)
    except ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path)
        location = f" at {path}" if path else ""
        raise CompilerInputError(
            f"Candidate Graph schema validation failed{location}: {error.message}"
        ) from error


def compile_capability_graph(
    source_catalogue: Mapping[str, Any],
    source_documents: Any = None,
    observed_compositions: Any = None,
    human_intent_evidence: Any = None,
    naming_evidence: Any = None,
    semantic_comparison_evidence: Any = None,
    *,
    workflow_contracts: Any = None,
    verification_boundaries: Any = None,
    outcome_evidence: Any = None,
    source_claims: Any = None,
    source_documents_or_claims: Any = None,
    observed_execution_composition_evidence: Any = None,
    execution_composition_evidence: Any = None,
    human_intent_naming_evidence: Any = None,
    naming_system_evidence: Any = None,
    bounded_semantic_comparison_evidence: Any = None,
) -> dict[str, Any]:
    """Compile explicit genesis evidence into one inactive candidate graph.

    The six positional inputs are intentionally explicit.  There is no
    ``old_actions``, ``dot_groups``, ``user_surface``, or other legacy-surface
    parameter.  ``outcome_evidence`` is an optional compact complement to the
    observed composition envelope; it is still evidence, not a candidate
    record.
    """

    if source_documents is None:
        source_documents = (
            source_documents_or_claims
            if source_documents_or_claims is not None
            else source_claims
        )
    elif source_documents_or_claims is not None:
        raise CompilerInputError("Provide one compact Source document/claims input")
    elif source_claims is not None:
        source_documents = {"documents": source_documents, "claims": source_claims}
    if observed_compositions is None:
        observed_compositions = (
            observed_execution_composition_evidence
            if observed_execution_composition_evidence is not None
            else execution_composition_evidence
        )
    elif observed_execution_composition_evidence is not None or execution_composition_evidence is not None:
        raise CompilerInputError("Provide one observed execution-composition evidence input")
    if human_intent_evidence is None:
        human_intent_evidence = human_intent_naming_evidence
    elif human_intent_naming_evidence is not None:
        raise CompilerInputError("Provide one human-intent evidence input")
    if naming_evidence is None:
        naming_evidence = naming_system_evidence
    elif naming_system_evidence is not None:
        raise CompilerInputError("Provide one Naming System evidence input")
    if semantic_comparison_evidence is None:
        semantic_comparison_evidence = bounded_semantic_comparison_evidence
    elif bounded_semantic_comparison_evidence is not None:
        raise CompilerInputError("Provide one semantic comparison evidence input")

    catalogue = _source_catalogue(source_catalogue)
    source_ids = [item["source_id"] for item in catalogue["sources"]]
    documents, claims = _source_material(source_documents, source_ids)
    _walk_input(documents, path="$.source_documents", allow_provider=True)
    _walk_input(claims, path="$.source_claims", allow_provider=True)
    if observed_compositions is None:
        raise CompilerInputError("observed_compositions (execution-composition evidence) is required")
    if human_intent_evidence is None:
        raise CompilerInputError("human_intent_evidence is required")
    if naming_evidence is None:
        raise CompilerInputError("naming_evidence (Naming System evidence) is required")
    comparison_items = _comparison_items(semantic_comparison_evidence)
    # The source document parser owns document privacy and legacy-hint gates;
    # these preflights cover wrappers that would otherwise be ignored by it.
    _walk_input(observed_compositions, path="$.observed_compositions")
    _walk_input(human_intent_evidence, path="$.human_intent_evidence", allow_workflow_target=True)
    _walk_input(naming_evidence, path="$.naming_evidence")
    _walk_input(workflow_contracts, path="$.workflow_contracts")
    _walk_input(verification_boundaries, path="$.verification_boundaries")
    _walk_input(outcome_evidence, path="$.outcome_evidence")

    digest_input = {
        "source_catalogue": catalogue,
        "source_documents": documents,
        "source_claims": claims,
        "observed_compositions": observed_compositions,
        "human_intent_evidence": human_intent_evidence,
        "naming_evidence": naming_evidence,
        "semantic_comparison_evidence": comparison_items,
        "workflow_contracts": workflow_contracts,
        "verification_boundaries": verification_boundaries,
        "outcome_evidence": outcome_evidence,
        "compiler_method": COMPILER_METHOD_VERSION,
    }
    input_digest = _digest(digest_input)
    observed_for_engine_input = _canonical(observed_compositions)
    intent_for_engine_input = _canonical(human_intent_evidence)
    naming_for_engine_input = _canonical(naming_evidence)
    outcome_for_engine_input = _canonical(outcome_evidence) if outcome_evidence is not None else None

    stages: list[dict[str, Any]] = []
    extracted = _extract_records(catalogue, documents, claims)
    stages.append(_stage("extraction", "completed", count=len(extracted), digest=_digest(extracted)))
    candidate_records, blocked_records = _candidate_contribution_records(extracted)

    enriched, semantic_unresolved = _semantic_overlays(extracted, comparison_items)
    # Keep the overlay on the complete evidence set so numeric comparison
    # endpoints retain their extraction ordering.  Only explicitly allowed
    # findings cross the next compiler boundary.
    candidate_enriched = [
        record
        for record in enriched
        if record.get("candidate_contribution_allowed") is True
    ]
    if len(candidate_enriched) != len(candidate_records):
        # This should be unreachable because the gate was checked above, but
        # retaining a fail-closed assertion protects the synthesis boundary if
        # an extraction engine changes its record shape.
        raise CompilerInputError("candidate contribution gate changed during evidence overlay")
    # Integration is intentionally called before the separate Dot synthesis
    # call.  Its comparison records are the five-relation evidence ledger;
    # Dot synthesis receives the same allowed bottom-up mappings as its only
    # seed.  Blocked findings remain in ``extracted`` for graph evidence.
    if candidate_enriched:
        try:
            integration = integrate_capabilities(candidate_enriched)
        except (CapabilityIntegrationError, ValueError) as error:
            raise CompilerInputError("five-relation capability integration failed") from error
        stages.append(
            _stage(
                "integration",
                "completed",
                count=len(integration.get("comparisons", [])),
                digest=_digest(integration.get("comparisons", [])),
            )
        )
        # Integration already owns Candidate Dot synthesis.  Reusing its
        # isolated output avoids repeating the complete comparison pass and
        # guarantees the comparison ledger and emitted Dots describe the same
        # exact integration decision.
        dots = _dot_records(integration.get("candidates", []))
        dots.sort(key=lambda item: (_text(item.get("dot_id")), _text(item.get("version"))))
        stages.append(_stage("dot-synthesis", "completed", count=len(dots), digest=_digest(dots)))
    else:
        integration = {
            "comparisons": [],
            "candidates": [],
            "candidate_dots": [],
            "ignored": [],
        }
        stages.append(
            _stage(
                "integration",
                "skipped",
                reason="No extraction findings passed the candidate-contribution gate.",
            )
        )
        dots = []
        stages.append(
            _stage(
                "dot-synthesis",
                "skipped",
                reason="No extraction findings passed the candidate-contribution gate.",
            )
        )

    workflow_synthesis: dict[str, Any]
    workflows: list[dict[str, Any]] = []
    if dots and observed_compositions:
        observed_for_engine = _rewrite_observed_paths(observed_for_engine_input, dots)
        reusable_for_engine: Any = observed_for_engine_input
        if outcome_evidence is not None:
            outcome_for_engine = _rewrite_observed_paths(outcome_for_engine_input, dots)
            reusable_for_engine = [observed_for_engine, outcome_for_engine]
        contracts = (
            _rewrite_workflow_contract_targets(workflow_contracts, dots)
            if workflow_contracts is not None
            else _infer_contracts(dots)
        )
        try:
            workflow_synthesis = synthesize_candidate_workflows(
                _safe_workflow_dots(dots),
                contracts=contracts,
                verification_boundaries=verification_boundaries,
                reusable_outcome_evidence=reusable_for_engine,
                observed_compositions=observed_for_engine,
            )
        except (ValueError, TypeError) as error:
            raise CompilerInputError("Candidate Workflow synthesis failed") from error
        workflows = _workflow_records(workflow_synthesis.get("candidates", []))
        workflows.sort(key=lambda item: (_text(item.get("workflow_id")), _text(item.get("version"))))
        stages.append(_stage("workflow-synthesis", "completed", count=len(workflows), digest=_digest(workflows)))
    else:
        workflow_synthesis = {
            "candidates": [],
            "candidate_workflows": [],
            "workflows": [],
            "conflicts": [],
            "rejected": [],
        }
        stages.append(
            _stage(
                "workflow-synthesis",
                "skipped",
                reason="Candidate Dots or observed reusable execution-composition evidence is missing.",
            )
        )

    actions: list[dict[str, Any]] = []
    action_induction: dict[str, Any] = {
        "candidate_actions": [],
        "candidates": [],
        "actions": [],
        "anti_seed_attestation": {
            "attested": True,
            "independent": True,
            "no_inherited_action": True,
            "no_legacy_action": True,
            "basis": "Candidate Workflows plus explicit human intent and Naming System evidence only.",
        },
    }
    if workflows and human_intent_evidence and naming_evidence:
        try:
            # The maintained induction engine includes compression, but the
            # compiler calls the explicit compression boundary as a second
            # deterministic stage so the receipt makes the order inspectable.
            intent_for_engine = _rewrite_human_intent_targets(intent_for_engine_input, workflows)
            induced = induce_candidate_actions(workflows, intent_for_engine, naming_for_engine_input)
            stages.append(
                _stage(
                    "action-induction",
                    "completed",
                    count=len(_action_records(induced.get("candidate_actions", induced))),
                    digest=_digest(induced.get("candidate_actions", induced)),
                )
            )
            action_induction = compress_candidate_actions(
                workflows, intent_for_engine, naming_for_engine_input
            )
            actions = _action_records(action_induction.get("candidate_actions", action_induction))
            actions.sort(key=lambda item: (_text(item.get("action_id")), _text(item.get("version"))))
            stages.append(_stage("action-compression", "completed", count=len(actions), digest=_digest(actions)))
        except (ValueError, TypeError) as error:
            raise CompilerInputError("Candidate Action induction/compression failed") from error
    else:
        stages.append(
            _stage(
                "action-induction",
                "skipped",
                reason="Candidate Workflows, human-intent evidence, and Naming System evidence are all required.",
            )
        )
        stages.append(
            _stage(
                "action-compression",
                "skipped",
                reason="No independently induced Candidate Actions are available to compress.",
            )
        )

    # Integration validates every Dot at construction; the complete graph
    # schema below is the independent compiler-boundary validation.
    for workflow in workflows:
        try:
            validate_workflow(workflow)
        except (ValueError, TypeError) as error:
            raise CompilerInputError("Candidate Workflow output failed candidate validation") from error
    if actions:
        try:
            actions = validate_action_graph(actions, workflow_records=workflows)
        except (ValueError, TypeError) as error:
            raise CompilerInputError("Candidate Action output failed candidate validation") from error

    leaks = _provider_leaks(workflows, kind="workflow") + _provider_leaks(actions, kind="action")
    leaks = sorted(set(leaks))
    unresolved = _conflicts(integration, workflow_synthesis, semantic_unresolved)
    metrics = _metrics(
        len(source_ids), extracted, len(candidate_records), len(blocked_records), dots, workflows, actions,
        integration.get("comparisons", []), unresolved, leaks, observed_compositions,
    )
    gates = _candidate_gates(
        dots,
        workflows,
        actions,
        leaks,
        candidate_contributing_responsibilities=len(candidate_records),
        blocked_findings=len(blocked_records),
    )
    gates["required_evidence"] = {
        "passed": bool(extracted) and bool(observed_compositions) and bool(human_intent_evidence) and bool(naming_evidence) and bool(comparison_items),
        "source_documents_supplied": bool(documents or claims),
        "responsibility_evidence": bool(extracted),
        "execution_composition_evidence": bool(observed_compositions),
        "human_intent_evidence": bool(human_intent_evidence),
        "naming_evidence": bool(naming_evidence),
        "semantic_comparison_evidence": bool(comparison_items),
    }
    gates["stage_order"] = {
        "passed": [item["stage"] for item in stages] == [
            "extraction", "integration", "dot-synthesis", "workflow-synthesis", "action-induction", "action-compression"
        ],
        "stages": [item["stage"] for item in stages],
    }

    graph: dict[str, Any] = {
        "$schema": GRAPH_SCHEMA_URI,
        "record_type": GRAPH_RECORD_TYPE,
        "record_version": GRAPH_RECORD_VERSION,
        "schema_version": GRAPH_RECORD_VERSION,
        "version": GRAPH_VERSION,
        "compiler": {"method": COMPILER_METHOD, "version": COMPILER_METHOD_VERSION},
        "input_digest": input_digest,
        "candidate_only": True,
        # These are references, not the validated Source records themselves.
        "source_refs": _source_refs(catalogue),
        "extraction_evidence": copy.deepcopy(extracted),
        "comparison_evidence": sorted(
            copy.deepcopy(list(integration.get("comparisons", []))),
            key=lambda item: _text(item.get("comparison_id")),
        ),
        "dots": dots,
        "workflows": workflows,
        "actions": actions,
        "unresolved_conflicts": unresolved,
        "metrics": metrics,
        "gates": gates,
        "pipeline": stages,
        "persistence": {"created": False, "workplace_write": False, "registry_write": False},
        "execution": {"performed": False, "source_execution": False, "provider_execution": False},
        "activation": {"performed": False, "authorised": False, "active_surface": False},
    }
    return validate_candidate_graph(graph)


def validate_candidate_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the detached candidate graph contract without persistence."""

    if not isinstance(value, Mapping):
        raise CompilerInputError("candidate graph must be a mapping")
    graph = copy.deepcopy(dict(value))
    _validate_candidate_graph_schema(graph)
    if graph.get("record_type") != GRAPH_RECORD_TYPE or graph.get("record_version") != GRAPH_RECORD_VERSION:
        raise CompilerInputError("unsupported candidate graph schema")
    if not isinstance(graph.get("input_digest"), str) or re.fullmatch(r"[a-f0-9]{64}", graph["input_digest"]) is None:
        raise CompilerInputError("candidate graph input_digest must be SHA-256")
    refs = graph.get("source_refs")
    if not isinstance(refs, list):
        raise CompilerInputError("candidate graph source_refs are required")
    for index, ref in enumerate(refs):
        if not isinstance(ref, Mapping) or not _text(ref.get("source_id")):
            raise CompilerInputError(f"candidate graph source_refs[{index}] is invalid")
        if any(key in ref for key in ("name", "source_type", "donor", "upstream", "licence", "constraints")):
            raise CompilerInputError("candidate graph must retain Source references, not Source copies")
    for key in ("extraction_evidence", "comparison_evidence", "dots", "workflows", "actions", "unresolved_conflicts", "pipeline"):
        if not isinstance(graph.get(key), list):
            raise CompilerInputError(f"candidate graph {key} must be a list")
    for item in graph["extraction_evidence"]:
        try:
            validate_responsibility_record(item)
        except (ValueError, TypeError) as error:
            raise CompilerInputError("candidate graph extraction evidence is invalid") from error
    for dot in graph["dots"]:
        try:
            validated = validate_capability_dot(dot, require_active=False)
        except (ValueError, TypeError) as error:
            raise CompilerInputError("candidate graph contains an invalid Candidate Dot") from error
        lifecycle = validated.get("lifecycle", {})
        if lifecycle.get("state") != "candidate" or lifecycle.get("active") is True or validated.get("activation", {}).get("status") != "inactive":
            raise CompilerBoundaryError("candidate graph contains an active Dot")
    for workflow in graph["workflows"]:
        try:
            validated = validate_workflow(workflow)
        except (ValueError, TypeError) as error:
            raise CompilerInputError("candidate graph contains an invalid Candidate Workflow") from error
        if validated.get("lifecycle", {}).get("status") != "candidate" or validated.get("lifecycle", {}).get("active") is True:
            raise CompilerBoundaryError("candidate graph contains an active Workflow")
    if graph["actions"]:
        try:
            actions = validate_action_graph(graph["actions"], workflow_records=graph["workflows"])
        except (ValueError, TypeError) as error:
            raise CompilerInputError("candidate graph contains an invalid Candidate Action") from error
        for action in actions:
            if action.get("lifecycle", {}).get("status") != "candidate" or action.get("activation", {}).get("status") != "inactive":
                raise CompilerBoundaryError("candidate graph contains an active Action")
            if "platform_projections" in action:
                raise CompilerBoundaryError("candidate graph cannot contain a platform projection")
    encoded = json.dumps(graph, ensure_ascii=False, sort_keys=True)
    if '"record_type": "capability-source"' in encoded:
        raise CompilerBoundaryError("candidate graph must not copy Source definitions")
    return graph


# Descriptive aliases keep the single implementation discoverable without
# creating alternative orchestration paths.
compile_genesis = compile_capability_graph
compile_candidate_graph = compile_capability_graph
compile_capabilities = compile_capability_graph
orchestrate_genesis = compile_capability_graph
validate_graph = validate_candidate_graph


class CapabilityCompiler:
    """Explicit, stateless facade over :func:`compile_capability_graph`."""

    def compile(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return compile_capability_graph(*args, **kwargs)


__all__ = [
    "CapabilityCompiler",
    "CapabilityCompilerError",
    "CompilerBoundaryError",
    "CompilerDependencyError",
    "CompilerInputError",
    "COMPILER_METHOD",
    "COMPILER_METHOD_VERSION",
    "GRAPH_SCHEMA_FILENAME",
    "GRAPH_RECORD_TYPE",
    "GRAPH_RECORD_VERSION",
    "GRAPH_SCHEMA_URI",
    "GRAPH_VERSION",
    "compile_candidate_graph",
    "compile_capabilities",
    "compile_capability_graph",
    "compile_genesis",
    "orchestrate_genesis",
    "validate_candidate_graph",
    "validate_graph",
]
