"""Pure, deterministic responsibility extraction from raw capability evidence.

The return value is an evidence subobject for a Workplace writer.  This
module never retrieves or invokes a Source, creates a Dot, writes a registry,
or treats document text/model suggestions as instruction authority.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fractal.capability_source import (
    SourceValidationError,
    can_reuse_source_content,
    validate_source,
)
from fractal.storage import canonical_json_bytes

EXTRACTION_RECORD_TYPE = "responsibility-extraction"
EXTRACTION_RECORD_VERSION = 1
EXTRACTION_METHOD = "deterministic-structured-responsibility"
EXTRACTION_METHOD_VERSION = "1.0.0"
RESPONSIBILITY_EXTRACTION_RECORD_TYPE = EXTRACTION_RECORD_TYPE
RESPONSIBILITY_EXTRACTION_VERSION = EXTRACTION_RECORD_VERSION

_HASH = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~=,#-]{0,255}$")
_HOME_MARKER = "/" + "Users" + "/"
_ABSOLUTE = re.compile(
    r"(?:^|[\s'\"=:(\[])(?:"
    + re.escape(_HOME_MARKER)
    + r"|/home/|/private/tmp/|/tmp/|/var/|/run/|[A-Za-z]:[\\/])"
)
_SECRET_KEY = re.compile(
    r"(?:^|[_-])(token|password|secret|api[_-]?key|credential|authorization)(?:$|[_-])", re.I
)
_SECRET_VALUE = re.compile(
    r"(?:bearer\s+\S{12,}|(?:token|password|secret|api[_-]?key)\s*[=:]\s*\S{8,})", re.I
)
_LEGACY = frozenset(
    {
        "action",
        "action_id",
        "actions",
        "category",
        "categories",
        "dot_group",
        "dot_group_id",
        "dot_groups",
        "workflow",
        "workflow_id",
        "workflows",
        "method_ref",
        "method_id",
        "instruction",
        "instructions",
        "tool_call",
        "tool_calls",
        "execute",
        "execution",
    }
)
_RAW_KEYS = frozenset(
    {
        "raw",
        "raw_document",
        "document_text",
        "full_text",
        "source_text",
        "body",
        "content",
        "prompt",
    }
)
_VERBS = frozenset(
    {
        "analyse",
        "analyze",
        "alert",
        "apply",
        "assess",
        "architect",
        "author",
        "audit",
        "build",
        "classify",
        "collect",
        "compare",
        "compose",
        "configure",
        "connect",
        "convert",
        "create",
        "coordinate",
        "decide",
        "delegate",
        "deploy",
        "detect",
        "develop",
        "debug",
        "dispatch",
        "drive",
        "evolve",
        "execute",
        "extract",
        "fetch",
        "filter",
        "find",
        "finish",
        "follow",
        "generate",
        "geocode",
        "ground",
        "group",
        "guide",
        "host",
        "index",
        "inspect",
        "install",
        "interpret",
        "implement",
        "join",
        "load",
        "list",
        "manage",
        "maintain",
        "map",
        "match",
        "master",
        "monitor",
        "normalise",
        "normalize",
        "operate",
        "optimize",
        "parse",
        "perform",
        "plan",
        "prepare",
        "publish",
        "query",
        "read",
        "receive",
        "rank",
        "recover",
        "render",
        "request",
        "resolve",
        "retrieve",
        "review",
        "search",
        "send",
        "shop",
        "summarise",
        "summarize",
        "transform",
        "translate",
        "track",
        "turn",
        "understand",
        "use",
        "validate",
        "verify",
        "write",
    }
)
_GENERIC = frozenset(
    {
        "ability",
        "automation",
        "capability",
        "feature",
        "integration",
        "library",
        "provider",
        "skill",
        "support",
        "tool",
        "utility",
    }
)
_PROVIDER_MARKERS = (
    ("microsoft entra", "microsoft-entra"),
    ("google cloud", "google-cloud"),
    ("google", "google"),
    ("azure", "azure"),
    ("github", "github"),
    ("gitlab", "gitlab"),
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("claude", "claude"),
    ("figma", "figma"),
    ("xcode", "xcode"),
    ("hermes", "hermes"),
    ("slack", "slack"),
    ("notion", "notion"),
    ("stripe", "stripe"),
    ("nvidia", "nvidia"),
    ("playwright", "playwright"),
    ("kubernetes", "kubernetes"),
    ("docker", "docker"),
    ("terraform", "terraform"),
    ("postgresql", "postgresql"),
    ("mysql", "mysql"),
    ("microsoft", "microsoft"),
    ("m365", "m365"),
    ("teams", "microsoft-teams"),
    ("aws", "aws"),
)
_ROUTING_DESCRIPTION = re.compile(
    r"^(?:when\s+to\s+use|(?:also\s+)?use(?:\s+this\s+skill)?\s+when|activate\s+when|"
    r"invoke\s+when|trigger(?:s|ed)?\s*:|trigger\s+on|best\s+for|use\s+cases?\s*:|"
    r"examples?\s*:|keywords?\s*:|covers?\b|supports?\s*:)",
    re.I,
)
_LANGUAGE_SUFFIX = re.compile(
    r"(?:\s+(?:for|in)\s+(?:python|java(?:script)?|typescript|\.net|c#|rust|go|php|ruby|cpp|c\+\+))"
    r"|(?:\s*\((?:python|java(?:script)?|typescript|\.net|c#|rust|go|php|ruby|cpp|c\+\+)\))",
    re.I,
)
_LANGUAGE_MODIFIER = re.compile(
    r"\b(?:python|java(?:script)?|typescript|\.net|c#|rust|golang|php|ruby|cpp|c\+\+)\b",
    re.I,
)
_SECTIONS = {
    "responsibility": "responsibilities",
    "responsibilities": "responsibilities",
    "capability": "responsibilities",
    "capabilities": "responsibilities",
    "inputs": "inputs",
    "outputs": "outputs",
    "preconditions": "preconditions",
    "prerequisites": "preconditions",
    "side effects": "side_effects",
    "side-effects": "side_effects",
    "effects": "side_effects",
    "knowledge": "knowledge",
    "procedure": "procedure_outline",
    "procedure outline": "procedure_outline",
    "steps": "procedure_outline",
    "instructions": "procedure_outline",
    "process": "procedure_outline",
    "usage": "procedure_outline",
    "how it works": "procedure_outline",
    "how to use": "procedure_outline",
    "verification": "verification",
    "verify": "verification",
    "failure": "failure_recovery",
    "failure recovery": "failure_recovery",
    "failure/recovery": "failure_recovery",
    "recovery": "failure_recovery",
    "troubleshooting": "failure_recovery",
    "errors": "failure_recovery",
    "error handling": "failure_recovery",
}
_ALIASES = {
    "inputs": ("inputs", "input", "parameters", "arguments", "requires"),
    "outputs": ("outputs", "output", "returns", "return"),
    "preconditions": ("preconditions", "precondition", "prerequisites", "conditions"),
    "side_effects": ("side_effects", "side_effect", "effects", "writes", "network_effects"),
    "knowledge": ("knowledge", "domain_knowledge", "context", "facts"),
    "procedure_outline": ("procedure_outline", "procedure", "steps", "step", "how_to"),
    "verification": ("verification", "verify", "validation", "tests"),
    "failure_recovery": ("failure_recovery", "failure", "recovery", "rollback", "error_handling"),
    "provider_dependency": (
        "provider_dependency",
        "provider",
        "provider_id",
        "vendor",
        "provider_specific",
    ),
    "source_refs": ("source_refs", "source_ref", "source_references"),
    "provenance_refs": ("provenance_refs", "provenance_ref", "provenance_references"),
    "confidence": ("confidence",),
    "uncertainties": ("uncertainties", "uncertainty", "open_questions"),
}


class ResponsibilityExtractionError(ValueError):
    """Base extraction-boundary error."""


class ExtractionValidationError(ResponsibilityExtractionError):
    """Malformed, non-portable, or authority-smuggling extraction evidence."""


class ExtractionLicenceError(ExtractionValidationError):
    """An extracted contribution does not match the Source licence gate."""


ResponsibilityExtractionValidationError = ExtractionValidationError
ResponsibilityEvidenceError = ExtractionValidationError


@dataclass(frozen=True)
class _Source:
    record: dict[str, Any]
    source_id: str
    provenance: tuple[str, ...]
    claims: tuple[str, ...]
    declarations: dict[str, Any]
    providers: tuple[str, ...]
    candidate_allowed: bool


@dataclass(frozen=True)
class _Document:
    frontmatter: dict[str, Any]
    text: str
    digest: str
    ref: str


@dataclass
class _Candidate:
    text: str
    fields: dict[str, Any]
    refs: list[str]
    origin: str


def _fail(message: str) -> None:
    raise ExtractionValidationError(message)


def _hash(value: Any) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError) as error:
        raise ExtractionValidationError("evidence must be JSON-serialisable") from error


def _clean(value: Any, label: str, *, limit: int = 420) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must contain text")
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(
        r"!\[([^]]*)\]\([^)]*\)|\[([^]]+)\]\([^)]*\)", lambda m: m.group(1) or m.group(2), text
    )
    text = re.sub(r"[`*>]+", "", text)
    text = re.sub(r"(^|\s)#+\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit or _SECRET_VALUE.search(text) or _ABSOLUTE.search(text):
        _fail(f"{label} is not compact portable evidence")
    return text


def _key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _reject_hints(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for name, child in value.items():
            if _key(name) in _LEGACY:
                _fail(
                    "legacy Action/category/dot_group/workflow hint is not an "
                    f"extraction input: {path}.{name}"
                )
            _reject_hints(child, f"{path}.{name}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_hints(child, f"{path}[{index}]")


def _privacy(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for name, child in value.items():
            name = str(name)
            if _SECRET_KEY.search(name):
                _fail(f"evidence contains secret field: {path}.{name}")
            if name.lower() in _RAW_KEYS:
                _fail(f"evidence cannot retain raw document field: {path}.{name}")
            _privacy(child, f"{path}.{name}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _privacy(child, f"{path}[{index}]")
    elif isinstance(value, str) and (
        _SECRET_VALUE.search(value)
        or (not value.startswith("http") and _ABSOLUTE.search(value))
        or len(value) > 900
    ):
        _fail(f"evidence contains non-portable text at {path}")


def _unique(items: Sequence[str], *, sort: bool = False) -> list[str]:
    result = list(dict.fromkeys(items))
    return sorted(result, key=str.casefold) if sort else result


def _list(value: Any, label: str, *, required: bool = False, refs: bool = False) -> list[str]:
    if value is None:
        result: list[str] = []
    elif isinstance(value, str):
        result = [_clean(value, label) if not refs else value.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result = []
        for index, child in enumerate(value):
            if isinstance(child, Mapping):
                child = child.get("value", child.get("name", child.get("description")))
            result.append(
                child.strip()
                if refs and isinstance(child, str)
                else _clean(child, f"{label}[{index}]")
            )
    else:
        _fail(f"{label} must be text or a list")
    result = _unique(result)
    if required and not result:
        _fail(f"{label} must contain at least one item")
    return result


def _field(fields: Mapping[str, Any], name: str) -> Any:
    return next((fields[alias] for alias in _ALIASES[name] if alias in fields), None)


def _sentence(value: Any, label: str) -> str:
    text = _clean(value, label, limit=360).lstrip("-* ")
    if not re.search(r"[.!?]$", text):
        text += "."
    if re.search(r"[.!?][ \t]+[A-Z]", text):
        _fail(f"{label} must be one sentence")
    return text


def _meaningful(text: str) -> bool:
    words = re.findall(r"[A-Za-z0-9]+", text.casefold())
    return (
        len(words) >= 2
        and (any(word in _VERBS for word in words) or len(words) >= 3)
        and not all(word in _GENERIC for word in words)
    )


def _parts(text: str) -> list[str]:
    result: list[str] = []
    for item in re.split(r"\s*(?:;|\n|\|)\s*", text):
        item = item.strip()
        if not item:
            continue
        pair = re.split(r"\s+and\s+", item, flags=re.I)
        result.extend(part.strip() for part in pair if part.strip()) if len(pair) > 1 and all(
            any(word in _VERBS for word in re.findall(r"[A-Za-z]+", part.casefold()))
            for part in pair
        ) else result.append(item)
    return result


def _candidate(value: Any, label: str, refs: Sequence[str], origin: str) -> list[_Candidate]:
    values = (
        [value]
        if isinstance(value, (str, Mapping))
        else list(value)
        if isinstance(value, Sequence)
        else None
    )
    if values is None or isinstance(value, (bytes, bytearray)):
        _fail(f"{label} must contain responsibility text")
    result: list[_Candidate] = []
    for index, item in enumerate(values):
        fields = copy.deepcopy(dict(item)) if isinstance(item, Mapping) else {}
        text = (
            item.get("responsibility", item.get("capability"))
            if isinstance(item, Mapping)
            else item
        )
        if text is None:
            continue
        fields.pop("responsibility", None)
        fields.pop("capability", None)
        text = _clean(text, f"{label}[{index}]")
        if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
            continue
        parts = [text] if isinstance(item, Mapping) else _parts(text)
        for part_index, part in enumerate(parts):
            if _meaningful(part):
                suffix = f"{label}:{index}:{part_index}" if len(parts) > 1 else None
                result.append(
                    _Candidate(
                        part,
                        fields.copy(),
                        _unique(list(refs) + ([suffix] if suffix else [])),
                        origin,
                    )
                )
    return result


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() in {"true", "yes"}:
        return True
    if value.lower() in {"false", "no"}:
        return False
    if value.lower() in {"null", "none"}:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(value) if value[:1] in {"'", '"'} else value
    except (SyntaxError, ValueError):
        return value


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    result: dict[str, Any] = {}
    current: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.lstrip().startswith("-") and current:
            result.setdefault(current, []).append(_parse_scalar(line.lstrip()[1:]))
            continue
        if ":" not in line:
            current = None
            continue
        name, raw = line.split(":", 1)
        current = name.strip()
        result[current] = [] if not raw.strip() else _parse_scalar(raw)
        if result[current] != []:
            current = None
    return result, "\n".join(lines[end + 1 :])


def _document(value: str | Mapping[str, Any] | None) -> _Document | None:
    if value is None:
        return None
    _reject_hints(value)
    supplied: Any = None
    if isinstance(value, str):
        front, text = _frontmatter(value)
    elif isinstance(value, Mapping):
        raw_front = value.get("frontmatter", {})
        front = (
            _frontmatter(raw_front)[0]
            if isinstance(raw_front, str)
            else copy.deepcopy(dict(raw_front))
            if isinstance(raw_front, Mapping)
            else None
        )
        if front is None:
            _fail("skill document frontmatter must be a mapping or text")
        text = value.get("text", value.get("body", value.get("content", "")))
        if not isinstance(text, str):
            _fail("skill document text must be text")
        supplied = value.get("document_sha256", value.get("content_sha256"))
        if (
            supplied is not None
            and not isinstance(supplied, str)
            or supplied is not None
            and not _HASH.fullmatch(supplied)
        ):
            _fail("skill document digest must be SHA-256")
        if not front:
            front = {
                k: copy.deepcopy(v)
                for k, v in value.items()
                if k
                not in {
                    "frontmatter",
                    "text",
                    "body",
                    "content",
                    "document_sha256",
                    "content_sha256",
                    "path",
                }
            }
        if value.get("path") is not None and (
            not isinstance(value["path"], str) or _ABSOLUTE.search(value["path"])
        ):
            _fail("skill document path is not portable")
    else:
        _fail("skill document must be text or a mapping")
    _reject_hints(front, "$.frontmatter")
    digest = supplied or hashlib.sha256(text.encode()).hexdigest()
    return _Document(copy.deepcopy(front), text, digest, f"document-{digest[:32]}")


def _provider(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        return next(
            (
                _provider(value[k])
                for k in ("provider_id", "provider", "vendor", "name", "package", "id")
                if k in value
            ),
            None,
        )
    return None


def _source(value: Mapping[str, Any]) -> _Source:
    if not isinstance(value, Mapping):
        _fail("Source must be a mapping")
    _reject_hints(value, "$.source")
    if value.get("record_type") in {"action", "workflow", "category", "dot-group"}:
        _fail("legacy Action/category/dot_group/workflow records cannot seed extraction")
    try:
        record = validate_source(value) if value.get("record_type") else copy.deepcopy(dict(value))
    except (SourceValidationError, TypeError) as error:
        raise ExtractionValidationError(f"invalid Source: {error}") from error
    source_id = record.get("source_id") or f"source-{_hash(record)[:32]}"
    if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
        _fail("Source source_id is not portable")
    raw_provenance = record.get("provenance", record.get("provenance_refs", []))
    raw_provenance = [raw_provenance] if isinstance(raw_provenance, Mapping) else raw_provenance
    provenance = []
    for item in raw_provenance if isinstance(raw_provenance, Sequence) else []:
        item = item.get("provenance_id", item.get("id")) if isinstance(item, Mapping) else item
        if isinstance(item, str) and item.strip():
            provenance.append(item.strip())
    provenance = _unique(provenance, sort=True) or [f"provenance-{_hash(source_id)[:32]}"]
    claims = record.get("claimed_capabilities", record.get("claims", []))
    claims = [claims] if isinstance(claims, str) else claims
    if not isinstance(claims, Sequence):
        _fail("Source claimed_capabilities must be a list")
    claims = tuple(_clean(item, "claimed_capabilities") for item in claims)
    declarations = record.get("declarations", {})
    if not isinstance(declarations, Mapping):
        _fail("Source declarations must be a mapping")
    declarations = copy.deepcopy(dict(declarations))
    names = []
    for item in declarations.get("provider_hints", []):
        name = _provider(item)
        if name:
            names.append(name)
    try:
        licence = record.get("licence", record.get("license", {}))
        allowed = (
            can_reuse_source_content(record)
            if value.get("record_type")
            else bool(
                isinstance(licence, Mapping) and licence.get("candidate_content_allowed", False)
            )
        )
    except (SourceValidationError, TypeError):
        allowed = False
    return _Source(
        record,
        source_id,
        tuple(provenance),
        claims,
        declarations,
        tuple(_unique(names, sort=True)),
        bool(allowed),
    )


def _text_candidates(doc: _Document) -> tuple[list[_Candidate], dict[str, list[str]]]:
    candidates: list[_Candidate] = []
    fields: dict[str, list[str]] = {}
    section: str | None = None
    fenced = False
    for line_number, raw in enumerate(doc.text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(?:```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if heading:
            section = _SECTIONS.get(re.sub(r"\s+", " ", heading.group(1).strip().casefold()))
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line)
        # Raw Skills commonly place commands, tables, and local paths below a
        # procedure heading.  They remain Source evidence, but are not compact
        # portable responsibility fields and therefore do not cross this
        # boundary.
        if (
            not re.search(r"[A-Za-z0-9]", re.sub(r"[`*#>|~_-]+", "", line))
            or len(line) > 420
            or _SECRET_VALUE.search(line)
            or _ABSOLUTE.search(line)
        ):
            continue
        ref = f"{doc.ref}#line-{line_number}"
        if section == "responsibilities":
            candidates.extend(_candidate(line, f"document.line{line_number}", [ref], "skill-text"))
        elif section:
            fields.setdefault(section, []).append(line)
        elif re.match(r"^(responsibility|capability)\s*:", line, re.I):
            candidates.extend(
                _candidate(
                    line.split(":", 1)[1].strip(),
                    f"document.line{line_number}",
                    [ref],
                    "skill-text",
                )
            )
    return candidates, {key: _list(value, f"document.{key}") for key, value in fields.items()}


def _front_candidates(front: Mapping[str, Any], doc: _Document | None) -> list[_Candidate]:
    if doc is None:
        return []
    result: list[_Candidate] = []
    for name in ("responsibilities", "responsibility", "capabilities", "description"):
        if name in front:
            values = _description_candidates(front[name]) if name == "description" else front[name]
            if values:
                result.extend(
                    _candidate(values, f"frontmatter.{name}", [doc.ref], "skill-frontmatter")
                )
    if not result and front.get("name"):
        fallback = _responsibility_from_name(front["name"])
        if fallback:
            result.extend(
                _candidate(
                    fallback,
                    "frontmatter.name-fallback",
                    [doc.ref],
                    "skill-name-fallback",
                )
            )
    return result


def _description_candidates(value: Any) -> Any:
    """Turn a Skill description into bounded responsibility candidates.

    Skill ecosystems overwhelmingly describe the reusable job in ``description``
    rather than in a bespoke ``responsibilities`` field.  Trigger language such
    as ``Use when`` is routing evidence, not a second responsibility, so it is
    removed before the ordinary candidate splitter runs.  This keeps Source
    boundaries out of Dot synthesis while ensuring every described Source is
    actually inspected during Genesis.
    """

    if not isinstance(value, str):
        return value
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return text
    values: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        sentence = sentence.strip()
        # A routing sentence is evidence about matching, not another reusable
        # capability.  Truncated upstream descriptions such as ``USE T`` are
        # also dropped rather than allowed to invalidate the complete intake.
        if _ROUTING_DESCRIPTION.match(sentence):
            continue
        sentence = re.split(
            r"\s*[—–-]\s*(?:use|activate|invoke|trigger)\b",
            sentence,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if sentence and not _ROUTING_DESCRIPTION.match(sentence):
            values.append(_normalise_description_responsibility(sentence))
    values = [item for item in values if item]
    if not values:
        return ""
    # Frontmatter descriptions route one Skill and routinely append keywords,
    # examples, trigger phrases, or coverage marketing as later sentences.
    # Multiple responsibilities must come from explicit structured/body
    # evidence, not sentence count in a routing description.
    return {"responsibility": values[0]}


def _normalise_description_responsibility(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    words = re.findall(r"[A-Za-z0-9]+", text)
    first = words[0].casefold() if words else ""
    if (
        len(words) < 2
        or (len(words) < 3 and first not in _VERBS)
        or re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I)
        or re.search(r",\s*[.!?]?$", text)
    ):
        return ""
    if first.endswith("s") and first[:-1] in _VERBS:
        text = re.sub(r"^[A-Za-z]+", first[:-1].capitalize(), text, count=1)
        first = first[:-1]
    text = re.sub(
        r"\band\s+([A-Za-z]+)s\b",
        lambda match: f"and {match.group(1)}"
        if match.group(1).casefold() in _VERBS
        else match.group(0),
        text,
        flags=re.I,
    )
    text = text[:1].upper() + text[1:]
    return text if first in _VERBS else f"Use {text}"


def _detected_provider(text: str) -> tuple[str, str] | None:
    lowered = text.casefold()
    connector_match: tuple[str, str] | None = None
    for marker, provider_id in _PROVIDER_MARKERS:
        if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", lowered):
            connector_bound = re.search(
                rf"\b(?:using|with|via|through)\b[^.!?]*\b{re.escape(marker)}\b",
                lowered,
                re.I,
            )
            if connector_bound is None:
                return marker, provider_id
            connector_match = connector_match or (marker, provider_id)
    return connector_match


def _normalise_intrinsic_provider_text(text: str) -> str:
    result = _LANGUAGE_SUFFIX.sub("", text)
    result = _LANGUAGE_MODIFIER.sub("", result)
    result = re.sub(r"\s*/\s*", " ", result)
    # SDK/library and programming-language variants are alternative
    # Implementations of one provider-specific responsibility, not separate
    # Dots.  Retain the named provider product and drop packaging detail.
    result = result.rstrip(" .!?")
    packaged = re.match(
        r"^(.*?)(?:\bSDK\b|\bclient\s+library\b|\blibrary\b)(?:\s+.*)?$",
        result,
        re.I,
    )
    if packaged and packaged.group(1).strip():
        result = packaged.group(1).strip()
    result = re.sub(r"\s*\([^)]*(?:package|com\.|@azure|sdk)[^)]*\)", "", result, flags=re.I)
    result = re.sub(r"\s+", " ", result).strip(" ,;:-")
    first = next(iter(re.findall(r"[A-Za-z]+", result.casefold())), "")
    if first.endswith("s") and first[:-1] in _VERBS:
        result = re.sub(r"^[A-Za-z]+", first[:-1].capitalize(), result, count=1)
        first = first[:-1]
    result = f"Use {result}" if first not in _VERBS else result[:1].upper() + result[1:]
    return result


def _responsibility_from_name(value: Any) -> str:
    """Create a bounded fallback only when a Skill has no job description."""

    if not isinstance(value, str):
        return ""
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKC", value))
    if not words:
        return ""
    gerunds = {
        "dispatching": "Dispatch",
        "executing": "Execute",
        "finishing": "Finish",
        "receiving": "Receive",
        "requesting": "Request",
        "using": "Use",
        "writing": "Write",
    }
    first = words[0].casefold()
    if first in gerunds:
        words[0] = gerunds[first]
    elif first in _VERBS:
        words[0] = first.capitalize()
    else:
        words.insert(0, "Perform")
    return " ".join(words) + "."


def _semantic(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None, source: _Source
) -> list[_Candidate]:
    if value is None:
        return []
    _reject_hints(value, "$.semantic_fields")
    values = (
        [value]
        if isinstance(value, Mapping)
        else list(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        else None
    )
    if values is None:
        _fail("semantic_fields must be a mapping or list")
    allowed = set((source.source_id, *source.provenance))
    result: list[_Candidate] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            _fail(f"semantic_fields[{index}] must be a mapping")
        refs = _list(item.get("source_refs"), "semantic source refs", refs=True)
        refs += _list(item.get("provenance_refs"), "semantic provenance refs", refs=True)
        refs += _list(item.get("evidence_refs"), "semantic evidence refs", refs=True)
        if not refs or not set(refs).issubset(allowed):
            _fail("semantic fields require matching Source refs")
        if item.get("responsibility") is None:
            _fail("semantic field requires responsibility")
        fields = copy.deepcopy(dict(item))
        fields.pop("responsibility", None)
        for name in ("source_refs", "provenance_refs", "evidence_refs"):
            fields.pop(name, None)
        result.extend(
            _candidate(
                item["responsibility"], f"semantic_fields[{index}]", refs, "assisted-semantic-field"
            )
        )
        if result:
            result[-1].fields.update(fields)
    return result


def _strip_provider(text: str, names: Sequence[str]) -> str:
    for name in sorted((name for name in names if name), key=len, reverse=True):
        text = re.sub(
            rf"\b(?:using|with|via|through)\b[^.!?]*\b{re.escape(name)}\b[^.!?]*",
            "",
            text,
            flags=re.I,
        )
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", "", text, flags=re.I)
    text = _LANGUAGE_SUFFIX.sub("", text)
    text = re.sub(r"\b(?:SDK|API|CLI)\s*$", "", text, flags=re.I)
    text = re.sub(
        r"\b(?:via|using|with|through)\s*(?=[,.!?]|$)", "", re.sub(r"\s+", " ", text), flags=re.I
    )
    text = re.sub(r"\s+([,.!?])(?=\s|$)", r"\1", text)
    return text.strip(" ,;:") or text


def _provider_semantics(
    candidate: _Candidate, source: _Source
) -> tuple[dict[str, Any], tuple[str, ...], bool]:
    raw = _field(candidate.fields, "provider_dependency")
    names = list(source.providers)
    provider = _provider(raw)
    intrinsic = False
    reason = None
    if provider:
        names.append(provider)
    detected = _detected_provider(candidate.text)
    if detected is not None:
        marker, detected_id = detected
        names.append(marker)
        connector_bound = re.search(
            rf"\b(?:using|with|via|through)\b[^.!?]*\b{re.escape(marker)}\b",
            candidate.text,
            re.I,
        )
        if raw is None and connector_bound is None:
            provider = detected_id
            intrinsic = True
            reason = (
                "The responsibility directly names provider-specific semantics; "
                "language/runtime variants remain implementation details."
            )
    if isinstance(raw, Mapping):
        intrinsic = (
            raw.get("intrinsic") is True
            or raw.get("essential") is True
            or str(raw.get("kind", raw.get("semantics", ""))).casefold()
            in {"intrinsic", "essential", "required"}
        )
        intrinsic = intrinsic or raw.get("intrinsic_provider_responsibility") is not None
        reason = raw.get("reason", raw.get("explanation", raw.get("reason_code")))
    names = tuple(_unique(names, sort=True))
    if intrinsic:
        if not provider:
            _fail("intrinsic provider dependency requires provider_id")
        return (
            {
                "kind": "intrinsic",
                "intrinsic": True,
                "provider_id": _clean(provider, "provider_id"),
                "intrinsic_provider_responsibility": {
                    "reason": _clean(
                        reason or "The Source states that this provider is essential.",
                        "provider reason",
                    ),
                    "evidence_refs": _unique(candidate.refs),
                },
            },
            names,
            True,
        )
    return {"kind": "abstract", "intrinsic": False}, names, False


def deterministic_responsibility_signature(
    responsibility: str,
    *,
    provider_names: Sequence[str] = (),
    intrinsic: bool = False,
    provider_id: str | None = None,
) -> str:
    text = responsibility.casefold()
    if not intrinsic:
        text = _strip_provider(text, provider_names)
    text = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text), re.UNICODE))
    if intrinsic and provider_id:
        text += f" provider {provider_id.casefold()}"
    if not text:
        raise ResponsibilityExtractionError("responsibility signature requires text")
    return f"responsibility-{hashlib.sha256(text.encode()).hexdigest()[:32]}"


normalised_responsibility_signature = deterministic_responsibility_signature
normalized_responsibility_signature = deterministic_responsibility_signature
responsibility_signature = deterministic_responsibility_signature


def _digest_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(record.get(key))
        for key in (
            "responsibility",
            "normalized_signature",
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
            "evidence_refs",
            "candidate_contribution_allowed",
        )
    }


def _record(candidate: _Candidate, source: _Source, doc: _Document | None) -> dict[str, Any]:
    provider_data, provider_names, intrinsic = _provider_semantics(candidate, source)
    text_value = (
        _normalise_intrinsic_provider_text(candidate.text) if intrinsic else candidate.text
    )
    text = _sentence(text_value, "responsibility")
    if not intrinsic:
        text = _sentence(_strip_provider(text, provider_names), "responsibility")
    signature = deterministic_responsibility_signature(
        text,
        provider_names=provider_names,
        intrinsic=intrinsic,
        provider_id=provider_data.get("provider_id"),
    )
    refs = _unique(
        candidate.refs + [source.source_id, *source.provenance] + ([doc.ref] if doc else []),
        sort=True,
    )
    source_refs = _list(_field(candidate.fields, "source_refs"), "source_refs", refs=True) or [
        source.source_id
    ]
    provenance_refs = _list(
        _field(candidate.fields, "provenance_refs"), "provenance_refs", refs=True
    ) or list(source.provenance)
    allowed_refs = set((source.source_id, *source.provenance))
    if not set(source_refs + provenance_refs).issubset(allowed_refs):
        _fail("record references do not belong to Source")
    fields = {
        name: _list(_field(candidate.fields, name), name)
        for name in ("inputs", "outputs", "preconditions", "knowledge", "procedure_outline")
    }
    fields["inputs"] = fields["inputs"] or ["the bounded input described by Source evidence"]
    fields["outputs"] = fields["outputs"] or ["one responsibility finding for review"]
    fields["preconditions"] = fields["preconditions"] or [
        "the referenced Source evidence is available"
    ]
    fields["procedure_outline"] = fields["procedure_outline"] or [
        "Review retained evidence before implementation."
    ]
    effects = _list(_field(candidate.fields, "side_effects"), "side_effects")
    if not effects:
        for key, prefix in (("write_effects", "write"), ("network_effects", "network")):
            for item in source.declarations.get(key, []):
                name = item.get("name") if isinstance(item, Mapping) else item
                effects.append(f"{prefix}: {_clean(name, f'declarations.{key}')}")
    fields["side_effects"] = _unique(effects) or [
        "No provider execution or persistence side effect is established by extraction."
    ]
    raw_verification = _field(candidate.fields, "verification")
    verification = {"status": "unverified", "evidence_refs": refs}
    if isinstance(raw_verification, Mapping):
        status = str(raw_verification.get("status", "unverified")).casefold()
        if status not in {"unverified", "in-progress", "verified", "failed", "claimed"}:
            _fail("verification.status is invalid")
        verification = {"status": status, "evidence_refs": refs}
    elif raw_verification is not None:
        verification = {
            "status": "claimed",
            "claims": _list(raw_verification, "verification"),
            "evidence_refs": refs,
        }
    raw_failure = _field(candidate.fields, "failure_recovery")
    if isinstance(raw_failure, Mapping):
        failures = _list(
            raw_failure.get("failure_modes", raw_failure.get("failure", raw_failure.get("errors"))),
            "failure_modes",
        )
        recovery = raw_failure.get(
            "recovery", raw_failure.get("recovery_strategy", raw_failure.get("rollback"))
        )
    else:
        failures, recovery = _list(raw_failure, "failure_modes"), None
    failure_recovery = {
        "failure_modes": failures or ["The responsibility boundary or evidence may be incomplete."],
        "recovery": _clean(recovery, "recovery")
        if recovery
        else "Keep the finding unverified and gather a portable Source reference.",
        "evidence_refs": refs,
    }
    confidence = str(
        _field(candidate.fields, "confidence")
        or ("high" if candidate.origin == "skill-frontmatter" else "medium")
    ).casefold()
    if confidence not in {"low", "medium", "high"}:
        _fail("confidence must be low, medium, or high")
    uncertainties = _list(_field(candidate.fields, "uncertainties"), "uncertainties")
    if candidate.origin == "source-claim":
        uncertainties.append("The responsibility is a Source claim, not a Capability Dot.")
    if provider_names and not intrinsic:
        uncertainties.append(
            "Provider identity was abstracted because intrinsic necessity was not evidenced."
        )
    if verification["status"] != "verified":
        uncertainties.append("No provider execution was performed by extraction.")
    record: dict[str, Any] = {
        "record_type": EXTRACTION_RECORD_TYPE,
        "record_version": EXTRACTION_RECORD_VERSION,
        "responsibility": text,
        "normalized_signature": signature,
        "normalised_signature": signature,
        **fields,
        "provider_dependency": provider_data,
        "verification": verification,
        "failure_recovery": failure_recovery,
        "source_refs": _unique(source_refs, sort=True),
        "provenance_refs": _unique(provenance_refs, sort=True),
        "evidence_refs": refs,
        "candidate_contribution_allowed": source.candidate_allowed,
        "extraction_method": EXTRACTION_METHOD,
        "extraction_version": EXTRACTION_METHOD_VERSION,
        "confidence": confidence,
        "uncertainties": _unique(uncertainties),
    }
    record["evidence"] = {
        "source_refs": record["source_refs"],
        "provenance_refs": record["provenance_refs"],
        "evidence_refs": refs,
        **({"document_sha256": doc.digest, "document_ref": doc.ref} if doc else {}),
    }
    record["evidence_digest"] = _hash(_digest_payload(record))
    record["extraction"] = {
        "method": EXTRACTION_METHOD,
        "version": EXTRACTION_METHOD_VERSION,
        "evidence_digest": record["evidence_digest"],
        "confidence": confidence,
        "uncertainties": record["uncertainties"],
        "candidate_contribution_allowed": source.candidate_allowed,
        "content_reused": False,
        "origin": candidate.origin,
    }
    return validate_responsibility_record(record, source=source.record)


def extract_responsibilities(
    source: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    skill_document: str | Mapping[str, Any] | None = None,
    *,
    semantic_fields: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    model_assisted: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic responsibility findings; empty means No Finding."""
    sources = (
        [source]
        if isinstance(source, Mapping)
        else list(source)
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray))
        else None
    )
    if sources is None:
        _fail("source must be a Source mapping or sequence")
    if not sources:
        return []
    if metadata is not None:
        _reject_hints(metadata, "$.metadata")
    assisted = semantic_fields if semantic_fields is not None else model_assisted
    all_records: list[dict[str, Any]] = []
    for raw in sources:
        source_view = _source(raw)
        doc_value: Any = skill_document
        if isinstance(skill_document, Mapping) and "documents" in skill_document:
            docs = skill_document["documents"]
            if not isinstance(docs, Mapping):
                _fail("skill document documents must be a mapping")
            doc_value = docs.get(source_view.source_id)
        doc = _document(doc_value)
        candidates: list[_Candidate] = []
        for claim in source_view.claims:
            prefix, separator, value = claim.partition(":")
            claim_value: Any = claim
            if separator and prefix.strip().casefold() in {"name", "title", "id"}:
                continue
            if separator and prefix.strip().casefold() == "description":
                claim_value = _description_candidates(value.strip())
            elif not separator:
                normalised = _normalise_description_responsibility(claim)
                claim_value = {"responsibility": normalised} if normalised else ""
            if claim_value and _meaningful(str(claim_value)):
                candidates.extend(
                    _candidate(
                        claim_value,
                        "source.claim",
                        [f"claim:{claim}"],
                        "source-claim",
                    )
                )
        source_meta = metadata or raw.get("metadata") if isinstance(raw, Mapping) else metadata
        if source_meta:
            if not isinstance(source_meta, Mapping):
                _fail("metadata must be a mapping")
            _reject_hints(source_meta, "$.metadata")
            for name in ("responsibilities", "responsibility", "capabilities", "description"):
                if name in source_meta:
                    values = (
                        _description_candidates(source_meta[name])
                        if name == "description"
                        else source_meta[name]
                    )
                    if values:
                        candidates.extend(
                            _candidate(
                                values,
                                f"metadata.{name}",
                                [f"metadata:{name}"],
                                "structured-source-metadata",
                            )
                        )
        if doc:
            candidates.extend(_front_candidates(doc.frontmatter, doc))
            text_candidates, text_fields = _text_candidates(doc)
            candidates.extend(text_candidates)
            for item in candidates:
                for name, value in text_fields.items():
                    item.fields.setdefault(name, value)
            for item in candidates:
                for name, aliases in _ALIASES.items():
                    if name not in item.fields:
                        for alias in aliases:
                            if alias in doc.frontmatter and not isinstance(
                                doc.frontmatter[alias], Mapping
                            ):
                                item.fields[name] = doc.frontmatter[alias]
                                break
        candidates.extend(_semantic(assisted, source_view))
        if not candidates:
            continue
        records = [_record(item, source_view, doc) for item in candidates]
        by_signature: dict[str, dict[str, Any]] = {}
        for record in records:
            if record["normalized_signature"] not in by_signature:
                by_signature[record["normalized_signature"]] = record
            else:
                current = by_signature[record["normalized_signature"]]
                for name in ("evidence_refs", "uncertainties", "knowledge"):
                    current[name] = _unique(current[name] + record[name], sort=True)
                current["evidence"]["evidence_refs"] = current["evidence_refs"]
                current["evidence_digest"] = _hash(_digest_payload(current))
                current["extraction"]["evidence_digest"] = current["evidence_digest"]
                current["extraction"]["uncertainties"] = current["uncertainties"]
        all_records.extend(by_signature.values())
    return sorted(
        all_records,
        key=lambda item: (
            item["normalized_signature"],
            item["source_refs"],
            item["evidence_digest"],
        ),
    )


def validate_responsibility_record(
    value: Mapping[str, Any], *, source: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate one compact evidence subobject and return a detached copy."""
    if not isinstance(value, Mapping):
        _fail("responsibility evidence must be a mapping")
    record = copy.deepcopy(dict(value))
    _reject_hints(record)
    _privacy(record)
    if (
        record.get("record_type") != EXTRACTION_RECORD_TYPE
        or record.get("record_version") != EXTRACTION_RECORD_VERSION
    ):
        _fail("unsupported responsibility extraction record")
    if _sentence(record.get("responsibility"), "responsibility") != record.get("responsibility"):
        _fail("responsibility is not canonical")
    signature = record.get("normalized_signature")
    intrinsic = (
        isinstance(record.get("provider_dependency"), Mapping)
        and record["provider_dependency"].get("kind") == "intrinsic"
    )
    provider_id = record.get("provider_dependency", {}).get("provider_id") if intrinsic else None
    if record.get(
        "normalised_signature"
    ) != signature or signature != deterministic_responsibility_signature(
        record["responsibility"],
        provider_names=[provider_id] if provider_id else [],
        intrinsic=intrinsic,
        provider_id=provider_id,
    ):
        _fail("normalized signature is not deterministic")
    provider = record.get("provider_dependency")
    if (
        not isinstance(provider, Mapping)
        or (intrinsic and not provider.get("provider_id"))
        or (not intrinsic and provider.get("provider_id") is not None)
    ):
        _fail("provider dependency semantics are invalid")
    for name in ("inputs", "outputs", "preconditions", "side_effects", "procedure_outline"):
        _list(record.get(name), name, required=True)
    _list(record.get("knowledge"), "knowledge")
    failure = record.get("failure_recovery")
    if not isinstance(failure, Mapping):
        _fail("failure_recovery is required")
    _list(failure.get("failure_modes"), "failure_modes", required=True)
    _clean(failure.get("recovery"), "recovery")
    if not isinstance(record.get("verification"), Mapping) or record["verification"].get(
        "status"
    ) not in {"unverified", "in-progress", "verified", "failed", "claimed"}:
        _fail("verification is invalid")
    source_refs = _list(record.get("source_refs"), "source_refs", required=True, refs=True)
    provenance = _list(record.get("provenance_refs"), "provenance_refs", required=True, refs=True)
    evidence = _list(record.get("evidence_refs"), "evidence_refs", required=True, refs=True)
    if not set(source_refs + provenance).issubset(evidence):
        _fail("evidence_refs must include Source and provenance refs")
    if not isinstance(record.get("candidate_contribution_allowed"), bool):
        _fail("candidate contribution gate is required")
    if (
        record.get("extraction_method") != EXTRACTION_METHOD
        or record.get("extraction_version") != EXTRACTION_METHOD_VERSION
    ):
        _fail("unsupported extraction method")
    if record.get("confidence") not in {"low", "medium", "high"}:
        _fail("confidence is invalid")
    if not _HASH.fullmatch(record.get("evidence_digest", "")):
        _fail("evidence_digest must be SHA-256")
    evidence_block = record.get("evidence")
    if not isinstance(evidence_block, Mapping) or any(
        evidence_block.get(name) != expected
        for name, expected in (
            ("source_refs", source_refs),
            ("provenance_refs", provenance),
            ("evidence_refs", evidence),
        )
    ):
        _fail("compact evidence references do not match")
    extraction = record.get("extraction")
    if not isinstance(extraction, Mapping) or any(
        extraction.get(name) != record.get(record_name)
        for name, record_name in (
            ("method", "extraction_method"),
            ("version", "extraction_version"),
            ("evidence_digest", "evidence_digest"),
            ("confidence", "confidence"),
            ("uncertainties", "uncertainties"),
            ("candidate_contribution_allowed", "candidate_contribution_allowed"),
        )
    ):
        _fail("extraction metadata does not match")
    if source is not None:
        source_view = _source(source)
        if (
            source_view.candidate_allowed != record["candidate_contribution_allowed"]
            or source_view.source_id not in source_refs
            or not set(provenance).issubset(source_view.provenance)
        ):
            raise ExtractionLicenceError("record does not match Source licence or provenance")
    if _hash(_digest_payload(record)) != record["evidence_digest"]:
        _fail("evidence_digest does not match structured evidence")
    return record


validate_responsibility = validate_responsibility_record
validate_responsibility_evidence = validate_responsibility_record


def validate_extraction(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    source: Mapping[str, Any] | None = None,
) -> Any:
    if isinstance(value, Mapping):
        if "responsibilities" not in value:
            return validate_responsibility_record(value, source=source)
        result = copy.deepcopy(dict(value))
        result["responsibilities"] = [
            validate_responsibility_record(item, source=source)
            for item in value["responsibilities"]
        ]
        return result
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail("extraction must be a list or envelope")
    return [validate_responsibility_record(item, source=source) for item in value]


def extract_responsibility_evidence(
    source: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    skill_document: str | Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    records = extract_responsibilities(source, skill_document, **kwargs)
    return {
        "record_type": EXTRACTION_RECORD_TYPE,
        "record_version": EXTRACTION_RECORD_VERSION,
        "finding": "finding" if records else "no-finding",
        "responsibilities": records,
        "source_only": True,
        "persistent_registry_created": False,
    }


extract_responsibility_candidates = extract_responsibilities
extract = extract_responsibilities

__all__ = [
    "EXTRACTION_METHOD",
    "EXTRACTION_METHOD_VERSION",
    "EXTRACTION_RECORD_TYPE",
    "EXTRACTION_RECORD_VERSION",
    "ExtractionLicenceError",
    "ExtractionValidationError",
    "ResponsibilityEvidenceError",
    "ResponsibilityExtractionError",
    "ResponsibilityExtractionValidationError",
    "deterministic_responsibility_signature",
    "extract",
    "extract_responsibilities",
    "extract_responsibility_candidates",
    "extract_responsibility_evidence",
    "normalised_responsibility_signature",
    "normalized_responsibility_signature",
    "responsibility_signature",
    "validate_extraction",
    "validate_responsibility",
    "validate_responsibility_evidence",
    "validate_responsibility_record",
]
