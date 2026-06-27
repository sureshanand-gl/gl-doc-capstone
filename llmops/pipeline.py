"""Live evaluation pipeline for running provider calls against golden invoice examples."""

from pathlib import Path
from typing import Any

from llmops.live_provider import LiveExtractionResult, OpenAICompatibleExtractor
from llmops.metrics import compute_field_accuracy, load_golden_dataset
from llmops.registry import load_prompt_registry


def _zero_accuracy_metrics(
    expected_fields: dict[str, Any],
    predicted_fields: dict[str, Any],
) -> dict[str, Any]:
    metrics = compute_field_accuracy(expected_fields, predicted_fields)
    metrics["matched_fields"] = 0
    metrics["scalar_matched_fields"] = 0
    metrics["order_item_matched_fields"] = 0
    metrics["field_accuracy"] = 0.0
    metrics["scalar_field_accuracy"] = 0.0
    metrics["order_item_field_accuracy"] = 0.0
    return metrics


def build_live_eval_row(
    dataset_row: dict[str, Any],
    provider_result: LiveExtractionResult,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any]:
    if provider_result.validation_status == "valid":
        metrics = compute_field_accuracy(dataset_row["expected_fields"], provider_result.fields)
    else:
        metrics = _zero_accuracy_metrics(dataset_row["expected_fields"], provider_result.fields)

    return {
        "document_id": dataset_row["document_id"],
        "source_name": dataset_row["source_name"],
        "provider": provider_result.provider,
        "model": provider_result.model,
        "surface": "live_pipeline",
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "latency_ms": provider_result.latency_ms,
        "validation_status": provider_result.validation_status,
        "validation_errors": provider_result.validation_errors,
        "field_accuracy": metrics["field_accuracy"],
        "scalar_field_accuracy": metrics["scalar_field_accuracy"],
        "order_item_field_accuracy": metrics["order_item_field_accuracy"],
        "matched_fields": metrics["matched_fields"],
        "total_fields": metrics["total_fields"],
        "missing_fields": metrics["missing_fields"],
        "fallback_reason": provider_result.fallback_reason,
        "prompt_tokens": (
            provider_result.usage["prompt_tokens"] if provider_result.usage is not None else None
        ),
        "completion_tokens": (
            provider_result.usage["completion_tokens"] if provider_result.usage is not None else None
        ),
        "total_tokens": (
            provider_result.usage["total_tokens"] if provider_result.usage is not None else None
        ),
        "cost_usd": provider_result.cost_usd,
        "usage_source": provider_result.usage_source,
        "fields": provider_result.fields,
    }


def run_live_golden_eval(
    repo_root: Path,
    dataset_path: Path,
    api_key: str,
    api_base: str,
    model: str,
    prompt_version: str,
) -> tuple[list[dict[str, Any]], str]:
    registry = load_prompt_registry(repo_root)
    prompt_entry = registry.get_invoice_entry(prompt_version)
    prompt = prompt_entry.prompt_path.read_text(encoding="utf-8")
    extractor = OpenAICompatibleExtractor.from_credentials(
        api_key=api_key,
        api_base=api_base,
        model=model,
        prompt=prompt,
        schema_path=prompt_entry.schema_path,
        pricing_path=repo_root / "configs" / "model_pricing.yaml",
    )

    rows = []
    for dataset_row in load_golden_dataset(dataset_path):
        provider_result = extractor.extract(dataset_row["ocr_text"])
        rows.append(
            build_live_eval_row(
                dataset_row=dataset_row,
                provider_result=provider_result,
                prompt_version=prompt_entry.prompt_version,
                schema_version=prompt_entry.schema_version,
            )
        )

    return rows, prompt_entry.schema_version
