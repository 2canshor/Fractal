"""Focused tests for deterministic responsibility extraction evidence."""

from __future__ import annotations

import copy
import json
import re

import pytest

from fractal.capability_extraction import (
    EXTRACTION_METHOD,
    ExtractionValidationError,
    deterministic_responsibility_signature,
    extract_responsibilities,
    extract_responsibility_evidence,
    validate_extraction,
    validate_responsibility_record,
)
from fractal.capability_source import build_source

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
CONTENT_A = "c" * 64
CONTENT_B = "d" * 64


def _source(
    *,
    commit: str = COMMIT_A,
    content_sha256: str = CONTENT_A,
    claims: tuple[str, ...] = ("parse documents",),
    licence: dict[str, object] | None = None,
    constraints: dict[str, object] | None = None,
    declarations: dict[str, object] | None = None,
):
    return build_source(
        name="Structured Skill",
        source_type="skill",
        donor_id="example-donor",
        locator="https://example.invalid/skill",
        commit=commit,
        version="1.0.0",
        content_sha256=content_sha256,
        retrieved_at="2026-08-25T00:00:00Z",
        licence=licence or {"status": "verified", "spdx": "MIT", "evidence": "LICENSE"},
        constraints=constraints or {"content_reuse": "candidate-copy"},
        claimed_capabilities=claims,
        declarations=declarations or {},
    )


def test_structured_skill_document_splits_multiple_responsibilities() -> None:
    source = _source(claims=())
    document = {
        "frontmatter": {
            "responsibilities": [
                {
                    "responsibility": "Parse documents.",
                    "inputs": ["a supplied document"],
                    "outputs": ["structured records"],
                    "preconditions": ["the document is readable"],
                },
                {
                    "responsibility": "Validate records.",
                    "inputs": ["structured records"],
                    "outputs": ["validation findings"],
                },
            ],
            "procedure_outline": ["Read the document.", "Emit bounded findings."],
        },
        "text": "This text is retained only through its digest.",
    }

    records = extract_responsibilities(source, document)

    assert [record["responsibility"] for record in records] == [
        "Parse documents.",
        "Validate records.",
    ]
    assert all(record["record_type"] == "responsibility-extraction" for record in records)
    assert all("dot_id" not in record for record in records)
    assert all(record["extraction_method"] == EXTRACTION_METHOD for record in records)
    assert validate_extraction(records) == records


def test_identical_candidates_share_signature_but_retain_source_provenance() -> None:
    first = _source(commit=COMMIT_A, content_sha256=CONTENT_A)
    second = _source(commit=COMMIT_B, content_sha256=CONTENT_B)

    records = extract_responsibilities([first, second])

    assert len(records) == 2
    assert records[0]["normalized_signature"] == records[1]["normalized_signature"]
    assert records[0]["source_refs"] != records[1]["source_refs"]
    assert records[0]["provenance_refs"] != records[1]["provenance_refs"]
    assert records[0]["evidence_digest"] != records[1]["evidence_digest"]


def test_provider_name_is_abstracted_unless_intrinsic() -> None:
    source = _source(
        claims=(),
        declarations={"provider_hints": [{"name": "Example Provider", "confidence": "observed"}]},
    )
    independent = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "responsibilities": [
                    {
                        "responsibility": "Summarize reports with Example Provider.",
                        "provider_dependency": {
                            "provider_id": "Example Provider",
                            "intrinsic": False,
                        },
                    }
                ]
            }
        },
    )[0]
    assert "Example Provider" not in independent["responsibility"]
    assert independent["provider_dependency"] == {"kind": "abstract", "intrinsic": False}

    intrinsic = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "responsibilities": [
                    {
                        "responsibility": "Use Example Provider's native moderation boundary.",
                        "provider_dependency": {
                            "provider_id": "Example Provider",
                            "kind": "intrinsic",
                            "reason": "The native boundary is required by the Source.",
                        },
                    }
                ]
            }
        },
    )[0]
    assert intrinsic["provider_dependency"]["kind"] == "intrinsic"
    assert intrinsic["provider_dependency"]["provider_id"] == "Example Provider"
    assert intrinsic["provider_dependency"]["intrinsic_provider_responsibility"]["evidence_refs"]
    assert "Example Provider" in intrinsic["responsibility"]


def test_effects_and_failure_recovery_are_structured_and_portable() -> None:
    source = _source(
        claims=(),
        declarations={
            "network_effects": [{"name": "remote lookup", "kind": "declared"}],
            "write_effects": [{"name": "candidate cache", "target": "build/cache"}],
        },
    )
    record = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "responsibilities": [
                    {
                        "responsibility": "Collect bounded records.",
                        "side_effects": ["writes a bounded cache"],
                        "failure_recovery": {
                            "failure_modes": ["remote lookup fails"],
                            "recovery": "Retain the prior cache and mark the finding incomplete.",
                        },
                    }
                ]
            }
        },
    )[0]
    assert record["side_effects"] == ["writes a bounded cache"]
    assert record["failure_recovery"]["failure_modes"] == ["remote lookup fails"]
    assert "prior cache" in record["failure_recovery"]["recovery"]
    assert ("/" + "Users" + "/") not in json.dumps(record)
    assert validate_responsibility_record(record, source=source) == record


def test_research_only_source_produces_findings_without_candidate_copy() -> None:
    source = _source(
        licence={"status": "missing"},
        constraints={"content_reuse": "metadata-only"},
        claims=(),
    )
    private_document = {
        "frontmatter": {
            "responsibilities": ["Extract public findings."],
        },
        "text": "A long document body that must not be copied into durable evidence.",
    }

    records = extract_responsibilities(source, private_document)

    assert len(records) == 1
    assert records[0]["candidate_contribution_allowed"] is False
    assert records[0]["extraction"]["candidate_contribution_allowed"] is False
    assert records[0]["extraction"]["content_reused"] is False
    assert "long document body" not in json.dumps(records)
    assert validate_responsibility_record(records[0], source=source) == records[0]


def test_no_finding_does_not_infer_a_responsibility_from_tool_metadata() -> None:
    source = _source(
        claims=(),
        declarations={
            "tools": [{"name": "python", "kind": "runtime"}],
            "dependencies": [{"name": "jsonschema", "version": ">=4"}],
            "provider_hints": [{"name": "Example Provider"}],
        },
    )

    assert extract_responsibilities(source) == []
    assert extract_responsibility_evidence(source)["finding"] == "no-finding"


def test_legacy_action_category_and_dot_group_hints_are_rejected() -> None:
    source = _source()
    old_action = {**source, "action": {"responsibility": "Do not seed this."}}
    with pytest.raises(ExtractionValidationError, match="legacy Action"):
        extract_responsibilities(old_action)

    with pytest.raises(ExtractionValidationError, match="legacy Action"):
        extract_responsibilities(source, {"frontmatter": {"dot_group": "legacy"}})


def test_signature_is_stable_and_abstracts_provider_identity() -> None:
    first = deterministic_responsibility_signature(
        "Summarize reports with Provider A.", provider_names=["Provider A"]
    )
    second = deterministic_responsibility_signature(
        "Summarize reports with Provider B.", provider_names=["Provider B"]
    )
    assert first == second
    assert first == deterministic_responsibility_signature(
        "  SUMMARIZE   reports with Provider A! ", provider_names=["Provider A"]
    )


def test_assisted_semantics_require_matching_source_references_and_never_authorise() -> None:
    source = _source(claims=())
    source_id = source["source_id"]
    record = extract_responsibilities(
        source,
        semantic_fields={
            "responsibility": "Review supplied findings.",
            "source_refs": [source_id],
            "verification": {"status": "unverified"},
        },
    )[0]
    assert record["verification"]["status"] == "unverified"
    assert "execution_authority" not in json.dumps(record)
    with pytest.raises(ExtractionValidationError, match="matching Source refs"):
        extract_responsibilities(
            source,
            semantic_fields={
                "responsibility": "Review supplied findings.",
                "source_refs": ["source-unknown"],
            },
        )


def test_no_network_or_source_execution_api_is_exposed() -> None:
    import fractal.capability_extraction as extraction_module

    forbidden = {
        "execute_source",
        "invoke_source",
        "resolve_source",
        "retrieve_source",
        "scrape_source",
        "create_capability_dot",
        "write_responsibility_registry",
    }
    assert forbidden.isdisjoint(vars(extraction_module))


def test_compact_evidence_has_references_and_hashes_not_raw_document() -> None:
    source = _source(claims=())
    body = (
        "This paragraph is intentionally long and private and should never be retained verbatim. "
        * 8
    )
    records = extract_responsibilities(
        source,
        {
            "frontmatter": {"responsibilities": ["Parse supplied documents."]},
            "text": body,
        },
    )
    encoded = json.dumps(records)
    assert body not in encoded
    assert len(encoded) < len(body) * 5
    assert re.fullmatch(r"[a-f0-9]{64}", records[0]["evidence_digest"])


def test_skill_description_is_inspected_as_responsibility_not_as_routing_suffix() -> None:
    source = _source(claims=())
    records = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "name": "document-search",
                "description": (
                    "Search supplied documents for grounded answers. "
                    "Use this skill when a user asks about retained knowledge."
                ),
            },
            "text": "## Verification\nConfirm every answer has an evidence reference.",
        },
    )

    assert [item["responsibility"] for item in records] == [
        "Search supplied documents for grounded answers."
    ]
    assert records[0]["verification"]["status"] == "claimed"
    assert records[0]["verification"]["claims"] == [
        "Confirm every answer has an evidence reference."
    ]


def test_description_trigger_fragments_never_become_responsibilities() -> None:
    source = _source(claims=())
    records = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "description": (
                    "Translate supplied text while preserving meaning. "
                    "Triggers: translator, text translation, localize."
                )
            }
        },
    )

    assert [item["responsibility"] for item in records] == [
        "Translate supplied text while preserving meaning."
    ]


def test_noun_description_gets_a_plain_verb_and_malformed_text_uses_name_fallback() -> None:
    source = _source(claims=())
    noun = extract_responsibilities(
        source,
        {"frontmatter": {"description": "Reusable package verification patterns."}},
    )[0]
    fallback = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "name": "social-campaign-planning",
                "description": "Plan, create,.",
            }
        },
    )[0]

    assert noun["responsibility"] == "Use Reusable package verification patterns."
    assert fallback["responsibility"] == "Perform social campaign planning."


def test_language_tokens_keep_semantic_hash_characters() -> None:
    source = _source(claims=())
    record = extract_responsibilities(
        source,
        {"frontmatter": {"description": "Master C# and .NET debugging patterns."}},
    )[0]

    assert record["responsibility"] == "Master C# and .NET debugging patterns."


def test_provider_context_is_abstracted_but_direct_provider_semantics_are_intrinsic() -> None:
    source = _source(claims=())
    abstract = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "description": (
                    "Build image analysis applications with Azure AI Vision SDK for Java."
                )
            }
        },
    )[0]
    intrinsic = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "description": "Azure Event Grid SDK for .NET."
            }
        },
    )[0]

    assert abstract["responsibility"] == "Build image analysis applications."
    assert abstract["provider_dependency"] == {"kind": "abstract", "intrinsic": False}
    assert intrinsic["responsibility"] == "Use Azure Event Grid."
    assert intrinsic["provider_dependency"]["kind"] == "intrinsic"
    assert intrinsic["provider_dependency"]["provider_id"] == "azure"


def test_provider_language_slashes_are_removed_and_direct_google_semantics_are_intrinsic() -> None:
    source = _source(claims=())
    azure = extract_responsibilities(
        source,
        {
            "frontmatter": {
                "description": "Azure Blob Storage JavaScript/TypeScript SDK for uploads."
            }
        },
    )[0]
    google = extract_responsibilities(
        source,
        {"frontmatter": {"description": "Join a Google Meet call and transcribe captions."}},
    )[0]

    assert azure["responsibility"] == "Use Azure Blob Storage."
    assert "/" not in azure["responsibility"]
    assert google["responsibility"] == "Join a Google Meet call and transcribe captions."
    assert google["provider_dependency"]["provider_id"] == "google"


def test_trigger_only_description_is_an_explicit_no_finding() -> None:
    source = _source(claims=())
    evidence = extract_responsibility_evidence(
        source,
        {"frontmatter": {"description": "Use this skill when deployment fails."}},
    )

    assert evidence["finding"] == "no-finding"
    assert evidence["responsibilities"] == []


def test_trigger_only_skill_uses_name_as_bounded_fallback() -> None:
    source = _source(claims=())
    evidence = extract_responsibility_evidence(
        source,
        {
            "frontmatter": {
                "name": "systematic-debugging",
                "description": "Use this skill when a test fails.",
            }
        },
    )

    assert evidence["finding"] == "finding"
    assert evidence["responsibilities"][0]["responsibility"] == (
        "Perform systematic debugging."
    )
    assert evidence["responsibilities"][0]["extraction"]["origin"] == (
        "skill-name-fallback"
    )


def test_validation_rejects_tampered_digest_and_raw_document() -> None:
    source = _source()
    record = extract_responsibilities(source)[0]
    tampered = copy.deepcopy(record)
    tampered["evidence_digest"] = "0" * 64
    tampered["extraction"]["evidence_digest"] = "0" * 64
    with pytest.raises(ExtractionValidationError, match="evidence_digest"):
        validate_responsibility_record(tampered)

    leaked = copy.deepcopy(record)
    leaked["raw_document"] = "full document"
    with pytest.raises(ExtractionValidationError, match="raw document"):
        validate_responsibility_record(leaked)
