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
        "total": "250.00",
        "tax": "12.50",
        "vendor_name": "Acme Supplies",
        "customer_name": "Example Customer",
        "currency": "USD",
    }
    predicted = {
        "invoice_number": "INV-1001",
        "invoice_date": "01/15/2026",
        "due_date": None,
        "total": "250.00",
        "tax": None,
        "vendor_name": "Acme Supplies",
        "customer_name": "Example Customer",
        "currency": "USD",
        "fallback_reason": "local_regex_fallback",
    }

    metrics = compute_field_accuracy(expected, predicted)

    assert metrics["matched_fields"] == 6
    assert metrics["field_accuracy"] == 0.75
    assert metrics["missing_fields"] == ["due_date", "tax"]


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

    dataset = load_golden_dataset(repo_root / "data" / "golden" / "invoice_extraction_v1.jsonl")

    assert len(dataset) == 2
    assert dataset[0]["document_id"] == "golden-invoice-001"


def test_build_eval_report_includes_versions_and_threshold_status():
    rows = [
        {
            "document_id": "doc-1",
            "source_name": "sample.txt",
            "metrics": {"field_accuracy": 0.75, "missing_fields": ["tax"]},
        },
        {
            "document_id": "doc-2",
            "source_name": "sample2.txt",
            "metrics": {"field_accuracy": 1.0, "missing_fields": []},
        },
    ]

    report = build_eval_report(rows, prompt_version="v1", schema_version="v1", min_field_accuracy=0.9)

    assert report["documents"] == 2
    assert report["prompt_version"] == "v1"
    assert report["schema_version"] == "v1"
    assert report["average_field_accuracy"] == 0.875
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
