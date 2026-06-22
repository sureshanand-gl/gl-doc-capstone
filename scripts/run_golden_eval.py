import json
import sys
from argparse import ArgumentParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llmops.local_extraction import extract_invoice_fields_local
from llmops.metrics import build_eval_report, compute_field_accuracy, load_golden_dataset


def parse_args() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument("--min-field-accuracy", type=float, default=0.80)
    return parser


def main() -> int:
    args = parse_args().parse_args()
    dataset_path = REPO_ROOT / "data" / "golden" / "invoice_extraction_v1.jsonl"
    report_path = REPO_ROOT / "outputs" / "llmops_eval_report.json"

    dataset = load_golden_dataset(dataset_path)
    rows = []

    for row in dataset:
        predicted = extract_invoice_fields_local(row["ocr_text"])
        metrics = compute_field_accuracy(row["expected_fields"], predicted)
        rows.append(
            {
                "document_id": row["document_id"],
                "source_name": row["source_name"],
                "metrics": metrics,
            }
        )

    report = build_eval_report(
        rows,
        prompt_version="v1",
        schema_version="v1",
        min_field_accuracy=args.min_field_accuracy,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["meets_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
