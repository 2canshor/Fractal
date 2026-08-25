from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.cli import main
from fractal.context import (
    ContextSourceResolver,
    RetrievalRequest,
    assemble_context_package,
    migrate_context_catalogue,
    rebuild_context_index,
    resolve_context_source,
)


def write_catalogue(path: Path, guides: Path, profile: Path) -> None:
    value = {
        "record_type": "context-catalogue",
        "record_version": 1,
        "sources": [
            {
                "root_id": "guides",
                "path": str(guides),
                "source_type": "guide",
                "sensitivity": "private",
                "instruction_authority": "reference_only",
                "personalisation": False,
                "topics": ["architecture", "context"],
                "applicability": {"task_types": [], "keywords": [], "project_ids": []},
                "include_suffixes": [".txt"],
            },
            {
                "root_id": "profile",
                "path": str(profile),
                "source_type": "canonical_record",
                "sensitivity": "private",
                "instruction_authority": "canonical_state",
                "personalisation": True,
                "topics": ["preference"],
                "applicability": {
                    "task_types": ["personalisation"],
                    "keywords": ["favourite", "preference"],
                    "project_ids": [],
                },
                "include_suffixes": [".json"],
            },
        ],
    }
    path.write_text(json.dumps(value))


def test_retrieval_is_bounded_auditable_and_not_automatically_instructional(
    tmp_path: Path,
) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "context.txt").write_text(
        "Title: Progressive context\nLoad only relevant reference material for the task."
    )
    profile = tmp_path / "profile.json"
    profile.write_text('{"favourite_colour":"cobalt"}')
    catalogue = tmp_path / "catalogue.json"
    database = tmp_path / "runtime" / "context.sqlite"
    manifest = tmp_path / "runtime" / "manifests" / "request.json"
    write_catalogue(catalogue, guides, profile)

    report = rebuild_context_index(catalogue, database)
    assert report["indexed"] == 2
    package = assemble_context_package(
        database,
        RetrievalRequest(
            query="progressive context",
            purpose="Choose a retrieval architecture",
            requester="test-agent",
            task_type="architecture",
            max_items=1,
        ),
        manifest_path=manifest,
    )
    assert len(package["matches"]) == 1
    assert package["matches"][0]["source_id"] == "guides:context.txt"
    assert package["matches"][0]["instruction_effect"] is False
    assert package["matches"][0]["source_sha256"]
    assert json.loads(manifest.read_text())["manifest_sha256"] == package["manifest_sha256"]


def test_personalisation_requires_explicit_and_relevant_request(tmp_path: Path) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "other.txt").write_text("Title: Architecture\nDatabase architecture notes")
    profile = tmp_path / "profile.json"
    profile.write_text('{"favourite_colour":"cobalt"}')
    catalogue = tmp_path / "catalogue.json"
    database = tmp_path / "context.sqlite"
    write_catalogue(catalogue, guides, profile)
    rebuild_context_index(catalogue, database)

    excluded = assemble_context_package(
        database,
        RetrievalRequest(
            query="favourite cobalt",
            purpose="Test default exclusion",
            requester="test-agent",
            task_type="personalisation",
            allow_personalisation=False,
        ),
    )
    assert excluded["no_results"] is True

    irrelevant = assemble_context_package(
        database,
        RetrievalRequest(
            query="database architecture",
            purpose="Test relevance filter",
            requester="test-agent",
            task_type="architecture",
            allow_personalisation=True,
        ),
    )
    assert all(match["source_id"] != "profile:profile.json" for match in irrelevant["matches"])

    included = assemble_context_package(
        database,
        RetrievalRequest(
            query="favourite cobalt",
            purpose="Answer an explicit preference question",
            requester="test-agent",
            task_type="personalisation",
            allow_personalisation=True,
        ),
    )
    assert included["matches"][0]["source_id"] == "profile:profile.json"
    assert included["matches"][0]["instruction_effect"] is False


def test_index_can_be_deleted_and_rebuilt_with_skipped_file_evidence(tmp_path: Path) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "small.txt").write_text("rebuildable context")
    (guides / "large.txt").write_text("x" * 100)
    derived = guides / "graphify-out" / "cache"
    derived.mkdir(parents=True)
    (derived / "derived.txt").write_text("derived duplicate context")
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    catalogue = tmp_path / "catalogue.json"
    database = tmp_path / "context.sqlite"
    write_catalogue(catalogue, guides, profile)

    first = rebuild_context_index(catalogue, database, maximum_file_bytes=50)
    assert first["indexed"] == 2
    assert first["skipped"] == [{"locator": "large.txt", "reason": "maximum-file-bytes"}]
    database.unlink()
    second = rebuild_context_index(catalogue, database, maximum_file_bytes=50)
    assert second["indexed"] == 2
    assert database.exists()


def test_context_cli_rebuilds_and_searches(tmp_path: Path, capsys) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "retrieval.txt").write_text("Title: Retrieval\nBounded retrieval package")
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    catalogue = tmp_path / "catalogue.json"
    database = tmp_path / "context.sqlite"
    write_catalogue(catalogue, guides, profile)
    assert (
        main(
            [
                "context",
                "rebuild",
                "--catalogue",
                str(catalogue),
                "--database",
                str(database),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["indexed"] == 2
    assert (
        main(
            [
                "context",
                "search",
                "--database",
                str(database),
                "--query",
                "bounded retrieval",
                "--purpose",
                "CLI smoke test",
                "--task-type",
                "architecture",
                "--max-items",
                "1",
            ]
        )
        == 0
    )
    package = json.loads(capsys.readouterr().out)
    assert package["matches"][0]["source_id"] == "guides:retrieval.txt"


def test_context_cli_requires_explicit_logical_root_mapping(tmp_path: Path, capsys) -> None:
    local = tmp_path / "local"
    local.mkdir()
    (local / "source.txt").write_text("Title: Explicit mapping\nLogical context")
    catalogue = tmp_path / "logical-catalogue.json"
    catalogue.write_text(
        json.dumps(
            {
                "record_type": "context-catalogue",
                "record_version": 1,
                "sources": [
                    {
                        "root_id": "local-source",
                        "locator": "local://",
                        "source_type": "guide",
                        "sensitivity": "public",
                        "instruction_authority": "reference_only",
                        "personalisation": False,
                        "topics": ["logical"],
                        "applicability": {
                            "task_types": [],
                            "keywords": [],
                            "project_ids": [],
                        },
                        "include_suffixes": [".txt"],
                    }
                ],
            }
        )
    )
    database = tmp_path / "logical.sqlite"
    database.write_bytes(b"existing-index")

    assert main(
        [
            "context",
            "rebuild",
            "--catalogue",
            str(catalogue),
            "--database",
            str(database),
        ]
    ) == 2
    assert "No local resolution root configured for local://" in capsys.readouterr().out
    assert database.read_bytes() == b"existing-index"

    assert main(
        [
            "context",
            "rebuild",
            "--catalogue",
            str(catalogue),
            "--database",
            str(database),
            "--context-root",
            f"local={local}",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["indexed"] == 1


def test_named_local_context_root_resolves_nested_logical_locator(tmp_path: Path, capsys) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "reference.txt").write_text("Title: Named root\nPortable source")
    catalogue = tmp_path / "named-catalogue.json"
    catalogue.write_text(
        json.dumps(
            {
                "record_type": "context-catalogue",
                "record_version": 1,
                "sources": [
                    {
                        "root_id": "guides",
                        "locator": "local://guides",
                        "source_type": "guide",
                        "sensitivity": "public",
                        "instruction_authority": "reference_only",
                        "personalisation": False,
                        "topics": ["named"],
                        "applicability": {
                            "task_types": [],
                            "keywords": [],
                            "project_ids": [],
                        },
                        "include_suffixes": [".txt"],
                    }
                ],
            }
        )
    )
    database = tmp_path / "named.sqlite"
    assert main(
        [
            "context",
            "rebuild",
            "--catalogue",
            str(catalogue),
            "--database",
            str(database),
            "--context-root",
            f"guides={guides}",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["indexed"] == 1


def test_logical_source_locators_resolve_from_one_explicit_root_set(
    tmp_path: Path,
) -> None:
    workplace = tmp_path / "workplace"
    system = tmp_path / "system"
    local = tmp_path / "local"
    for root in (workplace, system, local):
        root.mkdir()
    (workplace / "docs").mkdir()
    (workplace / "docs" / "portable.txt").write_text("portable context")
    (system / "policy.json").write_text("{}")
    (local / "runtime.txt").write_text("derived state")

    resolver = ContextSourceResolver.from_roots(
        {"workplace": workplace, "system": system, "local": local}
    )
    assert resolver.resolve("workplace://docs") == workplace / "docs"
    assert resolve_context_source("system://policy.json", resolver.roots) == (
        system / "policy.json"
    )
    assert resolver.resolve("local://runtime.txt") == local / "runtime.txt"
    assert resolver.resolve("local://local/runtime.txt") == local / "runtime.txt"
    with pytest.raises(ValueError, match="parent traversal"):
        resolver.resolve("workplace://../outside")
    with pytest.raises(ValueError, match="No local resolution root"):
        resolve_context_source("system://policy.json", {"workplace": workplace})


def test_legacy_catalogue_migration_is_portable_and_idempotent(tmp_path: Path) -> None:
    workplace = tmp_path / "workplace"
    guides = workplace / "guides"
    guides.mkdir(parents=True)
    (guides / "portable.txt").write_text("portable context")
    legacy = workplace / "memory" / "catalogue" / "context-catalogue.json"
    legacy.parent.mkdir(parents=True)
    write_catalogue(legacy, guides, workplace / "profile.json")
    (workplace / "profile.json").write_text("{}")

    migrated = migrate_context_catalogue(legacy, roots={"workplace": workplace})
    target = workplace / "context" / "sources.json"
    serialized = target.read_text(encoding="utf-8")
    assert target.exists()
    assert "locator" in migrated["sources"][0]
    assert "path" not in migrated["sources"][0]
    assert str(workplace) not in serialized
    assert migrated == migrate_context_catalogue(legacy, roots={"workplace": workplace})
    assert legacy.exists()


def test_legacy_external_context_migration_uses_named_local_locator(tmp_path: Path) -> None:
    guides = tmp_path / "guides"
    guides.mkdir()
    source = guides / "portable.txt"
    source.write_text("Title: External guide\nMapped explicitly")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "record_type": "context-catalogue",
                "record_version": 1,
                "sources": [
                    {
                        "root_id": "guides",
                        "path": str(guides),
                        "source_type": "guide",
                        "sensitivity": "public",
                        "instruction_authority": "reference_only",
                        "personalisation": False,
                        "topics": ["external"],
                        "applicability": {
                            "task_types": [],
                            "keywords": [],
                            "project_ids": [],
                        },
                        "include_suffixes": [".txt"],
                    }
                ],
            }
        )
    )

    migrated = migrate_context_catalogue(legacy, roots={"guides": guides})
    assert migrated["sources"][0]["locator"] == "local://guides"
