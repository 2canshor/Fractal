from __future__ import annotations

import json
from pathlib import Path

from fractal.cli import main


def storage_arguments(tmp_path: Path) -> list[str]:
    return [
        "--project-root",
        str(tmp_path / "projects"),
        "--runtime-root",
        str(tmp_path / "runtime"),
    ]


def test_cli_create_show_and_verify_real_project(tmp_path: Path, capsys) -> None:
    storage = storage_arguments(tmp_path)
    assert (
        main(
            [
                "project",
                "create",
                *storage,
                "--project-id",
                "cli-project",
                "--title",
                "CLI Project",
                "--platform",
                "test-adapter",
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["project_id"] == "cli-project"

    assert main(["project", "show", *storage, "--project-id", "cli-project"]) == 0
    summary = capsys.readouterr().out
    assert "# CLI Project" in summary
    assert "Record Revision: `0`" in summary

    assert main(["project", "verify", *storage, "--project-id", "cli-project"]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["event_chain_valid"] is True
    assert verification["event_count"] == 1


def test_cli_component_status_route(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "record_type": "component-registry",
                "record_version": 2,
                "system_version": "0.1.0-alpha.2",
                "candidate_status": "candidate",
                "components": [],
            }
        )
    )
    assert main(["components", "show", "--registry", str(registry)]) == 0
    output = capsys.readouterr().out
    assert "# Fractal Component Status" in output
    assert "Version State: `candidate`" in output
