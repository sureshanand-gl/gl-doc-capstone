"""Tests for field-accuracy metrics, trace output, and golden-eval report aggregation."""

from pathlib import Path
import json
import subprocess
import sys

from llmops.metrics import build_eval_report, compute_field_accuracy, load_golden_dataset
from llmops.tracing import write_trace_record


def test_metrics_compute_field_accuracy_and_missing_fields():
    expected = {
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
    }
    predicted = {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": None,
        "po_number": "PO-77",
        "payment_terms": "Net 15",
        "vendor_name": "Acme Supplies",
        "vendor_tax_id": "GSTIN-123",
        "customer_name": "Example Customer",
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
                "tax_rate": None,
                "gross_amount": "250.00",
            }
        ],
        "fallback_reason": "local_regex_fallback",
    }

    metrics = compute_field_accuracy(expected, predicted)

    assert metrics["matched_fields"] == 17
    assert metrics["total_fields"] == 21
    assert metrics["field_accuracy"] == 0.8095
    assert metrics["scalar_field_accuracy"] == 0.7692
    assert metrics["order_item_field_accuracy"] == 0.875
    assert metrics["missing_fields"] == [
        "due_date",
        "customer_tax_id",
        "tax",
        "order_items[0].tax_rate",
    ]


def test_trace_writer_redacts_raw_text_by_default(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"

    write_trace_record(
        trace_path=trace_path,
        record={
            "document_id": "doc-1",
            "ocr_text": "secret invoice text",
            "fields": {"invoice_number": "INV-1001"},
        },
        include_text=False,
    )

    payload = trace_path.read_text(encoding="utf-8")

    assert "secret invoice text" not in payload
    assert '"ocr_text_redacted": true' in payload


def test_load_golden_dataset_reads_fixture():
    repo_root = Path(__file__).resolve().parents[2]

    dataset = load_golden_dataset(repo_root / "data" / "golden" / "invoice_extraction_v2.jsonl")

    assert len(dataset) == 2
    assert dataset[0]["document_id"] == "golden-invoice-001"


def test_build_eval_report_includes_versions_and_threshold_status():
    rows = [
        {
            "document_id": "doc-1",
            "source_name": "sample.txt",
            "metrics": {
                "field_accuracy": 0.8095,
                "scalar_field_accuracy": 0.7692,
                "order_item_field_accuracy": 0.875,
                "missing_fields": ["tax"],
            },
        },
        {
            "document_id": "doc-2",
            "source_name": "sample2.txt",
            "metrics": {
                "field_accuracy": 1.0,
                "scalar_field_accuracy": 1.0,
                "order_item_field_accuracy": 1.0,
                "missing_fields": [],
            },
        },
    ]

    report = build_eval_report(rows, prompt_version="v2", schema_version="v2", min_field_accuracy=0.95)

    assert report["documents"] == 2
    assert report["prompt_version"] == "v2"
    assert report["schema_version"] == "v2"
    assert report["average_field_accuracy"] == 0.9047
    assert report["average_scalar_field_accuracy"] == 0.8846
    assert report["average_order_item_field_accuracy"] == 0.9375
    assert report["meets_threshold"] is False


def test_golden_eval_cli_writes_report_to_requested_output_path(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    report_path = tmp_path / "reports" / "llmops_eval_report.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_golden_eval.py",
            "--min-field-accuracy",
            "0.80",
            "--output-path",
            str(report_path),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["meets_threshold"] is True
    assert payload["minimum_field_accuracy"] == 0.8


def test_golden_eval_cli_returns_nonzero_when_threshold_fails(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    report_path = tmp_path / "llmops_eval_report.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_golden_eval.py",
            "--min-field-accuracy",
            "1.01",
            "--output-path",
            str(report_path),
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["meets_threshold"] is False
    assert payload["minimum_field_accuracy"] == 1.01
