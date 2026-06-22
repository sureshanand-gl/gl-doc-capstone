import json
from pathlib import Path
from typing import Any

from llmops.local_extraction import INVOICE_FIELD_KEYS


def load_golden_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def compute_field_accuracy(
    expected_fields: dict[str, Any],
    predicted_fields: dict[str, Any],
) -> dict[str, Any]:
    compared_keys = [key for key in INVOICE_FIELD_KEYS if key in expected_fields]
    matched = 0
    missing_fields: list[str] = []

    for key in compared_keys:
        if expected_fields.get(key) == predicted_fields.get(key):
            matched += 1
        else:
            missing_fields.append(key)

    total = len(compared_keys)
    accuracy = round(matched / total, 4) if total else 0.0
    return {
        "matched_fields": matched,
        "total_fields": total,
        "field_accuracy": accuracy,
        "missing_fields": missing_fields,
    }


def build_eval_report(
    rows: list[dict[str, Any]],
    prompt_version: str,
    schema_version: str,
    min_field_accuracy: float,
) -> dict[str, Any]:
    average_accuracy = round(
        sum(row["metrics"]["field_accuracy"] for row in rows) / len(rows),
        4,
    ) if rows else 0.0
    return {
        "documents": len(rows),
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "minimum_field_accuracy": min_field_accuracy,
        "average_field_accuracy": average_accuracy,
        "meets_threshold": average_accuracy >= min_field_accuracy,
        "results": [
            {
                "document_id": row["document_id"],
                "source_name": row["source_name"],
                "field_accuracy": row["metrics"]["field_accuracy"],
                "missing_fields": row["metrics"]["missing_fields"],
            }
            for row in rows
        ],
    }
