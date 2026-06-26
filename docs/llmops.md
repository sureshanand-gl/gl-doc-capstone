# LLMOps Pipeline

This project uses a lightweight, offline-first LLMOps workflow for Milestone 2 invoice
understanding. The goal is to keep prompt, schema, fallback, and evaluation behavior
reviewable without requiring live LLM secrets in CI.

## Assets

- Prompt registry: `prompts/registry.yaml`
- Versioned invoice prompt: `prompts/invoices/v2/system.md`
- Invoice field schema: `schemas/invoice_v2.json`
- Golden OCR-text examples: `data/golden/invoice_extraction_v2.jsonl`
- Eval runner: `scripts/run_golden_eval.py`
- Live LLMOps runner: `scripts/run_llmops_pipeline.py`
- Runtime metadata helpers: `llmops/registry.py`, `llmops/schema.py`, `llmops/tracing.py`,
  `llmops/metrics.py`, `llmops/live_provider.py`, `llmops/pipeline.py`,
  `llmops/reporting.py`

## Local Gates

Run the same offline gates used by GitHub Actions:

```bash
uv sync --locked --dev
uv run ruff check .
uv run python -m py_compile app_backend.py app_frontend.py llmops/schema.py llmops/metrics.py llmops/registry.py llmops/tracing.py scripts/run_golden_eval.py
uv run pytest -q
uv run python scripts/run_golden_eval.py --min-field-accuracy 0.80 --output-path outputs/llmops_eval_report.json
```

The golden eval exits with code `0` when average field accuracy meets the threshold and
code `1` when it does not. The report is written to `outputs/llmops_eval_report.json`
by default, or to the path passed with `--output-path`.

## Live LLMOps Pipeline

The live pipeline calls the configured OpenAI-compatible provider once per golden
example, validates strict JSON against the registered invoice schema, scores field
accuracy, and writes graph/report artifacts.

```bash
uv run python scripts/run_llmops_pipeline.py `
  --dataset data/golden/invoice_extraction_v2.jsonl `
  --output-dir outputs/llmops `
  --model gpt-4o-mini `
  --min-field-accuracy 0.80
```

Required environment:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE` optional; defaults to `https://aibe.mygreatlearning.com/openai/v1`
- `PROMETHEUS_PUSHGATEWAY_URL` optional; when set, pipeline publishes per-run summary gauges
- `LLMOPS_PRICING_FILE` optional; defaults to `configs/model_pricing.yaml`

The CLI loads `.env` by default for local runs. Use `--no-dotenv` when you want to
validate only process-level environment variables, such as CI secret checks.
For local setup, copy `.env.example` to `.env`. Repository validation should not depend on
`.env` being present.

Artifacts:

- `outputs/llmops/live_eval_report.json`
- `outputs/llmops/live_eval_report.html`
- `outputs/llmops/field_accuracy_chart.png`
- `outputs/llmops/provider_latency_chart.png`
- `outputs/llmops/pipeline_dag.mmd`
- `outputs/llmops/pipeline_dag.png` when Mermaid CLI (`mmdc`) is installed

Report payloads now include:

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `cost_usd`
- `usage_source`
- aggregate token/cost totals in `live_eval_report.json`

Malformed LLM JSON and schema-invalid responses are recorded as invalid live model
results. The live evaluator does not score local regex fallback as live model success.

## Docker + Monitoring

`docker-compose.yml` runs four services:

- `app`: Streamlit runtime plus in-process Prometheus exporter
- `prometheus`: scrapes `app:9108/metrics` and Pushgateway
- `pushgateway`: receives batch pipeline summary gauges
- `grafana`: pre-provisioned Prometheus datasource and LLMOps cost dashboard

Start stack:

```bash
docker compose up --build
```

Ports:

- app `8501`
- Prometheus `9090`
- Pushgateway `9091`
- Grafana `3000`

Compose sets:

- `PROMETHEUS_METRICS_PORT=9108`
- `PROMETHEUS_PUSHGATEWAY_URL=http://pushgateway:9091`
- `LLMOPS_PRICING_FILE=/app/configs/model_pricing.yaml`
- `EASYOCR_MODEL_DIR=/models/easyocr`
- `QWEN_MODEL_DIR=/models/qwen`

Billing note: cost metrics are only populated when provider response includes token
usage. Local fallback and Qwen requests remain visible in request/latency metrics but
stay non-billable with `usage_source="unavailable"`.
`docker compose config` should work even when `.env` is absent because service env
variables have compose-level defaults and no required `env_file` reference remains.

## GitHub Actions

`.github/workflows/llmops-ci.yml` runs on pushes to `main`, pull requests, and manual
dispatch. It stays offline-only:

- no `OPENAI_API_KEY`
- no live GPT call
- no Qwen model download
- no EasyOCR model-weight requirement

The job installs the locked uv environment, runs lint, compiles Python modules, runs unit
tests, runs the offline golden eval at `0.80` minimum field accuracy, and uploads
`outputs/llmops_eval_report.json` as the `llmops-eval-report` artifact.

The job then requires the `OPENAI_API_KEY` secret and runs the live LLMOps pipeline.
`OPENAI_API_BASE` is optional. Live artifacts are uploaded as `live-llmops-report`.
Per the project policy, provider outage, fork PR secret restrictions, or missing secrets
can fail the workflow.

## Runtime Metadata

Successful extraction responses keep the stable public result keys:

```json
{
  "status": "success",
  "type": "jpg|pdf|docx",
  "text": "...",
  "fields": {},
  "extraction_mode": "gpt-4o-mini|qwen-vl-local|local_fallback",
  "llmops": {}
}
```

The `llmops` object tracks provider, model, prompt version, schema version, validation
status, validation errors, fallback reason, latency, token usage, computed cost, and
surface. Live eval rows also track `scalar_field_accuracy` and `order_item_field_accuracy`
so nested line-item regressions do not disappear inside one top-level score.

## Trace Privacy

Runtime traces are written to `outputs/llmops_traces.jsonl`. Raw OCR text is redacted by
default. Set `LLMOPS_TRACE_TEXT=true` only for local debugging with non-sensitive or
anonymized documents. Do not commit raw customer documents, traces with OCR text, `.env`
files, API keys, or private model weights.

## Fallback Metadata

Provider failures intentionally preserve a usable extraction result through local fallback.
Fallback fields may include:

- `fallback_reason`: machine-readable reason such as `gpt_unavailable_local`,
  `gpt_policy_block_local`, `qwen_unavailable_local`, or `gpt_parse_fallback_local`
- `fallback_detail`: optional diagnostic detail, such as schema validation errors or a
  provider exception

These keys are allowed by `schemas/invoice_v2.json` so fallback behavior can be validated
without changing the core invoice field contract.

## Current Contract Notes

- `order_items` is scored per row and per field using flattened paths such as
  `order_items[0].description`.
- Offline and live reports now emit overall field accuracy plus scalar-only and line-item-only accuracy.
- Default local contract is `v2`; set `LLMOPS_PROMPT_VERSION` and `LLMOPS_SCHEMA_VERSION`
  explicitly only when testing another registered version.
