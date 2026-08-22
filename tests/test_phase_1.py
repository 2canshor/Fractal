from __future__ import annotations

import json
import re
from pathlib import Path

import fractal
from fractal.cli import main

ROOT = Path(__file__).parents[1]


def test_system_version_is_initial_prerelease() -> None:
    assert fractal.SYSTEM_VERSION == "0.1.0-alpha.2"
    assert fractal.__version__ == "0.1.0a2"


def test_version_command_reports_system_version(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0-alpha.2"


def test_technical_decisions_are_machine_readable() -> None:
    record = json.loads((ROOT / "docs" / "technical-decisions.json").read_text())
    ids = [item["id"] for item in record["decisions"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_public_tree_has_no_known_private_markers() -> None:
    home_marker = "/" + "Users" + "/"
    private_key_marker = "PRIVATE" + " KEY"
    legacy_markers = [
        "AI" + " Records",
        "About" + " Me",
        "Cowork" + " Global",
    ]
    forbidden = [
        re.compile(re.escape(home_marker) + r"[^/\s]+"),
        re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?" + private_key_marker),
        *(re.compile(rf"\b{re.escape(marker)}\b", re.IGNORECASE) for marker in legacy_markers),
    ]
    checked_suffixes = {".md", ".json", ".toml", ".yml", ".yaml", ".py"}
    excluded_parts = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"}
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or excluded_parts.intersection(path.parts)
            or path.suffix not in checked_suffixes
        ):
            continue
        text = path.read_text(errors="replace")
        for pattern in forbidden:
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert findings == []
