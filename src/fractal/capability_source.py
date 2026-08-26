"""The System-owned contract for raw external capability Sources.

A Source is evidence about an external library, Skill, or specification.  It
is deliberately not a capability registry entry: a Source has no invocation,
resolution, execution, activation, or persistence authority.  This module
only validates metadata supplied by an explicit genesis/evolution intake and
provides deterministic, local catalogue helpers.  It never retrieves or
executes upstream material.

The durable catalogue is a Workplace concern.  The helpers here own the
generic shape and safety gates that a Workplace writer must satisfy.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from fractal.storage import canonical_json_bytes

SCHEMA_URI = "https://fractal.local/schemas/capability-source.schema.json"
SOURCE_RECORD_TYPE = "capability-source"
SOURCE_RECORD_VERSION = 1
CATALOGUE_RECORD_TYPE = "capability-source-catalogue"
CATALOGUE_RECORD_VERSION = 1

SOURCE_TYPES = frozenset({"library", "skill", "spec"})
LICENCE_STATUSES = frozenset({"verified", "unclear", "missing", "incompatible"})
LICENCE_STATUS_ALIASES = {
    "verified-file": "verified",
    "verified-license": "verified",
    "verified-licence": "verified",
    "unknown": "unclear",
    "unverified": "unclear",
    "declared-no-licence-file": "missing",
    "declared-no-license-file": "missing",
}
LICENCE_RESTRICTION_ORDER = {
    "verified": 0,
    "unclear": 1,
    "missing": 2,
    "incompatible": 3,
}

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~=,-]*$")
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(token|password|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|credential|private[_-]?key|authorization)(?:$|[_-])",
    re.IGNORECASE,
)
_HOME_PATH_MARKER = "/" + "Users" + "/"
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s'\"=:(\[])"
    + r"(?:"
    + re.escape(_HOME_PATH_MARKER)
    + r"|/home/|/private/tmp/|/tmp/|/var/|/run/|[A-Za-z]:[\\/])"
)
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

_TOP_LEVEL_ALIASES = frozenset(
    {
        "$schema",
        "record_type",
        "record_version",
        "source_id",
        "name",
        "identity",
        "source_type",
        "type",
        "donor",
        "donor_id",
        "upstream",
        "upstream_locator",
        "locator",
        "source_url",
        "commit",
        "exact_commit",
        "tag",
        "exact_tag",
        "version",
        "exact_version",
        "path",
        "source_path",
        "content_sha256",
        "content_hash",
        "tree_sha256",
        "tree_hash",
        "file_sha256",
        "file_hash",
        "licence",
        "license",
        "constraints",
        "retrieved_at",
        "claimed_capabilities",
        "declarations",
        "declared",
        "tools",
        "scripts",
        "network_effects",
        "network",
        "write_effects",
        "writes",
        "dependencies",
        "provider_hints",
        "compatibility",
        "declared_tools",
        "declared_scripts",
        "declared_network_effects",
        "declared_write_effects",
        "declared_dependencies",
        "declared_provider_hints",
        "declared_compatibility",
        "status",
        "source_only",
        "provenance",
    }
)
_UPSTREAM_ALIASES = frozenset(
    {
        "locator",
        "upstream_locator",
        "source_url",
        "commit",
        "exact_commit",
        "tag",
        "exact_tag",
        "version",
        "exact_version",
        "path",
        "source_path",
        "content_sha256",
        "content_hash",
        "tree_sha256",
        "tree_hash",
        "file_sha256",
        "file_hash",
        "hash",
    }
)
_DECLARATION_KEYS = frozenset(
    {
        "tools",
        "scripts",
        "network_effects",
        "network",
        "write_effects",
        "writes",
        "dependencies",
        "provider_hints",
        "compatibility",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "provenance_id",
        "observed_at",
        "retrieved_at",
        "locator",
        "upstream_locator",
        "source_url",
        "commit",
        "exact_commit",
        "tag",
        "exact_tag",
        "version",
        "exact_version",
        "path",
        "source_path",
        "content_sha256",
        "content_hash",
        "tree_sha256",
        "tree_hash",
        "file_sha256",
        "file_hash",
        "evidence",
        "evidence_ids",
        "notes",
        "hash",
    }
)
_PORTABLE_LIST_KEYS = frozenset(
    {
        "path",
        "source_path",
        "target",
        "targets",
        "location",
        "locations",
        "root",
        "roots",
    }
)


class SourceValidationError(ValueError):
    """Raised when a Source or catalogue violates the canonical contract."""


class SourceLicenceError(SourceValidationError):
    """Raised when a licence gate would allow unsafe candidate content reuse."""


class SourceStorageError(RuntimeError):
    """Raised when a catalogue cannot be atomically persisted or read back."""


# Compatibility spellings keep callers at the Source boundary without adding
# another authority or lifecycle hierarchy.
CapabilitySourceError = SourceValidationError
SourceError = SourceValidationError


def _schema_path() -> Path:
    return Path(__file__).parent / "schemas" / "capability-source.schema.json"


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _normalise_text(value: Any, *, field: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _normalise_id(value: Any, *, field: str) -> str:
    text = _normalise_text(value, field=field)
    assert text is not None
    if _ID_RE.fullmatch(text) is None:
        raise SourceValidationError(f"{field} is not a portable identifier: {text}")
    return text


def _normalise_source_type(value: Any) -> str:
    source_type = _normalise_text(value, field="source_type")
    assert source_type is not None
    source_type = source_type.lower().replace("-", "_")
    if source_type == "specification":
        source_type = "spec"
    if source_type not in SOURCE_TYPES:
        raise SourceValidationError(f"Unsupported Source type: {source_type}")
    return source_type


def _normalise_hash(value: Any, *, field: str, allow_none: bool = True) -> str | None:
    if value is None and allow_none:
        return None
    text = _normalise_text(value, field=field)
    assert text is not None
    text = text.lower()
    if _SHA256_RE.fullmatch(text) is None:
        raise SourceValidationError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _normalise_commit(value: Any, *, field: str = "commit") -> str | None:
    if value is None:
        return None
    text = _normalise_text(value, field=field)
    assert text is not None
    text = text.lower()
    if _COMMIT_RE.fullmatch(text) is None:
        raise SourceValidationError(f"{field} must be an exact 40-character commit")
    return text


def _portable_relative_path(value: Any, *, field: str, allow_none: bool = True) -> str | None:
    if value is None and allow_none:
        return None
    text = _normalise_text(value, field=field)
    assert text is not None
    if "\\" in text or text.startswith(("/", "~")):
        raise SourceValidationError(f"{field} must be a portable relative path")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SourceValidationError(f"{field} escapes a portable root")
    if len(path.parts) == 1 and ":" in path.parts[0]:
        raise SourceValidationError(f"{field} cannot use a machine-specific drive")
    return path.as_posix()


def _normalise_datetime(value: Any, *, field: str) -> str:
    text = _normalise_text(value, field=field)
    assert text is not None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise SourceValidationError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise SourceValidationError(f"{field} must include a timezone")
    return text


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_locator(value: Any, *, field: str = "upstream.locator") -> str:
    locator = _normalise_text(value, field=field)
    assert locator is not None
    if not _URI_SCHEME_RE.match(locator):
        raise SourceValidationError(f"{field} must be an explicit URI")
    parsed = urlsplit(locator)
    if parsed.scheme.lower() == "file":
        raise SourceValidationError(f"{field} cannot persist a machine-local file URI")
    if parsed.username or parsed.password:
        raise SourceValidationError(f"{field} cannot contain credentials")
    if re.search(
        r"(?:^|[&?])(?:token|password|secret|api[_-]?key|authorization)\s*=",
        parsed.query,
        re.IGNORECASE,
    ):
        raise SourceValidationError(f"{field} cannot contain credential query parameters")
    if parsed.scheme.lower() in {"http", "https", "git", "ssh"} and not parsed.netloc:
        raise SourceValidationError(f"{field} has no upstream host")
    return locator


def _merge_alias(mapping: Mapping[str, Any], aliases: Sequence[str], *, field: str) -> Any:
    values = [mapping[key] for key in aliases if key in mapping and mapping[key] is not None]
    if not values:
        return None
    first = values[0]
    if any(value != first for value in values[1:]):
        raise SourceValidationError(f"Conflicting aliases for {field}")
    return first


def _normalise_licence(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"status": "verified", "spdx": value}
    if value is None:
        value = {"status": "missing"}
    if not isinstance(value, Mapping):
        raise SourceValidationError("licence must be a record or SPDX string")
    allowed = {"status", "spdx", "evidence", "constraints", "candidate_content_allowed"}
    unknown = set(value).difference(allowed)
    if unknown:
        raise SourceValidationError(f"Unknown licence fields: {sorted(unknown)}")
    raw_status = _normalise_text(value.get("status", "missing"), field="licence.status")
    assert raw_status is not None
    status = LICENCE_STATUS_ALIASES.get(raw_status.lower(), raw_status.lower())
    if status not in LICENCE_STATUSES:
        raise SourceValidationError(f"Unsupported licence status: {raw_status}")
    spdx = _normalise_text(value.get("spdx"), field="licence.spdx", allow_none=True)
    evidence = _normalise_text(value.get("evidence"), field="licence.evidence", allow_none=True)
    constraints = value.get("constraints", [])
    if not isinstance(constraints, list) or any(
        not isinstance(item, str) or not item.strip() for item in constraints
    ):
        raise SourceValidationError("licence.constraints must contain text")
    constraints = sorted(set(item.strip() for item in constraints))
    candidate_allowed = value.get("candidate_content_allowed")
    if candidate_allowed is None:
        candidate_allowed = status == "verified"
    if not isinstance(candidate_allowed, bool):
        raise SourceValidationError("licence.candidate_content_allowed must be boolean")
    if status != "verified" and candidate_allowed:
        raise SourceLicenceError(
            "Unclear, missing, or incompatible licence cannot permit candidate content reuse"
        )
    if status == "verified" and not spdx:
        raise SourceLicenceError("A verified licence requires an SPDX identifier")
    return {
        "status": status,
        "spdx": spdx,
        "evidence": evidence,
        "constraints": constraints,
        "candidate_content_allowed": candidate_allowed,
    }


def _normalise_constraint(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, list):
        value = {"notes": value}
    if not isinstance(value, Mapping):
        raise SourceValidationError("constraints must be a record")
    allowed = {
        "content_reuse",
        "runtime_dependency",
        "execution_authority",
        "persistence_authority",
        "notes",
    }
    unknown = set(value).difference(allowed)
    if unknown:
        raise SourceValidationError(f"Unknown Source constraint fields: {sorted(unknown)}")
    content_reuse = value.get("content_reuse", "metadata-only")
    if content_reuse not in {"candidate-copy", "metadata-only"}:
        raise SourceValidationError("constraints.content_reuse is invalid")
    runtime_dependency = value.get("runtime_dependency", "forbidden")
    if runtime_dependency != "forbidden":
        raise SourceValidationError("Source runtime dependency must remain forbidden")
    execution_authority = value.get("execution_authority", False)
    persistence_authority = value.get("persistence_authority", False)
    if execution_authority is not False or persistence_authority is not False:
        raise SourceValidationError("Source cannot carry execution or persistence authority")
    notes = value.get("notes", [])
    if not isinstance(notes, list) or any(
        not isinstance(item, str) or not item.strip() for item in notes
    ):
        raise SourceValidationError("constraints.notes must contain text")
    return {
        "content_reuse": content_reuse,
        "runtime_dependency": "forbidden",
        "execution_authority": False,
        "persistence_authority": False,
        "notes": sorted(set(item.strip() for item in notes)),
    }


def _normalise_effect_item(value: Any, *, field: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, Mapping):
        raise SourceValidationError(f"{field} items must be structured records")
    allowed = {
        "name",
        "kind",
        "description",
        "target",
        "effect",
        "path",
        "version",
        "optional",
        "confidence",
        "command",
        "arguments",
        "operations",
        "provider",
        "purpose",
        "requires",
        "permissions",
        "entrypoint",
        "package",
        "range",
    }
    unknown = set(value).difference(allowed)
    if unknown:
        raise SourceValidationError(f"Unknown {field} fields: {sorted(unknown)}")
    name = _normalise_text(value.get("name", value.get("effect")), field=f"{field}.name")
    assert name is not None
    item: dict[str, Any] = {"name": name}
    for key in sorted(set(value).difference({"name", "effect"})):
        child = value[key]
        if key == "path":
            item[key] = _portable_relative_path(child, field=f"{field}.path", allow_none=False)
        elif key == "optional":
            if not isinstance(child, bool):
                raise SourceValidationError(f"{field}.optional must be boolean")
            item[key] = child
        elif key in {"arguments", "operations", "requires", "permissions"}:
            if not isinstance(child, list) or any(
                not isinstance(item, str) or not item.strip() for item in child
            ):
                raise SourceValidationError(f"{field}.{key} must contain text")
            item[key] = sorted(set(item.strip() for item in child))
        elif key in {
            "kind",
            "description",
            "target",
            "version",
            "confidence",
            "command",
            "provider",
            "purpose",
            "entrypoint",
            "package",
            "range",
        }:
            item[key] = _normalise_text(child, field=f"{field}.{key}", allow_none=True)
        else:
            item[key] = child
    return item


def _normalise_declarations(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise SourceValidationError("declarations must be a record")
    unknown = set(value).difference(_DECLARATION_KEYS)
    if unknown:
        raise SourceValidationError(f"Unknown declaration fields: {sorted(unknown)}")
    values: dict[str, Any] = {}
    aliases = {
        "network_effects": ("network_effects", "network"),
        "write_effects": ("write_effects", "writes"),
    }
    for key in (
        "tools",
        "scripts",
        "network_effects",
        "write_effects",
        "dependencies",
        "provider_hints",
    ):
        raw = _merge_alias(value, aliases.get(key, (key,)), field=f"declarations.{key}")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise SourceValidationError(f"declarations.{key} must be a list")
        values[key] = [_normalise_effect_item(item, field=f"declarations.{key}") for item in raw]
    compatibility = value.get("compatibility", {})
    if not isinstance(compatibility, Mapping):
        raise SourceValidationError("declarations.compatibility must be a record")
    values["compatibility"] = _normalise_compatibility(compatibility)
    return values


def _normalise_compatibility(value: Mapping[str, Any]) -> dict[str, Any]:
    # Compatibility is deliberately descriptive rather than an activation
    # switch.  Keep only portable scalar/list data and sort list values.
    result: dict[str, Any] = {}
    for key in sorted(value):
        if not isinstance(key, str) or not key.strip():
            raise SourceValidationError("compatibility keys must be text")
        child = value[key]
        if isinstance(child, list):
            if any(not isinstance(item, (str, int, float, bool)) for item in child):
                raise SourceValidationError("compatibility lists must be scalar")
            result[key] = sorted(set(child), key=lambda item: str(item))
        elif isinstance(child, (str, int, float, bool)) or child is None:
            result[key] = child
        else:
            raise SourceValidationError("compatibility values must be portable scalars")
    return result


def _upstream_from_source(value: Mapping[str, Any]) -> dict[str, Any]:
    nested = value.get("upstream")
    if nested is None:
        nested = {}
    if not isinstance(nested, Mapping):
        raise SourceValidationError("upstream must be a record")
    unknown = set(nested).difference(_UPSTREAM_ALIASES)
    if unknown:
        raise SourceValidationError(f"Unknown upstream fields: {sorted(unknown)}")
    merged: dict[str, Any] = dict(nested)
    for key in _UPSTREAM_ALIASES:
        if key in value:
            if key in merged and merged[key] != value[key]:
                raise SourceValidationError(f"Conflicting upstream field: {key}")
            merged[key] = value[key]
    locator = _merge_alias(merged, ("locator", "upstream_locator", "source_url"), field="locator")
    commit = _merge_alias(merged, ("commit", "exact_commit"), field="commit")
    tag = _merge_alias(merged, ("tag", "exact_tag"), field="tag")
    version = _merge_alias(merged, ("version", "exact_version"), field="version")
    path = _merge_alias(merged, ("path", "source_path"), field="path")
    content = _merge_alias(merged, ("content_sha256", "content_hash"), field="content_sha256")
    tree = _merge_alias(merged, ("tree_sha256", "tree_hash"), field="tree_sha256")
    file_hash = _merge_alias(merged, ("file_sha256", "file_hash"), field="file_sha256")
    hash_record = merged.get("hash")
    if hash_record is not None:
        if not isinstance(hash_record, Mapping):
            raise SourceValidationError("upstream.hash must be a record")
        if set(hash_record).difference({"kind", "sha256"}):
            raise SourceValidationError("upstream.hash has unknown fields")
        hash_kind = _normalise_text(hash_record.get("kind"), field="upstream.hash.kind")
        hash_value = hash_record.get("sha256")
        if hash_kind not in {"content", "tree", "file"}:
            raise SourceValidationError("upstream.hash.kind must be content, tree, or file")
        hash_key = {
            "content": "content_sha256",
            "tree": "tree_sha256",
            "file": "file_sha256",
        }[hash_kind]
        current_hash = {"content_sha256": content, "tree_sha256": tree, "file_sha256": file_hash}[
            hash_key
        ]
        if current_hash is not None and current_hash != hash_value:
            raise SourceValidationError("Conflicting upstream hash values")
        if hash_key == "content_sha256":
            content = hash_value
        elif hash_key == "tree_sha256":
            tree = hash_value
        else:
            file_hash = hash_value
    locator = _normalise_locator(locator)
    commit = _normalise_commit(commit)
    tag = _normalise_text(tag, field="upstream.tag", allow_none=True)
    version = _normalise_text(version, field="upstream.version", allow_none=True)
    path = _portable_relative_path(path, field="upstream.path")
    content = _normalise_hash(content, field="upstream.content_sha256")
    tree = _normalise_hash(tree, field="upstream.tree_sha256")
    file_hash = _normalise_hash(file_hash, field="upstream.file_sha256")
    if commit is None and tag is None and version is None:
        raise SourceValidationError("upstream requires an exact commit, tag, or version")
    if commit is None and urlsplit(locator).scheme.lower() in {"git", "ssh"}:
        raise SourceValidationError("git upstream requires an exact commit")
    if tree is not None and file_hash is not None:
        raise SourceValidationError("upstream may carry a tree hash or file hash, not both")
    if content is None and tree is None and file_hash is None:
        raise SourceValidationError("upstream requires a content, tree, or file hash")
    return {
        "locator": locator,
        "commit": commit,
        "tag": tag,
        "version": version,
        "path": path,
        "content_sha256": content,
        "tree_sha256": tree,
        "file_sha256": file_hash,
    }


def _identity_from_source(value: Mapping[str, Any]) -> tuple[str, str, str, str | None]:
    identity = value.get("identity")
    if identity is None:
        identity = {}
    if not isinstance(identity, Mapping):
        raise SourceValidationError("identity must be a record")
    unknown = set(identity).difference({"name", "source_type", "type", "donor", "donor_id"})
    if unknown:
        raise SourceValidationError(f"Unknown identity fields: {sorted(unknown)}")
    name = _merge_alias(
        {**identity, **{key: value[key] for key in ("name",) if key in value}},
        ("name",),
        field="name",
    )
    source_type = _merge_alias(
        {**identity, **{key: value[key] for key in ("source_type", "type") if key in value}},
        ("source_type", "type"),
        field="source_type",
    )
    donor_value = value.get("donor", identity.get("donor"))
    donor_id = value.get("donor_id", identity.get("donor_id"))
    donor_name: str | None = None
    if isinstance(donor_value, Mapping):
        unknown_donor = set(donor_value).difference({"donor_id", "id", "name"})
        if unknown_donor:
            raise SourceValidationError(f"Unknown donor fields: {sorted(unknown_donor)}")
        nested_id = donor_value.get("donor_id", donor_value.get("id"))
        if donor_id is not None and nested_id is not None and donor_id != nested_id:
            raise SourceValidationError("Conflicting donor ids")
        donor_id = nested_id if nested_id is not None else donor_id
        donor_name = _normalise_text(donor_value.get("name"), field="donor.name", allow_none=True)
    elif donor_value is not None:
        if donor_id is not None and donor_id != donor_value:
            raise SourceValidationError("Conflicting donor ids")
        donor_id = donor_value
    name = _normalise_text(name, field="name")
    source_type = _normalise_source_type(source_type)
    donor_id = _normalise_id(donor_id, field="donor_id")
    return name or "", source_type, donor_id, donor_name


def _source_identity_payload(source: Mapping[str, Any]) -> dict[str, Any]:
    upstream = source["upstream"]
    # Path is intentionally excluded.  A file path in a repository is
    # provenance, not a Dot boundary; one Source may claim several behaviours.
    return {
        "source_type": source["source_type"],
        "donor_id": source["donor"]["donor_id"],
        "locator": upstream["locator"],
        "commit": upstream["commit"],
        "tag": upstream["tag"],
        "version": upstream["version"],
        "content_sha256": upstream["content_sha256"],
        "tree_sha256": upstream["tree_sha256"],
        "file_sha256": upstream["file_sha256"],
    }


def deterministic_source_id(source: Mapping[str, Any] | None = None, **identity: Any) -> str:
    """Return a stable Source id from upstream identity, never from claims or time."""
    if source is None:
        source = {
            "source_type": identity.get("source_type", identity.get("type")),
            "donor": {"donor_id": identity.get("donor_id", identity.get("donor"))},
            "upstream": {
                "locator": identity.get("locator", identity.get("upstream_locator")),
                "commit": identity.get("commit", identity.get("exact_commit")),
                "tag": identity.get("tag", identity.get("exact_tag")),
                "version": identity.get("version", identity.get("exact_version")),
                "content_sha256": identity.get("content_sha256", identity.get("content_hash")),
                "tree_sha256": identity.get("tree_sha256", identity.get("tree_hash")),
                "file_sha256": identity.get("file_sha256", identity.get("file_hash")),
            },
        }
    payload = _source_identity_payload(source)
    return f"source-{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:32]}"


source_id = deterministic_source_id
source_id_for = deterministic_source_id


def _provenance_payload(provenance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: provenance.get(key)
        for key in (
            "locator",
            "commit",
            "tag",
            "version",
            "path",
            "content_sha256",
            "tree_sha256",
            "file_sha256",
        )
    }


def deterministic_provenance_id(provenance: Mapping[str, Any]) -> str:
    """Return an observation id that ignores retrieval time and notes."""
    digest = hashlib.sha256(canonical_json_bytes(_provenance_payload(provenance))).hexdigest()
    return f"provenance-{digest[:32]}"


provenance_id = deterministic_provenance_id
provenance_id_for = deterministic_provenance_id


def _normalise_provenance(
    value: Any, *, upstream: Mapping[str, Any], retrieved_at: str
) -> list[dict[str, Any]]:
    if value is None:
        value = [{}]
    elif isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list) or not value:
        raise SourceValidationError("provenance must contain at least one observation")
    records: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise SourceValidationError("provenance entries must be records")
        unknown = set(raw).difference(_PROVENANCE_KEYS)
        if unknown:
            raise SourceValidationError(f"Unknown provenance fields: {sorted(unknown)}")
        merged = dict(upstream)
        merged.update(raw)
        locator = _normalise_locator(
            _merge_alias(
                merged, ("locator", "upstream_locator", "source_url"), field="provenance.locator"
            ),
            field="provenance.locator",
        )
        commit = _normalise_commit(
            _merge_alias(merged, ("commit", "exact_commit"), field="provenance.commit"),
            field="provenance.commit",
        )
        tag = _normalise_text(
            _merge_alias(merged, ("tag", "exact_tag"), field="provenance.tag"),
            field="provenance.tag",
            allow_none=True,
        )
        version = _normalise_text(
            _merge_alias(merged, ("version", "exact_version"), field="provenance.version"),
            field="provenance.version",
            allow_none=True,
        )
        path = _portable_relative_path(
            _merge_alias(merged, ("path", "source_path"), field="provenance.path"),
            field="provenance.path",
        )
        content = _normalise_hash(
            _merge_alias(
                merged, ("content_sha256", "content_hash"), field="provenance.content_sha256"
            ),
            field="provenance.content_sha256",
        )
        tree = _normalise_hash(
            _merge_alias(merged, ("tree_sha256", "tree_hash"), field="provenance.tree_sha256"),
            field="provenance.tree_sha256",
        )
        file_hash = _normalise_hash(
            _merge_alias(merged, ("file_sha256", "file_hash"), field="provenance.file_sha256"),
            field="provenance.file_sha256",
        )
        hash_record = merged.get("hash")
        if hash_record is not None:
            if not isinstance(hash_record, Mapping):
                raise SourceValidationError("provenance.hash must be a record")
            if set(hash_record).difference({"kind", "sha256"}):
                raise SourceValidationError("provenance.hash has unknown fields")
            hash_kind = _normalise_text(hash_record.get("kind"), field="provenance.hash.kind")
            hash_value = _normalise_hash(
                hash_record.get("sha256"), field="provenance.hash.sha256", allow_none=False
            )
            if hash_kind not in {"content", "tree", "file"}:
                raise SourceValidationError("provenance.hash.kind must be content, tree, or file")
            if hash_kind == "content":
                if content is not None and content != hash_value:
                    raise SourceValidationError("Conflicting provenance hash values")
                content = hash_value
            elif hash_kind == "tree":
                if tree is not None and tree != hash_value:
                    raise SourceValidationError("Conflicting provenance hash values")
                tree = hash_value
            else:
                if file_hash is not None and file_hash != hash_value:
                    raise SourceValidationError("Conflicting provenance hash values")
                file_hash = hash_value
        observed_at = _normalise_datetime(
            raw.get("observed_at", raw.get("retrieved_at", retrieved_at)),
            field="provenance.observed_at",
        )
        evidence = raw.get("evidence", raw.get("evidence_ids", []))
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise SourceValidationError("provenance evidence must contain text ids")
        notes = raw.get("notes", [])
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list) or any(
            not isinstance(item, str) or not item.strip() for item in notes
        ):
            raise SourceValidationError("provenance notes must contain text")
        record = {
            "provenance_id": "",
            "observed_at": observed_at,
            "locator": locator,
            "commit": commit,
            "tag": tag,
            "version": version,
            "path": path,
            "content_sha256": content,
            "tree_sha256": tree,
            "file_sha256": file_hash,
            "evidence": sorted(set(item.strip() for item in evidence)),
            "notes": sorted(set(item.strip() for item in notes)),
        }
        expected_id = deterministic_provenance_id(record)
        supplied_id = raw.get("provenance_id")
        if supplied_id is not None and supplied_id != expected_id:
            raise SourceValidationError("provenance_id is not deterministic")
        record["provenance_id"] = expected_id
        records.append(record)
    records.sort(key=lambda item: item["provenance_id"])
    if len({item["provenance_id"] for item in records}) != len(records):
        records = list(_merge_provenance_records(records))
    return records


def _normalise_source(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceValidationError("Source must be a record")
    unknown = set(value).difference(_TOP_LEVEL_ALIASES)
    if unknown:
        raise SourceValidationError(f"Unknown Source fields: {sorted(unknown)}")
    record_type = value.get("record_type", SOURCE_RECORD_TYPE)
    if record_type != SOURCE_RECORD_TYPE:
        raise SourceValidationError("Record is not a capability Source")
    record_version = value.get("record_version", SOURCE_RECORD_VERSION)
    if record_version != SOURCE_RECORD_VERSION:
        raise SourceValidationError("Unsupported capability Source version")
    name, source_type, donor_id, donor_name = _identity_from_source(value)
    upstream = _upstream_from_source(value)
    retrieved_at = _normalise_datetime(value.get("retrieved_at", _now()), field="retrieved_at")
    licence = _normalise_licence(value.get("licence", value.get("license")))
    constraints = _normalise_constraint(value.get("constraints"))
    if licence["candidate_content_allowed"] is False:
        if constraints["content_reuse"] == "candidate-copy":
            raise SourceLicenceError("Candidate copy requires a verified licence")
        constraints["content_reuse"] = "metadata-only"
    elif constraints["content_reuse"] == "candidate-copy" and licence["status"] != "verified":
        raise SourceLicenceError("Candidate copy requires a verified licence")
    claims = value.get("claimed_capabilities", [])
    if not isinstance(claims, list) or any(
        not isinstance(item, str) or not item.strip() for item in claims
    ):
        raise SourceValidationError("claimed_capabilities must contain names only")
    declarations_input: dict[str, Any] = {}
    nested_declarations = value.get("declarations", value.get("declared"))
    if nested_declarations is not None:
        if not isinstance(nested_declarations, Mapping):
            raise SourceValidationError("declarations must be a record")
        declarations_input.update(nested_declarations)
    for key in _DECLARATION_KEYS:
        if key in value:
            if key in declarations_input and declarations_input[key] != value[key]:
                raise SourceValidationError(f"Conflicting declaration field: {key}")
            declarations_input[key] = value[key]
    top_level_declaration_aliases = {
        "declared_tools": "tools",
        "declared_scripts": "scripts",
        "declared_network_effects": "network_effects",
        "declared_write_effects": "write_effects",
        "declared_dependencies": "dependencies",
        "declared_provider_hints": "provider_hints",
        "declared_compatibility": "compatibility",
    }
    for alias, key in top_level_declaration_aliases.items():
        if alias in value:
            if key in declarations_input and declarations_input[key] != value[alias]:
                raise SourceValidationError(f"Conflicting declaration field: {key}")
            declarations_input[key] = value[alias]
    declarations = _normalise_declarations(declarations_input)
    status = value.get("status", "source-only")
    if status != "source-only":
        raise SourceValidationError("A raw Source status must be exactly source-only")
    source_only = value.get("source_only", {})
    if not isinstance(source_only, Mapping):
        raise SourceValidationError("source_only must be a record")
    allowed_source_only = {
        "callable",
        "resolvable",
        "active",
        "execution_authority",
        "persistence_authority",
    }
    unknown_source_only = set(source_only).difference(allowed_source_only)
    if unknown_source_only:
        raise SourceValidationError(f"Unknown source_only fields: {sorted(unknown_source_only)}")
    source_only_record = {
        "callable": source_only.get("callable", False),
        "resolvable": source_only.get("resolvable", False),
        "active": source_only.get("active", False),
        "execution_authority": source_only.get("execution_authority", False),
        "persistence_authority": source_only.get("persistence_authority", False),
    }
    if any(value is not False for value in source_only_record.values()):
        raise SourceValidationError(
            "Raw Source cannot be callable, resolvable, active, or authoritative"
        )
    provisional = {
        "$schema": value.get("$schema", SCHEMA_URI),
        "record_type": SOURCE_RECORD_TYPE,
        "record_version": SOURCE_RECORD_VERSION,
        "source_id": "",
        "name": name,
        "source_type": source_type,
        "donor": {"donor_id": donor_id, "name": donor_name},
        "upstream": upstream,
        "licence": licence,
        "constraints": constraints,
        "retrieved_at": retrieved_at,
        "claimed_capabilities": sorted(set(item.strip() for item in claims)),
        "declarations": declarations,
        "status": "source-only",
        "source_only": source_only_record,
    }
    expected_source_id = deterministic_source_id(provisional)
    supplied_source_id = value.get("source_id")
    if supplied_source_id is not None and supplied_source_id != expected_source_id:
        raise SourceValidationError("source_id is not deterministic")
    provisional["source_id"] = expected_source_id
    provisional["provenance"] = _normalise_provenance(
        value.get("provenance"), upstream=upstream, retrieved_at=retrieved_at
    )
    _validate_portability(provisional)
    _schema_validator().validate(provisional)
    return provisional


def _validate_portability(value: Any, *, path: str = "Source", key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise SourceValidationError("Durable Source keys must be text")
            if _SECRET_KEY_RE.search(child_key):
                raise SourceValidationError(
                    f"Durable Source cannot contain secret field: {path}.{child_key}"
                )
            _validate_portability(child, path=f"{path}.{child_key}", key=child_key)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_portability(child, path=f"{path}[{index}]", key=key)
        return
    if not isinstance(value, str):
        return
    if key in {"locator", "source_url", "upstream_locator"}:
        # URI credentials and file:// are checked by _normalise_locator.  A
        # network URI is not a machine-local path and is safe provenance.
        return
    if _ABSOLUTE_PATH_RE.search(value) or value.startswith(("file://", "unix://")):
        raise SourceValidationError(f"Durable Source contains a machine-specific path: {path}")
    if _SECRET_KEY_RE.search(value) and (len(value) > 20 or "=" in value):
        raise SourceValidationError(f"Durable Source contains credential-like text: {path}")


def validate_source(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return one canonical raw Source record."""
    return _normalise_source(value)


validate_capability_source = validate_source


def source_only(value: Mapping[str, Any]) -> bool:
    """Return whether a record is a valid raw Source with no authority."""
    try:
        validate_source(value)
    except (SourceValidationError, TypeError):
        return False
    return True


is_source_only = source_only


def can_reuse_source_content(value: Mapping[str, Any]) -> bool:
    """Return the licence-gated candidate-copy decision for a Source."""
    source = validate_source(value)
    return bool(
        source["licence"]["status"] == "verified"
        and source["licence"]["candidate_content_allowed"]
        and source["constraints"]["content_reuse"] == "candidate-copy"
    )


source_content_reuse_allowed = can_reuse_source_content
candidate_content_allowed = can_reuse_source_content
licence_allows_candidate_copy = can_reuse_source_content


def require_candidate_content_reuse(value: Mapping[str, Any]) -> None:
    """Raise unless a Source has a verified licence and explicit copy gate."""
    if not can_reuse_source_content(value):
        raise SourceLicenceError(
            "Source permits research metadata only; candidate content reuse is blocked"
        )


def build_source(
    *,
    name: str,
    source_type: str,
    donor_id: str,
    locator: str,
    commit: str | None = None,
    tag: str | None = None,
    version: str | None = None,
    path: str | None = None,
    content_sha256: str | None = None,
    tree_sha256: str | None = None,
    file_sha256: str | None = None,
    licence: Mapping[str, Any] | str | None = None,
    constraints: Mapping[str, Any] | Sequence[str] | None = None,
    retrieved_at: str | None = None,
    claimed_capabilities: Iterable[str] = (),
    declarations: Mapping[str, Any] | None = None,
    donor_name: str | None = None,
) -> dict[str, Any]:
    """Construct and validate a Source without reading or retrieving anything."""
    record: dict[str, Any] = {
        "$schema": SCHEMA_URI,
        "record_type": SOURCE_RECORD_TYPE,
        "record_version": SOURCE_RECORD_VERSION,
        "name": name,
        "source_type": source_type,
        "donor": {"donor_id": donor_id, "name": donor_name},
        "upstream": {
            "locator": locator,
            "commit": commit,
            "tag": tag,
            "version": version,
            "path": path,
            "content_sha256": content_sha256,
            "tree_sha256": tree_sha256,
            "file_sha256": file_sha256,
        },
        "licence": licence,
        "constraints": constraints,
        "retrieved_at": retrieved_at or _now(),
        "claimed_capabilities": list(claimed_capabilities),
        "declarations": declarations or {},
        "status": "source-only",
        "source_only": {
            "callable": False,
            "resolvable": False,
            "active": False,
            "execution_authority": False,
            "persistence_authority": False,
        },
    }
    return validate_source(record)


make_source = build_source
create_source = build_source


def intake_source(
    value: Mapping[str, Any],
    *,
    operation: str | None = None,
    mode: str | None = None,
    catalogue: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate explicit genesis/evolution metadata and optionally merge it.

    No default runtime path calls this helper.  The operation marker is
    intentionally mandatory so normal runtime code cannot turn a locator into
    an implicit retrieval operation.
    """
    selected_operation = operation if operation is not None else mode
    if selected_operation not in {"genesis", "evolution"}:
        raise SourceValidationError(
            "Source intake requires an explicit genesis or evolution operation"
        )
    if operation is not None and mode is not None and operation != mode:
        raise SourceValidationError("Conflicting Source intake operation markers")
    source = validate_source(value)
    if catalogue is None:
        return source
    return merge_source_catalogue(catalogue, source)


ingest_source = intake_source
intake_capability_source = intake_source


def _canonical_item_key(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _merge_unique_records(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records = {(_canonical_item_key(item)): copy.deepcopy(item) for item in left}
    records.update({(_canonical_item_key(item)): copy.deepcopy(item) for item in right})
    return [records[key] for key in sorted(records)]


def _merge_compatibility(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(set(left) | set(right)):
        if key not in left:
            result[key] = copy.deepcopy(right[key])
        elif key not in right or left[key] == right[key]:
            result[key] = copy.deepcopy(left[key])
        elif isinstance(left[key], list) and isinstance(right[key], list):
            result[key] = sorted(set(left[key]) | set(right[key]), key=lambda item: str(item))
        else:
            # Conflicting descriptive hints remain deterministic and do not
            # become an activation decision: lexical minimum is the stable
            # retained value, while provenance keeps both source observations.
            result[key] = min((left[key], right[key]), key=lambda item: str(item))
    return result


def _merge_provenance_records(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in records:
        current = copy.deepcopy(dict(raw))
        identifier = current["provenance_id"]
        if identifier not in by_id:
            by_id[identifier] = current
            continue
        existing = by_id[identifier]
        existing["observed_at"] = min(existing["observed_at"], current["observed_at"])
        existing["evidence"] = sorted(
            set(existing.get("evidence", [])) | set(current.get("evidence", []))
        )
        existing["notes"] = sorted(set(existing.get("notes", [])) | set(current.get("notes", [])))
    return tuple(by_id[key] for key in sorted(by_id))


def merge_source_records(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Merge duplicate observations into one Source without creating a second id."""
    first = validate_source(left)
    second = validate_source(right)
    if deterministic_source_id(first) != deterministic_source_id(second):
        raise SourceValidationError("Cannot merge different Source identities")
    merged = copy.deepcopy(first)
    merged["source_id"] = deterministic_source_id(first)
    merged["retrieved_at"] = min(first["retrieved_at"], second["retrieved_at"])
    merged["claimed_capabilities"] = sorted(
        set(first["claimed_capabilities"]) | set(second["claimed_capabilities"])
    )
    merged["provenance"] = list(
        _merge_provenance_records(first["provenance"] + second["provenance"])
    )
    # A path is an observation detail, not an identity boundary.  Keep one
    # deterministic primary path while preserving every path in provenance.
    paths = [
        path for path in (first["upstream"].get("path"), second["upstream"].get("path")) if path
    ]
    if paths:
        merged["upstream"]["path"] = min(paths)
    first_licence = first["licence"]
    second_licence = second["licence"]
    status = max(
        (first_licence["status"], second_licence["status"]),
        key=lambda item: LICENCE_RESTRICTION_ORDER[item],
    )
    spdx_values = {item["spdx"] for item in (first_licence, second_licence) if item["spdx"]}
    evidence_values = {
        item["evidence"] for item in (first_licence, second_licence) if item["evidence"]
    }
    if status == "verified" and len(spdx_values) > 1:
        # Conflicting licence observations are research-only until a later
        # explicit review resolves them; never silently choose one for copy.
        status = "unclear"
    merged["licence"] = {
        "status": status,
        "spdx": next(iter(spdx_values)) if len(spdx_values) == 1 else None,
        "evidence": min(evidence_values) if evidence_values else None,
        "constraints": sorted(
            set(first_licence["constraints"]) | set(second_licence["constraints"])
        ),
        "candidate_content_allowed": status == "verified"
        and first_licence["candidate_content_allowed"]
        and second_licence["candidate_content_allowed"],
    }
    merged["constraints"]["content_reuse"] = (
        "candidate-copy"
        if merged["licence"]["candidate_content_allowed"]
        and first["constraints"]["content_reuse"] == "candidate-copy"
        and second["constraints"]["content_reuse"] == "candidate-copy"
        else "metadata-only"
    )
    for key in (
        "tools",
        "scripts",
        "network_effects",
        "write_effects",
        "dependencies",
        "provider_hints",
    ):
        merged["declarations"][key] = _merge_unique_records(
            first["declarations"][key], second["declarations"][key]
        )
    merged["declarations"]["compatibility"] = _merge_compatibility(
        first["declarations"]["compatibility"], second["declarations"]["compatibility"]
    )
    return validate_source(merged)


merge_source_provenance = merge_source_records
merge_duplicate_sources = merge_source_records
merge_duplicate_provenance = merge_source_records


def empty_source_catalogue() -> dict[str, Any]:
    """Return a validated empty catalogue for a Workplace-owned destination."""
    return {
        "$schema": SCHEMA_URI,
        "record_type": CATALOGUE_RECORD_TYPE,
        "record_version": CATALOGUE_RECORD_VERSION,
        "sources": [],
    }


def validate_source_catalogue(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a catalogue and enforce deterministic unique Source ordering."""
    if not isinstance(value, Mapping):
        raise SourceValidationError("Source catalogue must be a record")
    if value.get("record_type") != CATALOGUE_RECORD_TYPE:
        raise SourceValidationError("Source catalogue record type is invalid")
    if value.get("record_version") != CATALOGUE_RECORD_VERSION:
        raise SourceValidationError("Source catalogue version is invalid")
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise SourceValidationError("Source catalogue sources are missing")
    canonical = [validate_source(item) for item in sources]
    identifiers = [item["source_id"] for item in canonical]
    if len(identifiers) != len(set(identifiers)):
        raise SourceValidationError("Source ids must be unique")
    if identifiers != sorted(identifiers):
        raise SourceValidationError("Source ids must be sorted deterministically")
    result = {
        "$schema": value.get("$schema", SCHEMA_URI),
        "record_type": CATALOGUE_RECORD_TYPE,
        "record_version": CATALOGUE_RECORD_VERSION,
        "sources": canonical,
    }
    _validate_portability(result)
    return result


validate_catalogue = validate_source_catalogue
validate_capability_source_catalogue = validate_source_catalogue


def merge_source_catalogue(
    catalogue: Mapping[str, Any], source: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge one Source by deterministic id and return a sorted catalogue."""
    current = validate_source_catalogue(catalogue)
    incoming = validate_source(source)
    by_id = {item["source_id"]: item for item in current["sources"]}
    existing = by_id.get(incoming["source_id"])
    by_id[incoming["source_id"]] = (
        merge_source_records(existing, incoming) if existing is not None else incoming
    )
    return validate_source_catalogue({**current, "sources": [by_id[key] for key in sorted(by_id)]})


merge_into_catalogue = merge_source_catalogue


def _confined_path(path: str | os.PathLike[str] | Path, *, root: Path | None) -> Path:
    candidate = Path(path).expanduser()
    if root is None:
        candidate = candidate.absolute()
        return candidate
    root = Path(root).expanduser().resolve(strict=False)
    raw_candidate = candidate if candidate.is_absolute() else root / candidate
    if raw_candidate.is_symlink():
        raise SourceStorageError(f"Catalogue path cannot be a symlink: {raw_candidate}")
    candidate = raw_candidate.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise SourceStorageError(
            f"Catalogue path escapes its supplied root: {candidate}"
        ) from error
    if candidate.is_symlink():
        raise SourceStorageError(f"Catalogue path cannot be a symlink: {candidate}")
    return candidate


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_source_catalogue(
    path: str | os.PathLike[str] | Path,
    catalogue: Mapping[str, Any],
    *,
    root: str | os.PathLike[str] | Path | None = None,
) -> dict[str, Any]:
    """Atomically write, read back, and validate a Workplace-owned catalogue."""
    canonical = validate_source_catalogue(catalogue)
    destination = _confined_path(path, root=Path(root) if root is not None else None)
    encoded = (
        json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    try:
        _atomic_write(destination, encoded)
        read_back = load_source_catalogue(destination, root=root)
    except (OSError, UnicodeError, json.JSONDecodeError, SourceValidationError) as error:
        raise SourceStorageError(
            f"Source catalogue write/read-back failed: {destination}"
        ) from error
    if canonical_json_bytes(read_back) != canonical_json_bytes(canonical):
        raise SourceStorageError("Source catalogue read-back does not match the validated value")
    return read_back


def load_source_catalogue(
    path: str | os.PathLike[str] | Path,
    *,
    root: str | os.PathLike[str] | Path | None = None,
) -> dict[str, Any]:
    """Load and validate a catalogue without any retrieval or execution."""
    source_path = _confined_path(path, root=Path(root) if root is not None else None)
    try:
        value = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SourceStorageError(f"Cannot read Source catalogue: {source_path}") from error
    try:
        return validate_source_catalogue(value)
    except (SourceValidationError, TypeError) as error:
        raise SourceStorageError(f"Invalid Source catalogue: {source_path}") from error


load_catalogue = load_source_catalogue
write_catalogue = write_source_catalogue
load_capability_source_catalogue = load_source_catalogue
write_capability_source_catalogue = write_source_catalogue


def sha256_file(
    path: str | os.PathLike[str] | Path, *, root: str | os.PathLike[str] | Path | None = None
) -> str:
    """Hash an explicitly supplied file; this helper never retrieves it."""
    target = _confined_path(path, root=Path(root) if root is not None else None)
    if target.is_symlink() or not target.is_file():
        raise SourceValidationError("Source hash input must be a regular file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def sha256_tree(
    path: str | os.PathLike[str] | Path, *, root: str | os.PathLike[str] | Path | None = None
) -> str:
    """Hash an explicitly supplied tree using stable relative paths and bytes."""
    target = _confined_path(path, root=Path(root) if root is not None else None)
    if target.is_symlink() or not target.is_dir():
        raise SourceValidationError("Source tree hash input must be a directory")
    digest = hashlib.sha256()
    for child in sorted(target.rglob("*"), key=lambda item: item.relative_to(target).as_posix()):
        if child.is_symlink():
            raise SourceValidationError("Source tree cannot contain symlinks")
        if not child.is_file():
            continue
        digest.update(child.relative_to(target).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


file_sha256 = sha256_file
tree_sha256 = sha256_tree
