from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from fractal.capability_genesis import (
    CapabilityGenesisError,
    build_extraction_coverage,
    build_replayable_compiler_material,
    load_verified_source_documents,
)
from fractal.capability_source import build_source, empty_source_catalogue, merge_source_catalogue


def _source(path: str, content: bytes, *, name: str = "search-documents") -> dict[str, object]:
    return build_source(
        name=name,
        source_type="skill",
        donor_id="donor-a",
        donor_name="Donor A",
        locator="https://example.test/donor-a",
        commit="a" * 40,
        path=path,
        content_sha256="b" * 64,
        file_sha256=hashlib.sha256(content).hexdigest(),
        licence={
            "status": "verified",
            "spdx": "MIT",
            "candidate_content_allowed": True,
            "evidence": "https://example.test/LICENSE",
            "constraints": ["Retain notice."],
        },
        constraints={
            "content_reuse": "candidate-copy",
            "runtime_dependency": "forbidden",
            "execution_authority": False,
            "persistence_authority": False,
            "notes": [],
        },
        claimed_capabilities=[
            "name: search-documents",
            "description: Search supplied documents for grounded answers. "
            "Use when evidence is retained.",
        ],
    )


def test_full_source_inspection_records_finding_and_no_finding(tmp_path: Path) -> None:
    skill_bytes = (
        b"---\nname: search-documents\n"
        b"description: Search supplied documents for grounded answers.\n---\n"
        b"## Verification\nConfirm every answer has evidence.\n"
    )
    skill_path = tmp_path / "skills" / "search" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_bytes(skill_bytes)
    source = _source("skills/search/SKILL.md", skill_bytes)
    library = build_source(
        name="Research index",
        source_type="library",
        donor_id="index-a",
        donor_name="Index A",
        locator="https://example.test/index-a",
        commit="c" * 40,
        content_sha256="d" * 64,
        licence={
            "status": "missing",
            "spdx": None,
            "candidate_content_allowed": False,
            "evidence": "https://example.test/index-a",
            "constraints": ["Metadata only."],
        },
        constraints={
            "content_reuse": "metadata-only",
            "runtime_dependency": "forbidden",
            "execution_authority": False,
            "persistence_authority": False,
            "notes": [],
        },
    )
    catalogue = merge_source_catalogue(empty_source_catalogue(), source)
    catalogue = merge_source_catalogue(catalogue, library)

    documents = load_verified_source_documents(catalogue, donor_roots={"donor-a": tmp_path})
    evidence = build_extraction_coverage(catalogue, documents)

    assert evidence["source_count"] == 2
    assert evidence["source_decision_count"] == 2
    assert evidence["finding_source_count"] == 1
    assert evidence["no_finding_source_count"] == 1
    assert evidence["responsibility_count"] == 1
    assert evidence["candidate_contribution_count"] == 1
    assert evidence["raw_contents_persisted"] is False
    assert evidence["responsibilities"][0]["responsibility"] == (
        "Search supplied documents for grounded answers."
    )
    assert {item["finding"] for item in evidence["source_decisions"]} == {
        "responsibilities-found",
        "no-finding",
    }
    material = build_replayable_compiler_material(catalogue, evidence)
    assert material["source_count"] == 2
    assert material["source_decision_count"] == 2
    assert material["source_claim_count"] == 1
    assert material["raw_contents_persisted"] is False
    assert all(
        source["claimed_capabilities"] == []
        for source in material["source_catalogue"]["sources"]
    )


def test_source_file_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    expected = b"---\ndescription: Read evidence.\n---\n"
    source = _source("SKILL.md", expected)
    (tmp_path / "SKILL.md").write_bytes(b"tampered")
    catalogue = merge_source_catalogue(empty_source_catalogue(), source)

    with pytest.raises(CapabilityGenesisError, match="hash mismatch"):
        load_verified_source_documents(catalogue, donor_roots={"donor-a": tmp_path})


def test_unknown_document_source_is_rejected(tmp_path: Path) -> None:
    content = b"---\ndescription: Read evidence.\n---\n"
    source = _source("SKILL.md", content)
    (tmp_path / "SKILL.md").write_bytes(content)
    catalogue = merge_source_catalogue(empty_source_catalogue(), source)
    documents = load_verified_source_documents(catalogue, donor_roots={"donor-a": tmp_path})
    tampered = copy.deepcopy(documents)
    tampered["source-unknown"] = {}

    with pytest.raises(CapabilityGenesisError, match="unknown Sources"):
        build_extraction_coverage(catalogue, tampered)
