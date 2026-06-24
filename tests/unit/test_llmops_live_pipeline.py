import json
import subprocess
import sys
from pathlib import Path

from llmops.live_provider import OpenAICompatibleExtractor
from llmops.pipeline import build_live_eval_row
from llmops.reporting import write_llmops_artifacts
from scripts.run_llmops_pipeline import DEFAULT_OPENAI_API_BASE, load_openai_config


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str):
        self.chat = _FakeChat(content)


def test_openai_compatible_extractor_returns_valid_fields():
    client = _FakeClient(
        json.dumps(
            {
                "invoice_number": "INV-1001",
                "invoice_date": "01/15/2026",
                "due_date": "01/30/2026",
                "po_number": "PO-77",
                "payment_terms": "Net 15",
                "vendor_name": "Acme Supplies",
                "vendor_tax_id": "GSTIN-123",
                "customer_name": "Example Customer",
                "customer_tax_id": "GSTIN-999",
                "subtotal": "237.50",
                "total": "250.00",
                "tax": "12.50",
                "currency": "USD",
                "order_items": [
                    {
                        "line_no": 1,
                        "description": "Blue Widgets",
                        "qty": 5,
                        "unit": "pcs",
                        "unit_price": "47.50",
                        "net_amount": "237.50",
                        "tax_rate": "5%",
                        "gross_amount": "250.00",
                    }
                ],
            }
        )
    )
    extractor = OpenAICompatibleExtractor(
        client=client,
        model="gpt-4o-mini",
        prompt="Extract invoice fields.",
        schema_path=Path("schemas/invoice_v2.json"),
    )

    result = extractor.extract("Invoice Number: INV-1001")

    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert result.fields["invoice_number"] == "INV-1001"
    assert result.fields["order_items"][0]["line_no"] == "1"
    assert result.fields["order_items"][0]["qty"] == "5"
    assert result.validation_status == "valid"
    assert result.validation_errors == []
    assert result.fallback_reason is None
    assert client.chat.completions.calls[0]["temperature"] == 0


def test_openai_compatible_extractor_records_parse_failure_without_local_fallback():
    client = _FakeClient("not json")
    extractor = OpenAICompatibleExtractor(
        client=client,
        model="gpt-4o-mini",
        prompt="Extract invoice fields.",
        schema_path=Path("schemas/invoice_v2.json"),
    )

    result = extractor.extract("Invoice Number: INV-1001")

    assert result.validation_status == "invalid"
    assert result.fields == {
        "invoice_number": None,
        "invoice_date": None,
        "due_date": None,
        "po_number": None,
        "payment_terms": None,
        "vendor_name": None,
        "vendor_tax_id": None,
        "customer_name": None,
        "customer_tax_id": None,
        "subtotal": None,
        "total": None,
        "tax": None,
        "currency": None,
        "order_items": [],
        "fallback_reason": "llm_parse_error",
        "fallback_detail": "LLM response was not valid JSON.",
    }
    assert result.fallback_reason == "llm_parse_error"
    assert result.validation_errors == ["LLM response was not valid JSON."]


def test_build_live_eval_row_scores_parse_failure_as_zero_accuracy():
    provider_result = OpenAICompatibleExtractor.empty_failure_result(
        provider="openai",
        model="gpt-4o-mini",
        fallback_reason="llm_parse_error",
        fallback_detail="LLM response was not valid JSON.",
        validation_errors=["LLM response was not valid JSON."],
    )

    row = build_live_eval_row(
        dataset_row={
            "document_id": "doc-1",
            "source_name": "sample.txt",
            "expected_fields": {
                "invoice_number": "INV-1001",
                "invoice_date": None,
                "due_date": None,
                "po_number": "PO-77",
                "payment_terms": "Net 15",
                "vendor_name": "Acme Supplies",
                "vendor_tax_id": "GSTIN-123",
                "customer_name": None,
                "customer_tax_id": None,
                "subtotal": "237.50",
                "total": "250.00",
                "tax": None,
                "currency": "USD",
                "order_items": [
                    {
                        "line_no": "1",
                        "description": "Blue Widgets",
                        "qty": "5",
                        "unit": "pcs",
                        "unit_price": "47.50",
                        "net_amount": "237.50",
                        "tax_rate": "5%",
                        "gross_amount": "250.00",
                    }
                ],
            },
        },
        provider_result=provider_result,
        prompt_version="v2",
        schema_version="v2",
    )

    assert row["field_accuracy"] == 0.0
    assert row["scalar_field_accuracy"] == 0.0
    assert row["order_item_field_accuracy"] == 0.0
    assert row["validation_status"] == "invalid"
    assert row["fallback_reason"] == "llm_parse_error"
    assert row["missing_fields"] == [
        "invoice_number",
        "po_number",
        "payment_terms",
        "vendor_name",
        "vendor_tax_id",
        "subtotal",
        "total",
        "currency",
        "order_items[0].line_no",
        "order_items[0].description",
        "order_items[0].qty",
        "order_items[0].unit",
        "order_items[0].unit_price",
        "order_items[0].net_amount",
        "order_items[0].tax_rate",
        "order_items[0].gross_amount",
    ]


def test_write_llmops_artifacts_creates_json_html_charts_and_dag(tmp_path: Path):
    rows = [
        {
            "document_id": "doc-1",
            "source_name": "sample.txt",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "prompt_version": "v2",
            "schema_version": "v2",
            "latency_ms": 120.5,
            "validation_status": "valid",
            "field_accuracy": 1.0,
            "scalar_field_accuracy": 1.0,
            "order_item_field_accuracy": 1.0,
            "missing_fields": [],
            "fallback_reason": None,
            "validation_errors": [],
        }
    ]

    report = write_llmops_artifacts(
        rows=rows,
        output_dir=tmp_path,
        min_field_accuracy=0.8,
        prompt_version="v2",
        schema_version="v2",
    )

    assert report["meets_threshold"] is True
    assert (tmp_path / "live_eval_report.json").exists()
    assert (tmp_path / "live_eval_report.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (tmp_path / "field_accuracy_chart.png").stat().st_size > 0
    assert (tmp_path / "provider_latency_chart.png").stat().st_size > 0
    assert "graph TD" in (tmp_path / "pipeline_dag.mmd").read_text(encoding="utf-8")


def test_live_pipeline_cli_fails_clearly_without_api_key(tmp_path: Path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENAI_API_KEY", "")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_llmops_pipeline.py",
            "--no-dotenv",
            "--dataset",
            "data/golden/invoice_extraction_v2.jsonl",
            "--output-dir",
            str(tmp_path),
            "--min-field-accuracy",
            "0.80",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "OPENAI_API_KEY is required for live LLMOps pipeline" in result.stderr


def test_load_openai_config_uses_default_base_when_env_base_is_blank(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_BASE", "")

    api_key, api_base = load_openai_config(tmp_path)

    assert api_key == "test-key"
    assert api_base == DEFAULT_OPENAI_API_BASE
