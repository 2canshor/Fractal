"""Capability registry, deterministic packages, projections, and routing evals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class CapabilityError(RuntimeError):
    """Raised when capability state or provenance cannot be verified."""


def validate_skill_source(source: Path) -> dict[str, Any]:
    """Validate the portable subset every adapter needs before packaging."""
    source = Path(source)
    entrypoint = source / "SKILL.md"
    if not entrypoint.is_file():
        raise CapabilityError(f"Skill source has no SKILL.md: {source}")
    content = entrypoint.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<frontmatter>.*?)\n---\n", content, flags=re.DOTALL)
    if match is None:
        raise CapabilityError(f"Skill source has invalid frontmatter: {source.name}")
    name_match = re.search(
        r"^name:\s*[\"']?(?P<name>[^\"'\n]+)", match["frontmatter"], re.MULTILINE
    )
    description_match = re.search(
        r"^description:\s*(?P<description>.+)", match["frontmatter"], re.MULTILINE
    )
    if name_match is None or name_match["name"].strip() != source.name:
        raise CapabilityError(f"Skill source name does not match its folder: {source.name}")
    if description_match is None or len(description_match["description"].strip()) < 20:
        raise CapabilityError(f"Skill source needs a discriminating description: {source.name}")
    if "TODO" in content or "[TODO" in content:
        raise CapabilityError(f"Skill source contains unfinished scaffold text: {source.name}")
    openai_metadata = source / "agents" / "openai.yaml"
    if not openai_metadata.is_file() or f"${source.name}" not in openai_metadata.read_text():
        raise CapabilityError(f"Skill UI metadata is missing its explicit example: {source.name}")
    return {"skill_id": source.name, "valid": True}


def load_capability_registry() -> dict[str, Any]:
    """Load the packaged registry and verify ids, status evidence, and schema."""
    registry = json.loads(
        files("fractal.data").joinpath("capability-registry.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        files("fractal.schemas")
        .joinpath("capability-registry.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = sorted(Draft202012Validator(schema).iter_errors(registry), key=lambda item: item.path)
    if errors:
        raise CapabilityError(errors[0].message)
    ids = [item["capability_id"] for item in registry["capabilities"]]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise CapabilityError("Capability ids must be unique and sorted")
    for capability in registry["capabilities"]:
        execution = capability["status"]["execution"]
        if execution["state"].startswith("verified") and not execution["evidence_ids"]:
            raise CapabilityError("Verified execution requires evidence")
    return registry


def get_capability_status(capability_id: str) -> dict[str, Any]:
    """Return the three separate capability status dimensions."""
    registry = load_capability_registry()
    capability = next(
        (item for item in registry["capabilities"] if item["capability_id"] == capability_id),
        None,
    )
    if capability is None:
        return {
            "capability_id": capability_id,
            "availability": {"state": "unknown", "evidence_ids": []},
            "activation_authority": {
                "state": "unknown",
                "authority": "unknown",
                "evidence_ids": [],
            },
            "execution": {"state": "unknown", "evidence_ids": []},
        }
    return {"capability_id": capability_id, **capability["status"]}


def skill_tree_manifest(source: Path) -> dict[str, str]:
    """Hash every regular skill file without following symlinks."""
    source = Path(source)
    validate_skill_source(source)
    manifest: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise CapabilityError(f"Skill source contains a symlink: {relative}")
        if path.is_file():
            manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def build_skill_package(source: Path, destination: Path) -> dict[str, Any]:
    """Build one deterministic .skill archive from canonical source."""
    source = Path(source)
    destination = Path(destination)
    skill_id = source.name
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id) is None:
        raise CapabilityError(f"Invalid Skill id: {skill_id}")
    manifest = skill_tree_manifest(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(manifest):
                info = zipfile.ZipInfo(f"{skill_id}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (source / relative).read_bytes())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    package_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
    verification = verify_skill_package(source, destination)
    return {
        "skill_id": skill_id,
        "source_manifest": manifest,
        "package_sha256": package_sha256,
        "verified": verification["verified"],
    }


def verify_skill_package(source: Path, package: Path) -> dict[str, Any]:
    """Compare every package entry with canonical source."""
    source = Path(source)
    skill_id = source.name
    expected = {f"{skill_id}/{key}": value for key, value in skill_tree_manifest(source).items()}
    with zipfile.ZipFile(package) as archive:
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names}
    if actual != expected:
        raise CapabilityError(f"Skill package does not match canonical source: {skill_id}")
    return {"skill_id": skill_id, "verified": True, "file_count": len(expected)}


def verify_skill_projection(source: Path, projection: Path) -> dict[str, Any]:
    """Verify a staged copied or linked projection against canonical source."""
    source = Path(source)
    projection = Path(projection)
    target = projection.resolve(strict=True) if projection.is_symlink() else projection
    try:
        matches = skill_tree_manifest(source) == skill_tree_manifest(target)
    except CapabilityError as error:
        raise CapabilityError(f"Skill projection drift: {source.name}") from error
    if not matches:
        raise CapabilityError(f"Skill projection drift: {source.name}")
    return {
        "skill_id": source.name,
        "verified": True,
        "projection_kind": "symlink" if projection.is_symlink() else "copy",
    }


def select_capability(request: str) -> str | None:
    """Small deterministic selector used only as a routing eval baseline."""
    normalised = request.lower()
    routes = [
        ("web-operations", {"submit", "fill form", "click through", "monitor", "track page"}),
        ("system-review", {"system review", "completed project"}),
        (
            "project-review",
            {
                "project review",
                "review active project",
                "milestone review",
                "exception review",
            },
        ),
        ("legacy-material-review", {"legacy", "replacement", "old skill"}),
        ("capability-development", {"create a skill", "build a capability", "skill eval"}),
        ("delegation-workflow", {"delegate", "hand off", "subagent"}),
        ("interface-design", {"interface", "landing page", "accessibility", "ui", "ux"}),
        ("writing-authenticity", {"less like ai", "sound natural", "author's voice"}),
        ("naming-system", {"rename", "name this", "class name", "technical id"}),
        ("clarification", {"material unknown", "blind spot", "clarify architecture"}),
        ("research", {"research", "search web", "find sources", "extract page"}),
    ]
    matches = [
        capability for capability, signals in routes if any(item in normalised for item in signals)
    ]
    return matches[0] if matches else None
