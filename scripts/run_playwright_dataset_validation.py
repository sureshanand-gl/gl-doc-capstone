"""Browser-driven validator for deployed Streamlit invoice extraction app."""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from argparse import ArgumentParser, BooleanOptionalAction
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only when dependency missing
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "jpg",
    ".docx": "docx",
}
TRUTH_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "vendor_name",
    "customer_name",
    "tax",
    "total",
)
LLMOPS_FIELDS = ("provider", "model", "prompt_version", "schema_version", "validation_status")
SUCCESS_TEXT = "Processing complete."
ERROR_TEXT = "Processing failed:"
FILE_UPLOADER_TEXT = "Upload JPG, PDF, or DOCX"
PROCESS_BUTTON_TEXT = "Upload and Process"
SECTION_FIELDS_TEXT = "Extracted Fields"
SECTION_LLMOPS_TEXT = "LLMOps Metadata"
DATE_FIELD_NAMES = {"invoice_date", "due_date"}
MONEY_FIELD_NAMES = {"tax", "total"}


@dataclass
class ValidationRow:
    relative_path: str
    file_type: str
    truth_mode: str
    status: str
    error: str | None
    expected_fields: dict[str, Any]
    actual_fields: dict[str, Any]
    llmops_metadata: dict[str, Any]
    mismatches: dict[str, dict[str, Any]]
    screenshot_path: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "file_type": self.file_type,
            "truth_mode": self.truth_mode,
            "status": self.status,
            "error": self.error,
            "mismatch_fields": ",".join(sorted(self.mismatches)),
            "expected_fields": json.dumps(self.expected_fields, sort_keys=True),
            "actual_fields": json.dumps(self.actual_fields, sort_keys=True),
            "llmops_metadata": json.dumps(self.llmops_metadata, sort_keys=True),
            "mismatches": json.dumps(self.mismatches, sort_keys=True),
            "screenshot_path": self.screenshot_path,
        }


def parse_args() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--dataset-root", type=Path, default=REPO_ROOT / "Datasets")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "outputs" / "playwright_dataset_validation",
    )
    parser.add_argument("--types", default="pdf,jpg,docx")
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--headless", action=BooleanOptionalAction, default=True)
    parser.add_argument("--per-file-timeout-seconds", type=int, default=180)
    return parser


def parse_selected_types(raw_types: str) -> set[str]:
    selected = {value.strip().lower() for value in raw_types.split(",") if value.strip()}
    allowed = {"pdf", "jpg", "docx"}
    invalid = sorted(selected - allowed)
    if invalid:
        raise ValueError(f"Unsupported --types values: {', '.join(invalid)}")
    return selected or allowed


def discover_dataset_files(dataset_root: Path, selected_types: set[str]) -> list[Path]:
    files = []
    for path in sorted(dataset_root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        file_type = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
        if file_type and file_type in selected_types:
            files.append(path)
    return files


def load_pdf_truth_map(csv_path: Path) -> dict[str, dict[str, Any]]:
    truth_map = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            payload = json.loads(row["json_data"])
            truth_map[row["filename"]] = {
                "invoice_number": payload.get("invoice_no"),
                "invoice_date": payload.get("date_of_issue"),
                "due_date": None,
                "vendor_name": payload.get("seller", {}).get("name"),
                "customer_name": payload.get("client", {}).get("name"),
                "tax": payload.get("summary", {}).get("total_vat")
                or payload.get("summary", {}).get("vat_amount"),
                "total": payload.get("summary", {}).get("total_gross_worth")
                or payload.get("summary", {}).get("gross_worth"),
            }
    return truth_map


def load_image_truth_map(csv_path: Path) -> dict[str, dict[str, Any]]:
    truth_map = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            payload = json.loads(row["Json Data"])
            invoice = payload.get("invoice", {})
            subtotal = payload.get("subtotal", {})
            truth_map[row["File Name"]] = {
                "invoice_number": invoice.get("invoice_number"),
                "invoice_date": invoice.get("invoice_date"),
                "due_date": invoice.get("due_date"),
                "vendor_name": invoice.get("seller_name"),
                "customer_name": invoice.get("client_name"),
                "tax": subtotal.get("tax"),
                "total": subtotal.get("total"),
            }
    return truth_map


def load_truth_maps(dataset_root: Path) -> dict[str, dict[str, Any]]:
    pdf_truth = load_pdf_truth_map(dataset_root / "Batch 1" / "batch_1.csv")
    image_truth = load_image_truth_map(
        dataset_root
        / "High-Quality Invoice Scanned Images for OCR"
        / "batch_1"
        / "batch_1"
        / "batch1_1.csv"
    )
    combined = {}
    combined.update(pdf_truth)
    combined.update(image_truth)
    return combined


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def normalize_money_value(value: str) -> str | None:
    text = normalize_whitespace(value)
    if not text:
        return None
    cleaned = re.sub(r"[A-Za-z$€£¥₹:_-]", "", text)
    cleaned = cleaned.replace(" ", "")
    if not cleaned:
        return None
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    if last_comma > last_dot:
        cleaned = cleaned.replace(".", "")
        if cleaned.count(",") > 1:
            head, tail = cleaned.rsplit(",", 1)
            cleaned = head.replace(",", "") + "." + tail
        else:
            cleaned = cleaned.replace(",", ".")
    elif last_dot > last_comma:
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return f"{Decimal(cleaned):.2f}"
    except InvalidOperation:
        return normalize_whitespace(value).lower()


def date_candidates(value: str) -> set[str]:
    text = normalize_whitespace(value)
    if not text:
        return {None}
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso_match:
        return {f"{int(iso_match.group(1)):04d}-{int(iso_match.group(2)):02d}-{int(iso_match.group(3)):02d}"}
    numeric_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if numeric_match:
        first = int(numeric_match.group(1))
        second = int(numeric_match.group(2))
        year = int(numeric_match.group(3))
        candidates = set()
        for month, day in ((first, second), (second, first)):
            if 1 <= month <= 12 and 1 <= day <= 31:
                candidates.add(f"{year:04d}-{month:02d}-{day:02d}")
        return candidates or {text.lower()}
    for fmt in (
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b, %Y",
        "%d %B, %Y",
    ):
        try:
            return {datetime.strptime(text, fmt).strftime("%Y-%m-%d")}
        except ValueError:
            continue
    return {text.lower()}


def normalize_field_value(field_name: str, value: Any) -> set[Any]:
    if value is None:
        return {None}
    text = normalize_whitespace(str(value))
    if text.lower() in {"", "null", "none"}:
        return {None}
    if field_name in DATE_FIELD_NAMES:
        return date_candidates(text)
    if field_name in MONEY_FIELD_NAMES:
        return {normalize_money_value(text)}
    return {text.lower()}


def compare_fields(
    expected_fields: dict[str, Any],
    actual_fields: dict[str, Any],
    truth_mode: str,
) -> dict[str, Any]:
    mismatches: dict[str, dict[str, Any]] = {}
    if truth_mode == "smoke":
        return {"status": "pass", "mismatches": mismatches}
    for field_name in TRUTH_FIELDS:
        expected = expected_fields.get(field_name)
        actual = actual_fields.get(field_name)
        if normalize_field_value(field_name, expected).isdisjoint(
            normalize_field_value(field_name, actual)
        ):
            mismatches[field_name] = {"expected": expected, "actual": actual}
    return {"status": "mismatch" if mismatches else "pass", "mismatches": mismatches}


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(result["status"] for result in results)
    file_type_counts = Counter(result["file_type"] for result in results)
    truth_mode_counts = Counter(result["truth_mode"] for result in results)
    return {
        "total_files": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "file_type_counts": dict(sorted(file_type_counts.items())),
        "truth_mode_counts": dict(sorted(truth_mode_counts.items())),
        "non_pass_count": len([result for result in results if result["status"] != "pass"]),
    }


def sanitize_output_name(relative_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", relative_path)


def extract_json_fields_from_text(raw_text: str, keys: tuple[str, ...]) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {key: parsed.get(key) for key in keys if key in parsed}
    except json.JSONDecodeError:
        pass

    extracted: dict[str, Any] = {}
    normalized = text.replace("\u00a0", " ")
    for key in keys:
        patterns = (
            rf'"{key}"\s*:\s*(null|"[^"]*"|[^\n,}}]+)',
            rf"{key}\s*:\s*(null|[^\n]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            raw_value = match.group(1).strip().strip(",")
            if raw_value.lower() == "null":
                extracted[key] = None
            else:
                extracted[key] = raw_value.strip('"')
            break
    return extracted


def extract_section_text(page: Any, heading_text: str) -> str:
    heading = page.get_by_role("heading", name=heading_text)
    heading.wait_for(timeout=15000)
    json_block = heading.locator("xpath=following::div[@data-testid='stJson'][1]")
    json_block.wait_for(timeout=15000)
    return json_block.inner_text(timeout=15000)


def write_results_csv(rows: list[ValidationRow], output_path: Path) -> None:
    fieldnames = [
        "relative_path",
        "file_type",
        "truth_mode",
        "status",
        "error",
        "mismatch_fields",
        "expected_fields",
        "actual_fields",
        "llmops_metadata",
        "mismatches",
        "screenshot_path",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_record())


def wait_for_processing_result(page: Any, timeout_ms: int) -> str:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        body_text = page.locator("body").inner_text()
        if SUCCESS_TEXT in body_text:
            return "success"
        if ERROR_TEXT in body_text:
            return "error"
        page.wait_for_timeout(1000)
    raise PlaywrightTimeoutError(f"Timed out after {timeout_ms}ms waiting for processing result")


def wait_for_enabled(locator: Any, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        if locator.is_enabled():
            return
        locator.page.wait_for_timeout(250)
    raise PlaywrightTimeoutError(f"Timed out after {timeout_ms}ms waiting for enabled control")


def classify_truth_mode(file_type: str, filename: str, truth_map: dict[str, dict[str, Any]]) -> str:
    if file_type == "docx":
        return "smoke"
    return "compare" if filename in truth_map else "smoke"


def process_single_file(
    page: Any,
    base_url: str,
    dataset_root: Path,
    file_path: Path,
    truth_map: dict[str, dict[str, Any]],
    failures_dir: Path,
    timeout_ms: int,
) -> ValidationRow:
    relative_path = file_path.relative_to(dataset_root).as_posix()
    file_type = SUPPORTED_EXTENSIONS[file_path.suffix.lower()]
    truth_mode = classify_truth_mode(file_type, file_path.name, truth_map)
    expected_fields = truth_map.get(file_path.name, {}) if truth_mode == "compare" else {}
    screenshot_path = failures_dir / f"{sanitize_output_name(relative_path)}.png"

    try:
        page.goto(base_url, wait_until="domcontentloaded")
        page.get_by_text(FILE_UPLOADER_TEXT).wait_for(timeout=timeout_ms)
        page.locator("input[type='file']").set_input_files(str(file_path))
        process_button = page.get_by_role("button", name=PROCESS_BUTTON_TEXT)
        wait_for_enabled(process_button, timeout_ms=timeout_ms)
        process_button.click()
        outcome = wait_for_processing_result(page, timeout_ms)
        if outcome == "error":
            error_text = page.locator("body").inner_text()
            page.screenshot(path=str(screenshot_path), full_page=True)
            return ValidationRow(
                relative_path=relative_path,
                file_type=file_type,
                truth_mode=truth_mode,
                status="app_error",
                error=error_text,
                expected_fields=expected_fields,
                actual_fields={},
                llmops_metadata={},
                mismatches={},
                screenshot_path=str(screenshot_path.relative_to(REPO_ROOT).as_posix()),
            )

        fields_text = extract_section_text(page, SECTION_FIELDS_TEXT)
        llmops_text = extract_section_text(page, SECTION_LLMOPS_TEXT)
        actual_fields = extract_json_fields_from_text(fields_text, TRUTH_FIELDS)
        llmops_metadata = extract_json_fields_from_text(llmops_text, LLMOPS_FIELDS)
        if not actual_fields:
            page.screenshot(path=str(screenshot_path), full_page=True)
            return ValidationRow(
                relative_path=relative_path,
                file_type=file_type,
                truth_mode=truth_mode,
                status="parse_error",
                error="Could not extract visible field JSON from page",
                expected_fields=expected_fields,
                actual_fields={},
                llmops_metadata=llmops_metadata,
                mismatches={},
                screenshot_path=str(screenshot_path.relative_to(REPO_ROOT).as_posix()),
            )

        comparison = compare_fields(expected_fields, actual_fields, truth_mode=truth_mode)
        status = comparison["status"]
        row = ValidationRow(
            relative_path=relative_path,
            file_type=file_type,
            truth_mode=truth_mode,
            status=status,
            error=None,
            expected_fields=expected_fields,
            actual_fields=actual_fields,
            llmops_metadata=llmops_metadata,
            mismatches=comparison["mismatches"],
        )
        if status != "pass":
            page.screenshot(path=str(screenshot_path), full_page=True)
            row.screenshot_path = str(screenshot_path.relative_to(REPO_ROOT).as_posix())
        return row
    except PlaywrightTimeoutError as exc:
        page.screenshot(path=str(screenshot_path), full_page=True)
        return ValidationRow(
            relative_path=relative_path,
            file_type=file_type,
            truth_mode=truth_mode,
            status="timeout",
            error=str(exc),
            expected_fields=expected_fields,
            actual_fields={},
            llmops_metadata={},
            mismatches={},
            screenshot_path=str(screenshot_path.relative_to(REPO_ROOT).as_posix()),
        )
    except Exception as exc:  # pragma: no cover - exercised in live browser runs
        page.screenshot(path=str(screenshot_path), full_page=True)
        return ValidationRow(
            relative_path=relative_path,
            file_type=file_type,
            truth_mode=truth_mode,
            status="parse_error",
            error=str(exc),
            expected_fields=expected_fields,
            actual_fields={},
            llmops_metadata={},
            mismatches={},
            screenshot_path=str(screenshot_path.relative_to(REPO_ROOT).as_posix()),
        )


def run_validation(
    *,
    base_url: str,
    dataset_root: Path,
    output_dir: Path,
    selected_types: set[str],
    max_files: int | None,
    headless: bool,
    per_file_timeout_seconds: int,
) -> tuple[list[ValidationRow], dict[str, Any]]:
    if sync_playwright is None:
        raise RuntimeError(
            "Playwright is not installed. Run `uv sync` and `uv run python -m playwright install chromium` first."
        )

    truth_map = load_truth_maps(dataset_root)
    files = discover_dataset_files(dataset_root, selected_types=selected_types)
    if max_files is not None:
        files = files[:max_files]

    output_dir.mkdir(parents=True, exist_ok=True)
    failures_dir = output_dir / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.utcnow().isoformat() + "Z"
    rows: list[ValidationRow] = []
    timeout_ms = per_file_timeout_seconds * 1000

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            for file_path in files:
                page = browser.new_page()
                try:
                    rows.append(
                        process_single_file(
                            page,
                            base_url=base_url,
                            dataset_root=dataset_root,
                            file_path=file_path,
                            truth_map=truth_map,
                            failures_dir=failures_dir,
                            timeout_ms=timeout_ms,
                        )
                    )
                finally:
                    page.close()
        finally:
            browser.close()

    summary = build_summary([row.to_record() for row in rows])
    summary.update(
        {
            "base_url": base_url,
            "dataset_root": str(dataset_root),
            "output_dir": str(output_dir),
            "selected_types": sorted(selected_types),
            "started_at": started_at,
            "finished_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    return rows, summary


def main() -> int:
    args = parse_args().parse_args()
    selected_types = parse_selected_types(args.types)
    rows, summary = run_validation(
        base_url=args.base_url,
        dataset_root=args.dataset_root,
        output_dir=args.output_dir,
        selected_types=selected_types,
        max_files=args.max_files,
        headless=args.headless,
        per_file_timeout_seconds=args.per_file_timeout_seconds,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_results_csv(rows, args.output_dir / "results.csv")
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["non_pass_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
