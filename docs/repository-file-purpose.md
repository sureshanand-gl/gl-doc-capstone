# Repository File Purpose Catalog

This catalog gives one-line purpose summary for every tracked file in repository. Generated and binary assets are documented here because many of them cannot safely carry inline comments.

## Root Files

- `.DS_Store` - macOS Finder metadata accidentally tracked; not used by application.
- `.dockerignore` - excludes local caches, models, datasets, and outputs from Docker build context.
- `.env.example` - example environment variables for app runtime, LLMOps, and observability stack.
- `.github/workflows/llmops-ci.yml` - GitHub Actions workflow for offline lint, compile, test, and eval gates.
- `.gitignore` - ignores local secrets, caches, screenshots, logs, and temp folders.
- `AGENTS.md` - repository-specific instructions for coding agents working in this checkout.
- `Dockerfile` - base container image for Streamlit invoice app and bundled runtime assets.
- `README.md` - primary project overview, setup, and workflow guide.
- `app_backend.py` - OCR and extraction backend coordinating providers, fallbacks, layout worker, and telemetry.
- `app_frontend.py` - Streamlit UI for upload, extraction review, layout preview, and RAG chat.
- `app_frontend_helpers.py` - helper functions for preview rendering and lightweight retrieval/chat flows.
- `cloudbuild.cloudrun.yaml` - Cloud Build recipe for building per-service Cloud Run runtime images.
- `docker-compose.yml` - local multi-container stack for app, Prometheus, Pushgateway, and Grafana.
- `pyproject.toml` - Python project metadata plus uv, pytest, and Ruff configuration.
- `uv.lock` - locked dependency graph for reproducible `uv sync` installs.

## Configuration and Documentation

- `configs/model_pricing.yaml` - token pricing table used to estimate live pipeline cost.
- `docs/llmops.md` - detailed explanation of offline and live LLMOps workflow and artifacts.
- `docs/repository-file-purpose.md` - tracked-file purpose catalog for repository.
- `docs/repository-folder-purpose.md` - folder purpose map for repository.

## Prompt and Schema Assets

- `prompts/registry.yaml` - registry mapping invoice prompt versions to prompt and schema files.
- `prompts/invoices/v1/system.md` - first invoice extraction system prompt.
- `prompts/invoices/v2/system.md` - current invoice extraction system prompt with richer contract guidance.
- `schemas/invoice_v1.json` - JSON schema for invoice extraction contract version 1.
- `schemas/invoice_v2.json` - JSON schema for invoice extraction contract version 2.

## Golden Data

- `data/golden/invoice_extraction_v1.jsonl` - golden OCR-text dataset aligned to schema v1.
- `data/golden/invoice_extraction_v2.jsonl` - golden OCR-text dataset aligned to schema v2.

## LLMOps Python Package

- `llmops/__init__.py` - package marker and short package description.
- `llmops/live_provider.py` - OpenAI-compatible live extractor and normalized result dataclass.
- `llmops/local_extraction.py` - offline regex extraction and field normalization helpers.
- `llmops/metrics.py` - accuracy scoring and eval report aggregation helpers.
- `llmops/pipeline.py` - live golden-eval orchestration over provider calls and scoring.
- `llmops/registry.py` - prompt registry loader and typed prompt entry model.
- `llmops/reporting.py` - report, HTML, chart, and Mermaid artifact writers.
- `llmops/schema.py` - JSON-schema validation helpers for extracted fields.
- `llmops/telemetry.py` - Prometheus metric publication and token-cost accounting helpers.
- `llmops/tracing.py` - NDJSON trace writer with OCR-text redaction support.

## Scripts

- `scripts/deploy_cloud_run.ps1` - PowerShell deploy helper for Cloud Build plus per-service Cloud Run deployment.
- `scripts/deploy_cloud_run.sh` - Bash deploy helper for Cloud Build plus per-service Cloud Run deployment.
- `scripts/layout_worker.py` - lightweight OpenCV sidecar for coarse page region detection.
- `scripts/run_golden_eval.py` - offline eval runner for local extraction against golden dataset.
- `scripts/run_llmops_pipeline.py` - live eval runner for prompt, schema, accuracy, and telemetry verification.

## Deployment and Monitoring Assets

- `docker/cloudrun/app/default.conf.template` - nginx route template exposing Streamlit UI and Prometheus `/metrics` from one app service URL.
- `docker/cloudrun/app/entrypoint.sh` - app-container startup wrapper that launches internal Streamlit plus nginx on public port 8501.
- `docker/cloudrun/grafana/Dockerfile` - Cloud Run Grafana image layering repo provisioning plus datasource templating onto base image.
- `docker/cloudrun/grafana/entrypoint.sh` - Grafana startup wrapper that renders datasource config from deployed Prometheus URL.
- `docker/cloudrun/grafana/provisioning/dashboards/dashboards.yml` - dashboard provider config for Cloud Run Grafana.
- `docker/cloudrun/grafana/provisioning/datasources/prometheus.yml` - legacy static Cloud Run datasource config retained from sidecar topology.
- `docker/cloudrun/grafana/provisioning/datasources/prometheus.yml.tmpl` - runtime datasource template for direct Prometheus Cloud Run URL.
- `docker/cloudrun/prometheus/Dockerfile` - Cloud Run Prometheus image packaging runtime scrape template plus entrypoint.
- `docker/cloudrun/prometheus/entrypoint.sh` - Prometheus startup wrapper that renders scrape config from deployed service hosts.
- `docker/cloudrun/prometheus/prometheus.yml` - legacy static Cloud Run scrape config retained from sidecar topology.
- `docker/cloudrun/prometheus/prometheus.yml.tmpl` - runtime scrape template for direct app and Pushgateway Cloud Run services.
- `docker/cloudrun/proxy/Dockerfile` - deprecated nginx proxy image from earlier Cloud Run front-door topology.
- `docker/cloudrun/proxy/default.conf.template` - deprecated nginx route template from earlier Cloud Run proxy topology.
- `docker/cloudrun/proxy/entrypoint.sh` - deprecated nginx startup wrapper from earlier Cloud Run proxy topology.
- `docker/grafana/dashboards/llmops-cost-dashboard.json` - checked-in Grafana dashboard for cost and pipeline monitoring.
- `docker/grafana/provisioning/dashboards/dashboards.yml` - dashboard provider config for local Grafana.
- `docker/grafana/provisioning/datasources/prometheus.yml` - datasource config for local Grafana.
- `docker/prometheus/prometheus.yml` - scrape config for local docker-compose Prometheus.

## Model Assets

- `models/easyocr/.gitkeep` - keeps EasyOCR model directory tracked when weights are absent.
- `models/easyocr/craft_mlt_25k.pth` - EasyOCR CRAFT detection model required by OCR runtime.
- `models/easyocr/english_g2.pth` - EasyOCR English recognition model required by OCR runtime.
- `models/qwen/.gitkeep` - keeps optional local Qwen model directory tracked.

## Tests

- `tests/unit/test_app_backend_runtime.py` - runtime regression tests for degraded-mode backend behavior.
- `tests/unit/test_cloud_run_deploy_assets.py` - deploy asset tests for local Docker and Cloud Run packaging.
- `tests/unit/test_frontend_helpers.py` - unit tests for preview and retrieval helper functions.
- `tests/unit/test_llmops_assets.py` - tests validating prompt registry, schemas, and golden assets stay aligned.
- `tests/unit/test_llmops_extraction.py` - tests for local extraction fallback behavior and parsed fields.
- `tests/unit/test_llmops_live_pipeline.py` - tests for live provider extraction, config loading, and artifact writing.
- `tests/unit/test_llmops_metrics.py` - tests for accuracy scoring, report aggregation, and trace output.
- `tests/unit/test_llmops_telemetry.py` - tests for pricing lookup, usage normalization, and metric publication.

## Generated Outputs and Evidence

- `outputs/batch1_gpt_qwen_gt_comparison.csv` - side-by-side comparison of GPT, Qwen, and ground-truth outputs.
- `outputs/batch1_gpt_qwen_gt_summary.json` - summary statistics for GPT and Qwen comparison batch.
- `outputs/batch1_pdf_predictions_gpt4o.csv` - GPT-4o prediction export for PDF batch.
- `outputs/batch1_pdf_predictions_qwen_local.csv` - local Qwen prediction export for PDF batch.
- `outputs/document_ocr_pipeline.png` - visual diagram of OCR/document processing pipeline.
- `outputs/llmops-changes-summary.pptx` - presentation summarizing LLMOps changes and outcomes.
- `outputs/llmops-changes-summary.pptx.inspect.ndjson` - inspection artifact describing presentation structure/content.
- `outputs/llmops/field_accuracy_chart.png` - chart of per-document or aggregate extraction accuracy from live eval.
- `outputs/llmops/live_eval_report.html` - human-readable live LLMOps evaluation report.
- `outputs/llmops/live_eval_report.json` - machine-readable live LLMOps evaluation report.
- `outputs/llmops/pipeline_dag.mmd` - Mermaid source for live pipeline DAG diagram.
- `outputs/llmops/provider_latency_chart.png` - latency chart produced by live eval reporting.
- `outputs/llmops_eval_report.json` - offline golden-eval summary report.
- `outputs/llmops_eval_report_chart.png` - chart rendering of offline eval results.
- `outputs/milestone1_data_profile.csv` - profiling summary for milestone dataset columns/documents.
- `outputs/milestone1_easyocr_sample_results.jsonl` - sample EasyOCR extraction rows for milestone evidence.
- `outputs/milestone1_easyocr_sample_summary.json` - summary stats for EasyOCR sample run.
- `outputs/milestone1_progress_artifacts.json` - manifest of milestone progress artifacts.
- `outputs/milestone1_validation_metrics.csv` - milestone validation metrics export.
- `outputs/milestone1_workflow_architecture.mmd` - Mermaid source for milestone workflow architecture.
