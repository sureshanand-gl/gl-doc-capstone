# LLMOps Pipeline

This project uses a lightweight, offline-first LLMOps workflow for Milestone 1 invoice
understanding. The goal is to keep prompt, schema, fallback, and evaluation behavior
reviewable without requiring live LLM secrets in CI.

## Assets

- Prompt registry: `prompts/registry.yaml`
- Versioned invoice prompt: `prompts/invoices/v1/system.md`
- Invoice field schema: `schemas/invoice_v1.json`
- Golden OCR-text examples: `data/golden/invoice_extraction_v1.jsonl`
- Eval runner: `scripts/run_golden_eval.py`
- Runtime metadata helpers: `llmops/registry.py`, `llmops/schema.py`, `llmops/tracing.py`,
  `llmops/metrics.py`

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
status, validation errors, fallback reason, and latency. This metadata lets reviewers
separate model behavior, schema validity, and fallback behavior during debugging.

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

These keys are allowed by `schemas/invoice_v1.json` so fallback behavior can be validated
without changing the core invoice field contract.

## Adding Prompt or Schema v2

1. Create `prompts/invoices/v2/system.md`.
2. Add a schema file, for example `schemas/invoice_v2.json`, before changing extraction
   behavior.
3. Register both in `prompts/registry.yaml` under `invoices.v2`.
4. Add or update golden examples in `data/golden/` with reviewed expected fields.
5. Add focused tests for registry resolution, schema validation, and extraction behavior.
6. Run the local gates and compare the golden eval report before replacing any baseline
   outputs.
7. Set `LLMOPS_PROMPT_VERSION=v2` and `LLMOPS_SCHEMA_VERSION=v2` only after tests and evals
   prove the contract is ready.
