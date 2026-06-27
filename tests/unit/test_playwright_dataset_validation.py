"""Unit tests for deployed-app Playwright dataset validation helpers."""

from pathlib import Path

from scripts.run_playwright_dataset_validation import (
    SUPPORTED_EXTENSIONS,
    build_summary,
    compare_fields,
    discover_dataset_files,
    load_image_truth_map,
    load_pdf_truth_map,
    normalize_field_value,
    wait_for_enabled,
)


def test_discover_dataset_files_filters_supported_extensions(tmp_path: Path):
    dataset_root = tmp_path / "Datasets"
    (dataset_root / "nested").mkdir(parents=True)
    (dataset_root / "invoice.pdf").write_bytes(b"pdf")
    (dataset_root / "nested" / "scan.jpg").write_bytes(b"jpg")
    (dataset_root / "nested" / "notes.docx").write_bytes(b"docx")
    (dataset_root / "nested" / "ignored.csv").write_text("x", encoding="utf-8")
    (dataset_root / ".DS_Store").write_text("", encoding="utf-8")

    files = discover_dataset_files(dataset_root, selected_types={"pdf", "jpg", "docx"})

    assert SUPPORTED_EXTENSIONS[".pdf"] == "pdf"
    assert [path.name for path in files] == ["invoice.pdf", "notes.docx", "scan.jpg"]


def test_load_truth_maps_extract_expected_fields():
    repo_root = Path(__file__).resolve().parents[2]

    pdf_truth = load_pdf_truth_map(repo_root / "Datasets" / "Batch 1" / "batch_1.csv")
    image_truth = load_image_truth_map(
        repo_root
        / "Datasets"
        / "High-Quality Invoice Scanned Images for OCR"
        / "batch_1"
        / "batch_1"
        / "batch1_1.csv"
    )

    assert pdf_truth["invoice_51109301.pdf"]["invoice_number"] == "51109301"
    assert pdf_truth["invoice_51109301.pdf"]["vendor_name"] == "TechVision Distributors Pvt Ltd"
    assert pdf_truth["invoice_51109301.pdf"]["tax"] == "INR 167,697.60"
    assert image_truth["batch1-0494.jpg"]["invoice_number"] == "84652373"
    assert image_truth["batch1-0494.jpg"]["customer_name"] == "Clark-Foster"
    assert image_truth["batch1-0494.jpg"]["total"] == "232.95"


def test_normalize_field_value_handles_dates_money_blanks_and_text():
    assert normalize_field_value("invoice_date", "2024-02-03") == {"2024-02-03"}
    assert normalize_field_value("invoice_date", "03 Feb 2024") == {"2024-02-03"}
    assert normalize_field_value("invoice_date", "03/07/2023") == {"2023-03-07", "2023-07-03"}
    assert normalize_field_value("tax", "INR 1,844,673.60") == {"1844673.60"}
    assert normalize_field_value("total", "1 844 673,60") == {"1844673.60"}
    assert normalize_field_value("due_date", "") == {None}
    assert normalize_field_value("vendor_name", "  TechVision   Distributors Pvt Ltd ") == {
        "techvision distributors pvt ltd"
    }


def test_compare_fields_reports_pass_mismatch_and_smoke_modes():
    expected = {
        "invoice_number": "51109301",
        "invoice_date": "03/07/2023",
        "due_date": "",
        "vendor_name": "TechVision Distributors Pvt Ltd",
        "customer_name": "Raj Electronics Pvt Ltd",
        "tax": "INR 167,697.60",
        "total": "INR 1,844,673.60",
    }
    actual_pass = {
        "invoice_number": "51109301",
        "invoice_date": "2023-07-03",
        "due_date": None,
        "vendor_name": "techvision distributors pvt ltd",
        "customer_name": "Raj Electronics Pvt Ltd",
        "tax": "167697.60",
        "total": "1844673.60",
    }
    actual_fail = dict(actual_pass, total="999.99")

    pass_result = compare_fields(expected, actual_pass, truth_mode="compare")
    fail_result = compare_fields(expected, actual_fail, truth_mode="compare")
    smoke_result = compare_fields({}, actual_pass, truth_mode="smoke")

    assert pass_result["status"] == "pass"
    assert pass_result["mismatches"] == {}
    assert fail_result["status"] == "mismatch"
    assert fail_result["mismatches"]["total"]["expected"] == "INR 1,844,673.60"
    assert smoke_result["status"] == "pass"
    assert smoke_result["mismatches"] == {}


def test_build_summary_counts_status_and_truth_modes():
    results = [
        {"status": "pass", "file_type": "pdf", "truth_mode": "compare"},
        {"status": "mismatch", "file_type": "jpg", "truth_mode": "compare"},
        {"status": "pass", "file_type": "docx", "truth_mode": "smoke"},
    ]

    summary = build_summary(results)

    assert summary["total_files"] == 3
    assert summary["status_counts"] == {"pass": 2, "mismatch": 1}
    assert summary["file_type_counts"] == {"docx": 1, "jpg": 1, "pdf": 1}
    assert summary["truth_mode_counts"] == {"compare": 2, "smoke": 1}
    assert summary["non_pass_count"] == 1


def test_wait_for_enabled_polls_until_control_is_ready(monkeypatch):
    class FakePage:
        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

    class FakeLocator:
        def __init__(self):
            self.page = FakePage()
            self.calls = 0

        def is_enabled(self) -> bool:
            self.calls += 1
            return self.calls >= 3

    locator = FakeLocator()
    monotonic_values = iter([0.0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(
        "scripts.run_playwright_dataset_validation.time.monotonic",
        lambda: next(monotonic_values),
    )

    wait_for_enabled(locator, timeout_ms=1000)

    assert locator.calls == 3
