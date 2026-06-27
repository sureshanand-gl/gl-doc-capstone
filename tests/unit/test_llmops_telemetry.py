"""Tests for telemetry normalization, pricing, and Prometheus publication helpers."""

from pathlib import Path

from llmops.telemetry import (
    build_pipeline_summary_metrics,
    calculate_usage_cost,
    load_model_pricing,
    normalize_usage,
    publish_pipeline_summary,
)


def test_normalize_usage_and_cost_from_pricing_file(tmp_path: Path):
    pricing_path = tmp_path / "model_pricing.yaml"
    pricing_path.write_text(
        """
models:
  gpt-4o-mini:
    input_cost_per_1k_tokens: 0.00015
    output_cost_per_1k_tokens: 0.0006
""".strip(),
        encoding="utf-8",
    )

    pricing = load_model_pricing(pricing_path)
    usage = normalize_usage(
        {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
        }
    )

    assert usage == {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
    }
    assert calculate_usage_cost("gpt-4o-mini", usage, pricing) == 0.00036
    assert calculate_usage_cost("missing-model", usage, pricing) is None
    assert normalize_usage(None) is None


def test_publish_pipeline_summary_pushes_expected_values(monkeypatch):
    pushed = {}

    def fake_push_to_gateway(address, job, registry):
        pushed["address"] = address
        pushed["job"] = job
        samples = {}
        for collector in registry.collect():
            for sample in collector.samples:
                if sample.name == collector.name:
                    samples[sample.name] = sample.value
        pushed["samples"] = samples

    monkeypatch.setattr("llmops.telemetry.push_to_gateway", fake_push_to_gateway)

    report = {
        "documents": 2,
        "invalid_documents": 1,
        "fallback_documents": 1,
        "average_latency_ms": 321.0,
        "average_field_accuracy": 0.75,
        "total_prompt_tokens": 100,
        "total_completion_tokens": 50,
        "total_cost_usd": 0.0123,
    }

    publish_pipeline_summary(
        pushgateway_url="http://pushgateway:9091",
        report=report,
        job="llmops_live_pipeline",
    )

    assert pushed["address"] == "http://pushgateway:9091"
    assert pushed["job"] == "llmops_live_pipeline"
    assert pushed["samples"] == {
        "llmops_pipeline_documents": 2,
        "llmops_pipeline_invalid_documents": 1,
        "llmops_pipeline_fallback_documents": 1,
        "llmops_pipeline_total_prompt_tokens": 100,
        "llmops_pipeline_total_completion_tokens": 50,
        "llmops_pipeline_total_cost_usd": 0.0123,
        "llmops_pipeline_average_latency_ms": 321.0,
        "llmops_pipeline_average_accuracy": 0.75,
        "llmops_pipeline_last_success_unixtime": pushed["samples"][
            "llmops_pipeline_last_success_unixtime"
        ],
    }
    assert pushed["samples"]["llmops_pipeline_last_success_unixtime"] > 0


def test_build_pipeline_summary_metrics_rolls_up_usage_and_cost():
    rows = [
        {
            "latency_ms": 100.0,
            "validation_status": "valid",
            "fallback_reason": None,
            "prompt_tokens": 30,
            "completion_tokens": 10,
            "cost_usd": 0.001,
            "field_accuracy": 1.0,
        },
        {
            "latency_ms": 300.0,
            "validation_status": "invalid",
            "fallback_reason": "provider_error",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cost_usd": None,
            "field_accuracy": 0.0,
        },
    ]

    assert build_pipeline_summary_metrics(rows) == {
        "documents": 2,
        "invalid_documents": 1,
        "fallback_documents": 1,
        "total_prompt_tokens": 30,
        "total_completion_tokens": 10,
        "total_cost_usd": 0.001,
        "average_latency_ms": 200.0,
        "average_accuracy": 0.5,
    }
