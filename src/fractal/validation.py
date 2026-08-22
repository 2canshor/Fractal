"""JSON Schema validation for canonical Fractal records."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


@lru_cache(maxsize=1)
def project_validator() -> Draft202012Validator:
    """Load and compile the packaged Project-record schema."""
    schema_path = files("fractal.schemas").joinpath("project-record.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_project_record(record: dict[str, Any]) -> None:
    """Raise a validation error when a Project record is not canonical."""
    project_validator().validate(record)
