import time
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, push_to_gateway, start_http_server
except ImportError:  # pragma: no cover - fallback for environments missing dependency
    from collections import namedtuple

    Sample = namedtuple(
        "Sample",
        ["name", "labels", "value", "timestamp", "exemplar", "native_histogram"],
        defaults=[None, None, None],
    )

    class CollectorRegistry:
        def __init__(self):
            self._collectors: list[Any] = []

        def register(self, collector: Any) -> None:
            self._collectors.append(collector)

        def collect(self) -> list[Any]:
            return list(self._collectors)

    class _BoundMetric:
        def __init__(self, metric: Any, labels: dict[str, str]):
            self.metric = metric
            self.labels = labels

        def inc(self, amount: float = 1.0) -> None:
            self.metric._values[self.metric._label_key(self.labels)] = (
                self.metric._values.get(self.metric._label_key(self.labels), 0.0) + amount
            )

        def observe(self, value: float) -> None:
            self.metric._values[self.metric._label_key(self.labels)] = value

        def set(self, value: float) -> None:
            self.metric._values[self.metric._label_key(self.labels)] = value

    class _Metric:
        def __init__(self, name: str, documentation: str, labelnames=(), registry: CollectorRegistry | None = None, **_: Any):
            self.name = name
            self.documentation = documentation
            self.labelnames = tuple(labelnames)
            self._values: dict[tuple[str, ...], float] = {}
            self._registry = registry
            if registry is not None:
                registry.register(self)

        def _label_key(self, labels: dict[str, str]) -> tuple[str, ...]:
            return tuple(str(labels[name]) for name in self.labelnames)

        def labels(self, **labels: str) -> _BoundMetric:
            return _BoundMetric(self, labels)

        def inc(self, amount: float = 1.0) -> None:
            self._values[()] = self._values.get((), 0.0) + amount

        def observe(self, value: float) -> None:
            self._values[()] = value

        def set(self, value: float) -> None:
            self._values[()] = value

        @property
        def samples(self) -> list[Sample]:
            return [
                Sample(
                    name=self.name,
                    labels=dict(zip(self.labelnames, key)),
                    value=value,
                    timestamp=None,
                    exemplar=None,
                    native_histogram=None,
                )
                for key, value in self._values.items()
            ]

    class Counter(_Metric):
        pass

    class Gauge(_Metric):
        pass

    class Histogram(_Metric):
        pass

    def push_to_gateway(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("prometheus_client is not installed")

    def start_http_server(*args: Any, **kwargs: Any) -> None:
        return None


DEFAULT_MODEL_PRICING = {
    "gpt-4o-mini": {
        "input_cost_per_1k_tokens": 0.00015,
        "output_cost_per_1k_tokens": 0.0006,
    }
}
DEFAULT_LABEL_NAMES = ("surface", "provider", "model", "status", "document_type")
_DEFAULT_TELEMETRY: "LLMOpsTelemetry | None" = None
_STARTED_PORTS: set[int] = set()
_LOCK = Lock()


def load_model_pricing(pricing_path: Path | None) -> dict[str, dict[str, float]]:
    pricing = {
        model: {
            "input_cost_per_1k_tokens": float(values["input_cost_per_1k_tokens"]),
            "output_cost_per_1k_tokens": float(values["output_cost_per_1k_tokens"]),
        }
        for model, values in DEFAULT_MODEL_PRICING.items()
    }
    if pricing_path is None or not pricing_path.exists():
        return pricing

    payload = yaml.safe_load(pricing_path.read_text(encoding="utf-8")) or {}
    for model, values in (payload.get("models") or {}).items():
        pricing[str(model)] = {
            "input_cost_per_1k_tokens": float(values["input_cost_per_1k_tokens"]),
            "output_cost_per_1k_tokens": float(values["output_cost_per_1k_tokens"]),
        }
    return pricing


def normalize_usage(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None

    def _value(name: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    prompt_tokens = _value("prompt_tokens")
    completion_tokens = _value("completion_tokens")
    total_tokens = _value("total_tokens")
    if prompt_tokens is None or completion_tokens is None:
        return None
    if total_tokens is None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
    }


def calculate_usage_cost(
    model: str,
    usage: dict[str, int] | None,
    pricing: dict[str, dict[str, float]],
) -> float | None:
    if usage is None:
        return None
    model_pricing = pricing.get(model)
    if model_pricing is None:
        return None
    cost = (
        (usage["prompt_tokens"] / 1000.0) * model_pricing["input_cost_per_1k_tokens"]
        + (usage["completion_tokens"] / 1000.0) * model_pricing["output_cost_per_1k_tokens"]
    )
    return round(cost, 8)


def build_pipeline_summary_metrics(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    documents = len(rows)
    return {
        "documents": documents,
        "invalid_documents": sum(1 for row in rows if row.get("validation_status") != "valid"),
        "fallback_documents": sum(1 for row in rows if row.get("fallback_reason")),
        "total_prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows),
        "total_completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "total_cost_usd": round(sum(float(row.get("cost_usd") or 0.0) for row in rows), 8),
        "average_latency_ms": round(
            sum(float(row.get("latency_ms") or 0.0) for row in rows) / documents, 2
        ) if documents else 0.0,
        "average_accuracy": round(
            sum(float(row.get("field_accuracy") or 0.0) for row in rows) / documents, 4
        ) if documents else 0.0,
    }


def publish_pipeline_summary(
    pushgateway_url: str,
    report: dict[str, Any],
    job: str = "llmops_live_pipeline",
) -> None:
    registry = CollectorRegistry()
    metrics = {
        "llmops_pipeline_documents": int(report.get("documents", 0)),
        "llmops_pipeline_invalid_documents": int(report.get("invalid_documents", 0)),
        "llmops_pipeline_fallback_documents": int(report.get("fallback_documents", 0)),
        "llmops_pipeline_total_prompt_tokens": int(report.get("total_prompt_tokens", 0)),
        "llmops_pipeline_total_completion_tokens": int(report.get("total_completion_tokens", 0)),
        "llmops_pipeline_total_cost_usd": float(report.get("total_cost_usd", 0.0)),
        "llmops_pipeline_average_latency_ms": float(report.get("average_latency_ms", 0.0)),
        "llmops_pipeline_average_accuracy": float(
            report.get("average_field_accuracy", report.get("average_accuracy", 0.0))
        ),
        "llmops_pipeline_last_success_unixtime": time.time(),
    }
    for metric_name, value in metrics.items():
        gauge = Gauge(metric_name, metric_name, registry=registry)
        gauge.set(value)
    push_to_gateway(pushgateway_url, job=job, registry=registry)


class LLMOpsTelemetry:
    def __init__(
        self,
        pricing: dict[str, dict[str, float]],
        registry: CollectorRegistry | None = None,
    ) -> None:
        self.pricing = pricing
        self.registry = registry or CollectorRegistry()
        self.requests_total = Counter(
            "llmops_requests_total",
            "Total LLMOps requests.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )
        self.request_failures_total = Counter(
            "llmops_request_failures_total",
            "Total failed or fallback LLMOps requests.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )
        self.request_latency_ms = Histogram(
            "llmops_request_latency_ms",
            "LLMOps request latency in milliseconds.",
            labelnames=DEFAULT_LABEL_NAMES,
            buckets=(50, 100, 250, 500, 1000, 2000, 5000, 10000),
            registry=self.registry,
        )
        self.prompt_tokens_total = Counter(
            "llmops_prompt_tokens_total",
            "Total prompt tokens billed by provider.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )
        self.completion_tokens_total = Counter(
            "llmops_completion_tokens_total",
            "Total completion tokens billed by provider.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )
        self.total_tokens_total = Counter(
            "llmops_total_tokens_total",
            "Total provider tokens billed.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )
        self.cost_usd_total = Counter(
            "llmops_cost_usd_total",
            "Total estimated provider cost in USD.",
            labelnames=DEFAULT_LABEL_NAMES,
            registry=self.registry,
        )

    def start_server(self, port: int) -> None:
        with _LOCK:
            if port in _STARTED_PORTS:
                return
            start_http_server(port, registry=self.registry)
            _STARTED_PORTS.add(port)

    def record_request(
        self,
        *,
        surface: str,
        provider: str,
        model: str,
        status: str,
        document_type: str,
        latency_ms: float,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        labels = {
            "surface": surface,
            "provider": provider,
            "model": model,
            "status": status,
            "document_type": document_type,
        }
        self.requests_total.labels(**labels).inc()
        self.request_latency_ms.labels(**labels).observe(float(latency_ms))
        if status != "valid":
            self.request_failures_total.labels(**labels).inc()
        if prompt_tokens is not None:
            self.prompt_tokens_total.labels(**labels).inc(prompt_tokens)
        if completion_tokens is not None:
            self.completion_tokens_total.labels(**labels).inc(completion_tokens)
        if total_tokens is not None:
            self.total_tokens_total.labels(**labels).inc(total_tokens)
        if cost_usd is not None:
            self.cost_usd_total.labels(**labels).inc(cost_usd)


def get_default_telemetry(pricing_path: Path | None) -> LLMOpsTelemetry:
    global _DEFAULT_TELEMETRY
    with _LOCK:
        if _DEFAULT_TELEMETRY is None:
            _DEFAULT_TELEMETRY = LLMOpsTelemetry(load_model_pricing(pricing_path))
        return _DEFAULT_TELEMETRY
