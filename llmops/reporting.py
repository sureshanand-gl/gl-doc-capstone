import html
import json
import shutil
import subprocess
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


PIPELINE_DAG = """graph TD
    A[Golden OCR dataset] --> B[Prompt registry]
    B --> C[OpenAI-compatible live call]
    C --> D[Strict JSON parse]
    D --> E[Schema validation]
    E --> F[Field accuracy scoring]
    F --> G[JSON report]
    F --> H[PNG charts]
    F --> I[HTML report]
"""


def _average_accuracy(rows: list[dict[str, Any]]) -> float:
    return round(mean(row["field_accuracy"] for row in rows), 4) if rows else 0.0


def _average_latency(rows: list[dict[str, Any]]) -> float:
    return round(mean(row["latency_ms"] for row in rows), 2) if rows else 0.0


def build_live_eval_report(
    rows: list[dict[str, Any]],
    min_field_accuracy: float,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any]:
    average_accuracy = _average_accuracy(rows)
    return {
        "documents": len(rows),
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "minimum_field_accuracy": min_field_accuracy,
        "average_field_accuracy": average_accuracy,
        "average_scalar_field_accuracy": round(
            mean(row["scalar_field_accuracy"] for row in rows),
            4,
        ) if rows else 0.0,
        "average_order_item_field_accuracy": round(
            mean(row["order_item_field_accuracy"] for row in rows),
            4,
        ) if rows else 0.0,
        "average_latency_ms": _average_latency(rows),
        "meets_threshold": average_accuracy >= min_field_accuracy,
        "invalid_documents": sum(1 for row in rows if row["validation_status"] != "valid"),
        "fallback_documents": sum(1 for row in rows if row.get("fallback_reason")),
        "results": rows,
    }


def _write_field_accuracy_chart(rows: list[dict[str, Any]], output_path: Path) -> None:
    labels = [row["document_id"] for row in rows] or ["no-documents"]
    values = [row["field_accuracy"] for row in rows] or [0.0]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, values, color="#2f6f73")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Field accuracy")
    ax.set_title("Live LLM field accuracy by document")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def _write_latency_chart(rows: list[dict[str, Any]], output_path: Path) -> None:
    labels = [row["document_id"] for row in rows] or ["no-documents"]
    values = [row["latency_ms"] for row in rows] or [0.0]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, values, color="#8a5a44")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Live provider latency by document")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def _render_status(value: Any) -> str:
    return "" if value is None else html.escape(str(value))


def _write_html_report(report: dict[str, Any], output_path: Path) -> None:
    rows_html = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['document_id'])}</td>"
        f"<td>{html.escape(row['source_name'])}</td>"
        f"<td>{html.escape(row['provider'])}</td>"
        f"<td>{html.escape(row['model'])}</td>"
        f"<td>{row['field_accuracy']:.2%}</td>"
        f"<td>{row['scalar_field_accuracy']:.2%}</td>"
        f"<td>{row['order_item_field_accuracy']:.2%}</td>"
        f"<td>{row['latency_ms']:.2f}</td>"
        f"<td>{html.escape(row['validation_status'])}</td>"
        f"<td>{_render_status(row.get('fallback_reason'))}</td>"
        f"<td>{html.escape(', '.join(row['missing_fields']))}</td>"
        "</tr>"
        for row in report["results"]
    )
    error_items = "\n".join(
        f"<li><strong>{html.escape(row['document_id'])}</strong>: "
        f"{html.escape('; '.join(row.get('validation_errors') or []))}</li>"
        for row in report["results"]
        if row.get("validation_errors")
    )
    if not error_items:
        error_items = "<li>No validation or provider errors recorded.</li>"

    output_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Live LLMOps Evaluation Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }}
    .cards {{ display: grid; grid-template-columns: repeat(6, minmax(140px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d8dee4; border-radius: 6px; padding: 14px; }}
    .metric {{ font-size: 28px; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border: 1px solid #d8dee4; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f8fa; }}
    img {{ max-width: 100%; border: 1px solid #d8dee4; border-radius: 6px; }}
    pre {{ background: #f6f8fa; padding: 16px; overflow-x: auto; }}
  </style>
</head>
<body>
  <h1>Live LLMOps Evaluation Report</h1>
  <div class="cards">
    <div class="card"><div>Documents</div><div class="metric">{report['documents']}</div></div>
    <div class="card"><div>Average Accuracy</div><div class="metric">{report['average_field_accuracy']:.2%}</div></div>
    <div class="card"><div>Scalar Accuracy</div><div class="metric">{report['average_scalar_field_accuracy']:.2%}</div></div>
    <div class="card"><div>Line Item Accuracy</div><div class="metric">{report['average_order_item_field_accuracy']:.2%}</div></div>
    <div class="card"><div>Average Latency</div><div class="metric">{report['average_latency_ms']:.2f} ms</div></div>
    <div class="card"><div>Meets Threshold</div><div class="metric">{report['meets_threshold']}</div></div>
  </div>
  <h2>Charts</h2>
  <p><img src="field_accuracy_chart.png" alt="Field accuracy chart"></p>
  <p><img src="provider_latency_chart.png" alt="Provider latency chart"></p>
  <h2>Document Results</h2>
  <table>
    <thead>
      <tr>
        <th>Document</th><th>Source</th><th>Provider</th><th>Model</th>
        <th>Accuracy</th><th>Scalar</th><th>Line Items</th><th>Latency ms</th><th>Validation</th><th>Fallback</th>
        <th>Missing Fields</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <h2>Fallbacks and Errors</h2>
  <ul>{error_items}</ul>
  <h2>Pipeline DAG</h2>
  <pre>{html.escape(PIPELINE_DAG)}</pre>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_pipeline_dag(output_dir: Path) -> None:
    mmd_path = output_dir / "pipeline_dag.mmd"
    mmd_path.write_text(PIPELINE_DAG, encoding="utf-8")

    mmdc = shutil.which("mmdc")
    if not mmdc:
        return
    subprocess.run(
        [mmdc, "-i", str(mmd_path), "-o", str(output_dir / "pipeline_dag.png")],
        check=False,
        capture_output=True,
        text=True,
    )


def write_llmops_artifacts(
    rows: list[dict[str, Any]],
    output_dir: Path,
    min_field_accuracy: float,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_live_eval_report(
        rows=rows,
        min_field_accuracy=min_field_accuracy,
        prompt_version=prompt_version,
        schema_version=schema_version,
    )

    (output_dir / "live_eval_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    _write_field_accuracy_chart(rows, output_dir / "field_accuracy_chart.png")
    _write_latency_chart(rows, output_dir / "provider_latency_chart.png")
    _write_pipeline_dag(output_dir)
    _write_html_report(report, output_dir / "live_eval_report.html")
    return report
