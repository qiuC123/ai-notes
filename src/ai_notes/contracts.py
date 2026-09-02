from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


QUEUE_SCHEMA = "learning-queue.v1"
DECISIONS_SCHEMA = "learning-decisions.v1"
MANIFEST_SCHEMA = "learning-run-manifest.v1"


class ContractValidationError(ValueError):
    pass


def load_schema(schema_name: str) -> dict[str, Any]:
    resource = files("ai_notes.schemas").joinpath(f"{schema_name}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_contract(schema_name: str, payload: object) -> dict[str, Any]:
    schema = load_schema(schema_name)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractValidationError(f"{schema_name} validation failed at {location}: {error.message}")
    if not isinstance(payload, dict):  # The schemas require this; keep the return type explicit.
        raise ContractValidationError(f"{schema_name} must be a JSON object")
    return payload
