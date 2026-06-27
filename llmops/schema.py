"""JSON-schema validation helpers for invoice extraction payloads."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_invoice_fields(
    fields: dict[str, Any],
    schema_path: Path | None = None,
) -> list[str]:
    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "invoice_v2.json"

    validator = Draft202012Validator(_load_schema(schema_path))
    return [error.message for error in validator.iter_errors(fields)]
