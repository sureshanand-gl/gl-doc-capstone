"""Golden-dataset loaders and accuracy scoring helpers for invoice extraction evals."""

import json
from pathlib import Path
from typing import Any

from llmops.local_extraction import ORDER_ITEM_FIELD_KEYS, SCALAR_FIELD_KEYS


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
    scalar_compared_keys = [key for key in SCALAR_FIELD_KEYS if key in expected_fields]
    matched = 0
    scalar_matched = 0
    order_item_matched = 0
    missing_fields: list[str] = []

    for key in scalar_compared_keys:
        if expected_fields.get(key) == predicted_fields.get(key):
            matched += 1
            scalar_matched += 1
        else:
            missing_fields.append(key)

    expected_items = (
        expected_fields.get("order_items")
        if isinstance(expected_fields.get("order_items"), list)
        else []
    )
    predicted_items = (
        predicted_fields.get("order_items")
        if isinstance(predicted_fields.get("order_items"), list)
        else []
    )
    order_item_total = 0
    for index, expected_item in enumerate(expected_items):
        if not isinstance(expected_item, dict):
            continue
        predicted_item = (
            predicted_items[index]
            if index < len(predicted_items) and isinstance(predicted_items[index], dict)
            else {}
        )
        for key in ORDER_ITEM_FIELD_KEYS:
            order_item_total += 1
            if expected_item.get(key) == predicted_item.get(key):
                matched += 1
                order_item_matched += 1
            else:
                missing_fields.append(f"order_items[{index}].{key}")

    scalar_total = len(scalar_compared_keys)
    total = scalar_total + order_item_total
    accuracy = round(matched / total, 4) if total else 0.0
    scalar_accuracy = round(scalar_matched / scalar_total, 4) if scalar_total else 0.0
    order_item_accuracy = (
        round(order_item_matched / order_item_total, 4) if order_item_total else 1.0
    )
    return {
        "matched_fields": matched,
        "total_fields": total,
        "field_accuracy": accuracy,
        "scalar_matched_fields": scalar_matched,
        "scalar_total_fields": scalar_total,
        "scalar_field_accuracy": scalar_accuracy,
        "order_item_matched_fields": order_item_matched,
        "order_item_total_fields": order_item_total,
        "order_item_field_accuracy": order_item_accuracy,
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
    average_scalar_accuracy = round(
        sum(row["metrics"]["scalar_field_accuracy"] for row in rows) / len(rows),
        4,
    ) if rows else 0.0
    average_order_item_accuracy = round(
        sum(row["metrics"]["order_item_field_accuracy"] for row in rows) / len(rows),
        4,
    ) if rows else 0.0
    return {
        "documents": len(rows),
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "minimum_field_accuracy": min_field_accuracy,
        "average_field_accuracy": average_accuracy,
        "average_scalar_field_accuracy": average_scalar_accuracy,
        "average_order_item_field_accuracy": average_order_item_accuracy,
        "meets_threshold": average_accuracy >= min_field_accuracy,
        "results": [
            {
                "document_id": row["document_id"],
                "source_name": row["source_name"],
                "field_accuracy": row["metrics"]["field_accuracy"],
                "scalar_field_accuracy": row["metrics"]["scalar_field_accuracy"],
                "order_item_field_accuracy": row["metrics"]["order_item_field_accuracy"],
                "missing_fields": row["metrics"]["missing_fields"],
            }
            for row in rows
        ],
    }
