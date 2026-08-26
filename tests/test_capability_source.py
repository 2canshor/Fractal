"""Focused contract tests for raw capability Source intake."""

from __future__ import annotations

import copy
import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator

from fractal import capability_source as source_module
from fractal.capability_source import (
    SourceLicenceError,
    SourceStorageError,
    SourceValidationError,
    build_source,
    can_reuse_source_content,
    deterministic_provenance_id,
    deterministic_source_id,
    empty_source_catalogue,
    intake_source,
    load_source_catalogue,
    merge_source_catalogue,
    merge_source_records,
    validate_source,
    validate_source_catalogue,
    write_source_catalogue,
)

COMMIT = "a" * 40
CONTENT = "b" * 64


def _source(
    source_type: str = "library",
    *,
    path: str | None = "src/feature.py",
    retrieved_at: str = "2026-08-25T00:00:00Z",
    capabilities: tuple[str, ...] = ("bounded-feature",),
    licence: dict[str, object] | None = None,
    constraints: dict[str, object] | None = None,
):
    return build_source(
        name=f"Example {source_type}",
        source_type=source_type,
        donor_id="example-donor",
        locator="https://example.invalid/example",
        commit=COMMIT,
        tag="v1.2.3",
        version="1.2.3",
        path=path,
        content_sha256=CONTENT,
        licence=licence
        or {
            "status": "verified",
            "spdx": "MIT",
            "evidence": "LICENSE",
        },
        constraints=constraints
        or {
            "content_reuse": "candidate-copy",
            "notes": ["No upstream runtime dependency"],
        },
        retrieved_at=retrieved_at,
        claimed_capabilities=capabilities,
        declarations={
            "tools": [{"name": "python", "kind": "runtime"}],
            "scripts": [{"name": "check", "path": "scripts/check.py"}],
            "network_effects": [{"name": "none", "kind": "declared"}],
            "write_effects": [{"name": "candidate-output", "target": "build/"}],
            "dependencies": [{"name": "python", "version": ">=3.12"}],
            "provider_hints": [{"name": "local-runtime", "confidence": "observed"}],
            "compatibility": {"platforms": ["macos"], "python": ">=3.12"},
        },
    )


@pytest.mark.parametrize("source_type", ["library", "skill", "spec"])
def test_library_skill_and_spec_records_are_valid(source_type: str) -> None:
    record = _source(source_type)

    assert validate_source(record) == record
    assert record["status"] == "source-only"
    assert record["source_only"] == {
        "callable": False,
        "resolvable": False,
        "active": False,
        "execution_authority": False,
        "persistence_authority": False,
    }
    assert len(record["claimed_capabilities"]) == 1


def test_packaged_schema_accepts_a_valid_source() -> None:
    schema = json.loads(
        files("fractal.schemas")
        .joinpath("capability-source.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_source())


@pytest.mark.parametrize("status", ["unclear", "missing", "incompatible"])
def test_unclear_licence_is_research_metadata_only(status: str) -> None:
    record = _source(
        licence={"status": status},
        constraints={"content_reuse": "metadata-only"},
    )

    assert record["licence"]["candidate_content_allowed"] is False
    assert record["constraints"]["content_reuse"] == "metadata-only"
    assert can_reuse_source_content(record) is False
    with pytest.raises(SourceLicenceError, match="metadata only"):
        source_module.require_candidate_content_reuse(record)


def test_unverified_licence_cannot_claim_candidate_copy() -> None:
    with pytest.raises(SourceLicenceError, match="Candidate copy"):
        _source(
            licence={"status": "unknown"},
            constraints={"content_reuse": "candidate-copy"},
        )


def test_exact_commit_and_hashes_are_checked() -> None:
    with pytest.raises(SourceValidationError, match="exact 40"):
        build_source(
            name="Bad commit",
            source_type="library",
            donor_id="example-donor",
            locator="https://example.invalid/example",
            commit="short",
            version="1.0.0",
            content_sha256=CONTENT,
        )
    with pytest.raises(SourceValidationError, match="SHA-256"):
        build_source(
            name="Bad hash",
            source_type="library",
            donor_id="example-donor",
            locator="https://example.invalid/example",
            commit=COMMIT,
            version="1.0.0",
            content_sha256="not-a-hash",
        )


def test_tree_and_file_hashes_are_alternative_snapshot_hashes() -> None:
    tree_record = _source(path=None)
    tree_record["upstream"]["content_sha256"] = None
    tree_record["upstream"]["tree_sha256"] = CONTENT
    tree_record["provenance"][0]["content_sha256"] = None
    tree_record["provenance"][0]["tree_sha256"] = CONTENT
    tree_record["provenance"][0]["provenance_id"] = deterministic_provenance_id(
        tree_record["provenance"][0]
    )
    tree_record["source_id"] = deterministic_source_id(tree_record)
    assert validate_source(tree_record)["upstream"]["tree_sha256"] == CONTENT

    invalid = copy.deepcopy(tree_record)
    invalid["upstream"]["file_sha256"] = CONTENT
    with pytest.raises(SourceValidationError, match="tree hash or file hash"):
        validate_source(invalid)


def test_source_file_path_does_not_create_a_dot_boundary() -> None:
    first = _source(path="skills/one/SKILL.md", capabilities=("one",))
    second = _source(path="skills/two/SKILL.md", capabilities=("two",))

    assert first["source_id"] == second["source_id"]
    merged = merge_source_records(first, second)
    assert merged["source_id"] == first["source_id"]
    assert merged["claimed_capabilities"] == ["one", "two"]
    assert {item["path"] for item in merged["provenance"]} == {
        "skills/one/SKILL.md",
        "skills/two/SKILL.md",
    }


def test_duplicate_provenance_merges_without_duplicate_source() -> None:
    first = _source(retrieved_at="2026-08-25T00:00:00Z")
    second = _source(retrieved_at="2026-08-25T01:00:00Z")
    catalogue = merge_source_catalogue(
        merge_source_catalogue(empty_source_catalogue(), first), second
    )

    assert len(catalogue["sources"]) == 1
    assert len(catalogue["sources"][0]["provenance"]) == 1
    assert catalogue["sources"][0]["retrieved_at"] == "2026-08-25T00:00:00Z"


def test_source_only_non_callability_is_fail_closed() -> None:
    invalid = copy.deepcopy(_source())
    invalid["source_only"]["callable"] = True
    with pytest.raises(SourceValidationError, match="callable"):
        validate_source(invalid)

    invalid = copy.deepcopy(_source())
    invalid["status"] = "active"
    with pytest.raises(SourceValidationError, match="exactly source-only"):
        validate_source(invalid)


def test_portable_paths_and_secret_fields_are_rejected() -> None:
    with pytest.raises(SourceValidationError, match="portable relative path"):
        _source(path="/" + "Users" + "/private/skill.md")

    invalid = copy.deepcopy(_source())
    invalid["declarations"]["provider_hints"] = [{"name": "provider", "api_key": "do-not-persist"}]
    with pytest.raises(SourceValidationError, match="Unknown declarations|secret field"):
        validate_source(invalid)


def test_old_action_fixture_has_no_intake_effect() -> None:
    old_action = {
        "record_type": "action",
        "record_version": 1,
        "action_id": "legacy-action",
        "name": "Legacy action",
    }
    catalogue = empty_source_catalogue()
    with pytest.raises(SourceValidationError):
        intake_source(old_action, operation="genesis", catalogue=catalogue)
    assert catalogue["sources"] == []


def test_intake_requires_explicit_genesis_or_evolution_marker() -> None:
    with pytest.raises(SourceValidationError, match="explicit genesis or evolution"):
        intake_source(_source())
    assert intake_source(_source(), operation="genesis")["status"] == "source-only"
    assert intake_source(_source(), mode="evolution")["status"] == "source-only"


def test_catalogue_write_is_atomic_read_back_safe_and_root_confined(tmp_path) -> None:
    catalogue = merge_source_catalogue(empty_source_catalogue(), _source())
    written = write_source_catalogue("genesis/sources/catalogue.json", catalogue, root=tmp_path)
    destination = tmp_path / "genesis/sources/catalogue.json"

    assert destination.is_file()
    assert load_source_catalogue(destination, root=tmp_path) == written
    assert validate_source_catalogue(written) == written
    with pytest.raises(SourceStorageError, match="escapes"):
        write_source_catalogue(tmp_path.parent / "outside.json", catalogue, root=tmp_path)


def test_no_execution_api_is_exposed() -> None:
    forbidden = {
        "execute_source",
        "invoke_source",
        "resolve_source",
        "retrieve_source",
        "scrape_source",
    }
    assert forbidden.isdisjoint(vars(source_module))
