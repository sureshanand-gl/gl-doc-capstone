# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Milestone 2 invoice understanding Streamlit app.

- `app_backend.py`: OCR, layout-worker integration, provider routing, GPT/Qwen/local fallback, and field extraction.
- `app_frontend.py`: Streamlit upload UI, layout preview, and RAG chat. Keep workflow logic in backend or helpers.
- `app_frontend_helpers.py`: layout preview loaders/rendering and lightweight RAG helpers.
- `pyproject.toml` and `uv.lock`: Python dependency metadata and lockfile managed by uv.
- `prompts/`, `schemas/`, `data/golden/`, `scripts/`: thin LLMOps assets for prompt contracts, golden evals, and offline validation.
- `Datasets/`: sample invoices, PDFs, images, CSV labels, and DOCX examples. Treat as input data.
- `outputs/`: generated metrics, predictions, comparisons, diagrams, and experiment artifacts.

When adding LLMOps assets, mirror `D:\Workspace\Assignments\LLMOps`: use `prompts/<domain>/vN/` for versioned prompts, `schemas/` for JSON contracts, `data/golden/` for validated examples, and `tests/unit` or `tests/integration` for checks.

## Build, Test, and Development Commands

Use PowerShell from repository root:

```powershell
uv sync
```

Run the local app:

```powershell
uv run streamlit run app_frontend.py --server.headless true --server.port 8501
```

Quick validation:

```powershell
uv run python -m py_compile app_backend.py app_frontend.py
```

If LLMOps tooling is added, follow reference command intent: `ruff` for lint/format, `mypy` for typing, `pytest` for tests, and a lightweight eval script for golden-data regressions.

## Coding Style & Naming Conventions

Use Python 3.11+, 4-space indentation, snake_case functions and variables, and PascalCase classes such as `Milestone1NotebookAPI`. Keep output contracts stable: `status`, `type`, `text`, `fields`, `extraction_mode`, and fallback metadata. Layout metadata, page stats, and `llmops` metadata may be extended. New prompts and schemas must be versioned and reviewed together.

## LLMOps Workflow

Prefer contract-first changes. Define expected fields in schemas before changing extraction behavior. Track provider, model, prompt version, schema version, fallback reason, and eval result for extraction changes. Keep synthetic or anonymized data separate from raw customer documents.

## Testing Guidelines

Run `py_compile`, `pytest`, and the golden eval for every extraction-contract change. Manually test at least one image or PDF through Streamlit. For backend extraction changes, add focused tests under `tests/unit/` or `tests/integration/`, and compare against golden examples before updating `outputs/`.

## Commit & Pull Request Guidelines

No Git history is available in this checkout. Use concise imperative commit messages, for example `Add invoice schema v1` or `Track Qwen fallback reason`. Pull requests should include purpose, changed contracts, test/eval evidence, affected file types (`jpg`, `pdf`, `docx`), and screenshots for UI changes.

## Security & Configuration Tips

Do not commit `.env`, API keys, private model weights, or sensitive invoice data. `.env` may define `OPENAI_API_KEY`, `OPENAI_API_BASE`, `FIELD_EXTRACTOR_MODE=auto|gpt|qwen`, `LLMOPS_PROMPT_VERSION`, `LLMOPS_SCHEMA_VERSION`, and optional `LAYOUT_WORKER_PYTHON`. Keep `pyproject.toml` and `uv.lock` synchronized with imports and document any optional local model dependency.
