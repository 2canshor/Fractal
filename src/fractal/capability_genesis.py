"""Reproducible, non-persistent helpers for Capability System Genesis.

The module verifies already-retrieved Source bytes, prepares compact in-memory
documents, and records a finding or No Finding for every Source.  It performs
no network access, does not execute donor content, and never writes raw Source
bytes into the canonical Workplace.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fractal.capability_extraction import (
    ExtractionValidationError,
    extract_responsibilities,
    validate_responsibility_record,
)
from fractal.capability_source import validate_source_catalogue
from fractal.storage import canonical_json_bytes

GENESIS_EXTRACTION_RECORD_TYPE = "capability-genesis-extraction-coverage"
GENESIS_EXTRACTION_RECORD_VERSION = 1
GENESIS_EXTRACTION_METHOD = "verified-full-source-responsibility-inspection"
GENESIS_EXTRACTION_METHOD_VERSION = "1.0.0"


class CapabilityGenesisError(ValueError):
    """Genesis input is incomplete, unverified, or non-replayable."""


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _description(source: Mapping[str, Any]) -> str | None:
    descriptions = []
    for raw in source.get("claimed_capabilities", []):
        if not isinstance(raw, str):
            continue
        prefix, separator, value = raw.partition(":")
        if separator and prefix.strip().casefold() == "description" and value.strip():
            descriptions.append(value.strip())
    if not descriptions:
        return None
    return sorted(set(descriptions), key=str.casefold)[0]


def _name(source: Mapping[str, Any]) -> str | None:
    names = []
    for raw in source.get("claimed_capabilities", []):
        if not isinstance(raw, str):
            continue
        prefix, separator, value = raw.partition(":")
        if separator and prefix.strip().casefold() in {"name", "title"} and value.strip():
            names.append(value.strip())
    return sorted(set(names), key=str.casefold)[0] if names else None


def _body(raw: str) -> str:
    """Drop YAML frontmatter without parsing donor-specific YAML extensions."""

    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return raw
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    return "\n".join(lines[end + 1 :]) if end is not None else raw


def load_verified_source_documents(
    catalogue: Mapping[str, Any],
    *,
    donor_roots: Mapping[str, str | Path],
) -> dict[str, dict[str, Any]]:
    """Load exact Skill documents from explicit transient archive roots.

    Every Skill record must resolve below its donor root and match the retained
    ``file_sha256``.  Library/spec Sources deliberately have no raw document
    and are handled as No Finding unless their retained metadata says more.
    """

    validated = validate_source_catalogue(catalogue)
    roots = {key: Path(value).resolve() for key, value in donor_roots.items()}
    documents: dict[str, dict[str, Any]] = {}
    for source in validated["sources"]:
        if source["source_type"] != "skill":
            continue
        source_id = source["source_id"]
        donor_id = source["donor"]["donor_id"]
        root = roots.get(donor_id)
        if root is None:
            raise CapabilityGenesisError(f"missing transient archive root for donor {donor_id}")
        relative = source["upstream"].get("path")
        expected = source["upstream"].get("file_sha256")
        if not relative or not expected:
            raise CapabilityGenesisError(f"Skill Source {source_id} lacks path or file_sha256")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise CapabilityGenesisError(f"Skill Source {source_id} escapes donor root") from error
        if not path.is_file():
            raise CapabilityGenesisError(f"Skill Source {source_id} file is missing: {relative}")
        raw_bytes = path.read_bytes()
        actual = hashlib.sha256(raw_bytes).hexdigest()
        if actual != expected:
            raise CapabilityGenesisError(
                f"Skill Source {source_id} file hash mismatch: expected {expected}, got {actual}"
            )
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CapabilityGenesisError(f"Skill Source {source_id} is not UTF-8") from error
        description = _description(source)
        name = _name(source)
        frontmatter = {}
        if name:
            frontmatter["name"] = name
        if description:
            frontmatter["description"] = description
        documents[source_id] = {
            "frontmatter": frontmatter,
            "text": _body(raw),
            "document_sha256": actual,
            "path": relative,
        }
    return documents


def build_extraction_coverage(
    catalogue: Mapping[str, Any],
    source_documents: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect every Source and retain compact findings plus No-Finding decisions."""

    validated = validate_source_catalogue(catalogue)
    known = {item["source_id"] for item in validated["sources"]}
    unknown = sorted(set(source_documents) - known)
    if unknown:
        raise CapabilityGenesisError(f"source_documents contain unknown Sources: {unknown}")

    findings: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for source in validated["sources"]:
        source_id = source["source_id"]
        try:
            records = extract_responsibilities(source, source_documents.get(source_id))
            records = [validate_responsibility_record(item, source=source) for item in records]
        except (ExtractionValidationError, ValueError, TypeError) as error:
            raise CapabilityGenesisError(f"Source inspection failed for {source_id}") from error
        records = sorted(records, key=lambda item: item["evidence_digest"])
        findings.extend(records)
        decisions.append(
            {
                "source_id": source_id,
                "finding": "responsibilities-found" if records else "no-finding",
                "responsibility_count": len(records),
                "evidence_digests": [item["evidence_digest"] for item in records],
                "candidate_contribution_count": sum(
                    item["candidate_contribution_allowed"] is True for item in records
                ),
                "reason": (
                    "The retained Source description or body exposed at least one "
                    "bounded responsibility."
                    if records
                    else "No bounded reusable responsibility was present in retained "
                    "Source evidence."
                ),
            }
        )

    decisions.sort(key=lambda item: item["source_id"])
    findings.sort(
        key=lambda item: (
            item["normalized_signature"],
            tuple(item["source_refs"]),
            item["evidence_digest"],
        )
    )
    source_count = len(validated["sources"])
    finding_sources = sum(item["finding"] == "responsibilities-found" for item in decisions)
    result = {
        "record_type": GENESIS_EXTRACTION_RECORD_TYPE,
        "record_version": GENESIS_EXTRACTION_RECORD_VERSION,
        "method": {
            "name": GENESIS_EXTRACTION_METHOD,
            "version": GENESIS_EXTRACTION_METHOD_VERSION,
        },
        "catalogue_digest": _digest(validated),
        "source_count": source_count,
        "source_decision_count": len(decisions),
        "finding_source_count": finding_sources,
        "no_finding_source_count": source_count - finding_sources,
        "responsibility_count": len(findings),
        "candidate_contribution_count": sum(
            item["candidate_contribution_allowed"] is True for item in findings
        ),
        "source_decisions": decisions,
        "responsibilities": copy.deepcopy(findings),
        "raw_contents_persisted": False,
        "source_execution_performed": False,
        "activation_performed": False,
    }
    result["evidence_digest"] = _digest(result)
    return result


def build_replayable_compiler_material(
    catalogue: Mapping[str, Any],
    extraction_coverage: Mapping[str, Any],
) -> dict[str, Any]:
    """Project compact extraction findings into deterministic compiler input."""

    validated = validate_source_catalogue(catalogue)
    if extraction_coverage.get("record_type") != GENESIS_EXTRACTION_RECORD_TYPE:
        raise CapabilityGenesisError("extraction coverage record type is invalid")
    if extraction_coverage.get("catalogue_digest") != _digest(validated):
        raise CapabilityGenesisError("extraction coverage does not match the Source catalogue")
    if extraction_coverage.get("source_decision_count") != len(validated["sources"]):
        raise CapabilityGenesisError("extraction coverage is not exhaustive")

    source_ids = {item["source_id"] for item in validated["sources"]}
    claims: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    retained_fields = (
        "responsibility",
        "inputs",
        "outputs",
        "preconditions",
        "side_effects",
        "provider_dependency",
        "knowledge",
        "procedure_outline",
        "verification",
        "failure_recovery",
        "source_refs",
        "provenance_refs",
        "confidence",
        "uncertainties",
    )
    for raw in extraction_coverage.get("responsibilities", []):
        if not isinstance(raw, Mapping):
            raise CapabilityGenesisError("extraction responsibility is not a mapping")
        refs = raw.get("source_refs")
        if (
            not isinstance(refs, list)
            or len(refs) != 1
            or refs[0] not in source_ids
        ):
            raise CapabilityGenesisError(
                "each compact extraction finding must belong to exactly one known Source"
            )
        claims[refs[0]].append(
            {key: copy.deepcopy(raw[key]) for key in retained_fields if key in raw}
        )
    claims = {
        source_id: sorted(values, key=_digest)
        for source_id, values in sorted(claims.items())
        if values
    }
    compiler_catalogue = copy.deepcopy(validated)
    for source in compiler_catalogue["sources"]:
        # The compiler consumes the retained extraction findings below.  Raw
        # Source claims cannot become an untracked second responsibility path.
        source["claimed_capabilities"] = []
    return {
        "record_type": "capability-genesis-compiler-material",
        "record_version": 1,
        "source_catalogue": compiler_catalogue,
        "source_claims": claims,
        "source_count": len(validated["sources"]),
        "source_claim_count": sum(len(values) for values in claims.values()),
        "source_decision_count": extraction_coverage["source_decision_count"],
        "extraction_evidence_digest": extraction_coverage["evidence_digest"],
        "raw_contents_persisted": False,
    }


__all__ = [
    "CapabilityGenesisError",
    "GENESIS_EXTRACTION_METHOD",
    "GENESIS_EXTRACTION_METHOD_VERSION",
    "GENESIS_EXTRACTION_RECORD_TYPE",
    "GENESIS_EXTRACTION_RECORD_VERSION",
    "build_extraction_coverage",
    "build_replayable_compiler_material",
    "load_verified_source_documents",
]
