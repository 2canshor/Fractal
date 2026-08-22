#!/usr/bin/env python3
"""Check mechanically decidable parts of a proposed name."""

from __future__ import annotations

import argparse
import json
import re


def check_name(kind: str, name: str) -> list[str]:
    findings: list[str] = []
    if not name.strip():
        return ["name-is-empty"]
    if kind == "boolean" and re.fullmatch(r"(?:is|has|can|should)[A-Z][A-Za-z0-9]*", name) is None:
        findings.append("boolean-must-read-as-a-camel-case-question")
    if kind == "class" and re.fullmatch(r"[A-Z][A-Za-z0-9]*", name) is None:
        findings.append("class-must-use-pascal-case")
    file_pattern = r"[a-z0-9]+(?:[-_][a-z0-9]+)*(?:\.[a-z0-9]+)?"
    if kind in {"file", "module"} and re.fullmatch(file_pattern, name) is None:
        findings.append("file-or-module-must-use-lowercase-technical-syntax")
    if kind == "running-state" and not name.lower().endswith("ing"):
        findings.append("running-state-must-use-a-present-participle")
    if kind == "technical-id" and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        findings.append("technical-id-must-use-kebab-case")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind",
        choices=["boolean", "class", "file", "module", "running-state", "technical-id"],
    )
    parser.add_argument("name")
    arguments = parser.parse_args()
    findings = check_name(arguments.kind, arguments.name)
    print(json.dumps({"name": arguments.name, "kind": arguments.kind, "findings": findings}))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
