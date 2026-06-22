# Milestone 1 Invoice Understanding (EasyOCR + GPT-4o-mini)

[![LLMOps CI](https://github.com/sureshanand-gl/gl-doc-capstone/actions/workflows/llmops-ci.yml/badge.svg)](https://github.com/sureshanand-gl/gl-doc-capstone/actions/workflows/llmops-ci.yml)

This project implements Milestone 1 of the invoice understanding pipeline:
- OCR on JPG/PDF/DOCX inputs using local EasyOCR models
- Field extraction using GPT-4o-mini
- Automatic local fallback extraction when API access is blocked by policy/network
- Thin LLMOps layer for prompt versioning, schema validation, golden evals, and trace metadata
- Streamlit UI for upload and results

## 1. Main Files

- `03_milestone1_easyocr_only.ipynb`:
	Milestone 1 research and baseline notebook (data discovery, preprocessing, OCR baseline, validation artifacts).
- `app_backend.py`:
	Production-style backend module derived from notebook logic.
- `app_frontend.py`:
	Streamlit UI layer that calls backend methods only.
- `prompts/invoices/v1/system.md`, `prompts/registry.yaml`:
	Versioned extraction prompt and registry.
- `schemas/invoice_v1.json`:
	JSON contract for extracted invoice fields.
- `data/golden/invoice_extraction_v1.jsonl`:
	Validated OCR-text golden examples for offline evals.
- `scripts/run_golden_eval.py`:
	Deterministic offline golden-data evaluation script.
- `scripts/run_llmops_pipeline.py`:
	Live OpenAI-compatible LLMOps pipeline that generates JSON, HTML, PNG charts, and
	a Mermaid pipeline DAG from golden examples.
- `docs/llmops.md`:
	LLMOps pipeline guide covering local gates, CI, prompt/schema versioning, trace privacy, and fallback metadata.
- `craft_mlt_25k.pth`, `english_g2.pth`:
	Required local EasyOCR model files.

## 2. Environment Setup

This project uses `uv` for dependency management. From this folder:

```bash
uv sync
```

For local Qwen mode, install the optional heavy model dependencies:

```bash
uv sync --extra qwen
```

Create a `.env` file in this folder:

```env
OPENAI_API_KEY=your_key_here
OPENAI_API_BASE=https://aibe.mygreatlearning.com/openai/v1
FIELD_EXTRACTOR_MODE=auto
LLMOPS_PROMPT_VERSION=v1
LLMOPS_SCHEMA_VERSION=v1
LLMOPS_TRACE_TEXT=false
```

Notes:
- `OPENAI_API_KEY` is required for GPT extraction.
- If GPT endpoint is blocked by corporate DLP/Zscaler, the app falls back automatically to local extraction.

## 3. Run the App

From this folder:

```bash
uv run streamlit run app_frontend.py --server.headless true --server.port 8501
```

Then upload any `jpg`, `jpeg`, `png`, `pdf`, or `docx` file.

## 4. Quick Validation

```bash
uv run ruff check .
uv run python -m py_compile app_backend.py app_frontend.py
uv run pytest -q
```

Offline eval:

```bash
uv run python scripts/run_golden_eval.py --min-field-accuracy 0.80 --output-path outputs/llmops_eval_report.json
```

Live LLMOps report with provider calls:

```bash
uv run python scripts/run_llmops_pipeline.py --dataset data/golden/invoice_extraction_v1.jsonl --output-dir outputs/llmops --model gpt-4o-mini --min-field-accuracy 0.80
```

This writes `outputs/llmops/live_eval_report.json`, `live_eval_report.html`,
field accuracy and latency PNG charts, and `pipeline_dag.mmd`. `OPENAI_API_KEY` is
required. `OPENAI_API_BASE` is optional and defaults to the configured OpenAI-compatible
endpoint.

For the full local and GitHub Actions LLMOps workflow, see `docs/llmops.md`.

## 5. Code Logic in `03_milestone1_easyocr_only.ipynb`

The notebook builds the Milestone 1 baseline pipeline in stages:

1. Imports and workspace/model path setup
2. EasyOCR initialization with local models only (`download_enabled=False`)
3. Recursive dataset file discovery under `Datasets/`
4. Image quality and preprocessing utilities:
	 - quality metrics: blur variance, brightness, contrast
	 - preprocessing: grayscale -> denoise -> adaptive threshold -> RGB
5. OCR functions for JPG and PDF:
	 - JPG: read image with OpenCV, preprocess, OCR
	 - PDF: render pages with `pypdfium2`, preprocess each page, OCR and merge text
6. Baseline field extraction using rule/pattern matching
7. Baseline experiment on sample documents and output artifact generation in `outputs/`
8. Validation/retrieval baseline metrics for Milestone 1 reporting

## 6. Code Logic in `app_backend.py`

`Milestone1NotebookAPI` is the backend API class used by Streamlit.

### 6.1 Initialization (`__init__`)
- Validates required EasyOCR model files exist.
- Initializes EasyOCR reader with:
	- detector: `craft`
	- recognizer: `english_g2`
	- `gpu=False`, local model storage, no downloads
- Loads `.env` values and configures OpenAI client (`httpx` based).

### 6.2 OCR Utilities
- `quality_metrics(image_rgb)`: computes blur/brightness/contrast.
- `preprocess_for_ocr(image_rgb)`: denoise + adaptive threshold for stronger OCR.
- `easyocr_on_image_array(image_rgb)`: returns
	- merged text
	- average confidence
	- number of detections

### 6.3 Field Extraction
- `extract_fields_gpt4omini(ocr_text)`:
	- Sends OCR text to GPT-4o-mini for strict JSON extraction.
	- Expected keys: `invoice_number`, `invoice_date`, `due_date`, `total`, `tax`, `vendor_name`, `customer_name`, `currency`.
- `extract_fields_local(text)`:
	- Local baseline extractor shared with golden eval script.
- Prompt/schema loading:
	- Loads invoice prompt from `prompts/invoices/v1/system.md`.
	- Validates extracted fields against `schemas/invoice_v1.json`.
- `is_policy_block(message)`:
	- Detects Zscaler/DLP policy-block responses.

### 6.4 File-Type Processing
- `ocr_jpg_upload(uploaded_file)`:
	decode -> preprocess -> OCR -> field extraction.
- `ocr_pdf_upload(uploaded_file)`:
	render all pages -> preprocess -> OCR per page -> merge text -> field extraction.
- `ocr_docx_upload(uploaded_file)`:
	- Extracts native DOCX text (paragraphs/tables) using `python-docx`.
	- Extracts embedded images from `word/media/*` and OCRs each image.
	- Merges native text + embedded image OCR text, then runs field extraction.
- `process_upload(uploaded_file)`:
	Routes by extension: `.pdf`, `.docx`, otherwise image route.

## 7. UI Flow in `app_frontend.py`

1. Create `Milestone1NotebookAPI(ROOT)` instance.
2. Accept upload via Streamlit file uploader.
3. On button click, call `api.process_upload(uploaded)`.
4. Show:
	 - extraction mode (`gpt4o-mini` or `local_fallback`)
	 - OCR text
	 - extracted JSON fields
	 - `llmops` metadata (`provider`, `model`, `prompt_version`, `schema_version`, `validation_status`, `fallback_reason`, `latency_ms`)

## 8. Output Structure (Typical)

```json
{
	"status": "success",
	"type": "jpg|pdf|docx",
	"avg_confidence": 0.0,
	"text": "...",
	"fields": {
		"invoice_number": null,
		"invoice_date": null,
		"due_date": null,
		"total": null,
		"tax": null,
		"vendor_name": null,
		"customer_name": null,
		"currency": null
	}
}
```

Each successful result now also includes:

```json
{
	"llmops": {
		"provider": "openai|qwen|local",
		"model": "gpt-4o-mini|qwen-vl-local|local_fallback",
		"prompt_version": "v1",
		"schema_version": "v1",
		"validation_status": "valid|invalid",
		"fallback_reason": null,
		"latency_ms": 0.0
	}
}
```

## 9. Milestone 1 Scope and Known Limits

- Current baseline extraction is strong for OCR text capture, but field coverage depends on invoice format variability.
- GPT extraction may be blocked by enterprise policy; local fallback ensures app continuity.
- Layout model integration is not yet part of this module and can be added in Milestone 2.
