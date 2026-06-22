import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import easyocr
import httpx
import numpy as np
import pypdfium2 as pdfium
from dotenv import load_dotenv
from openai import OpenAI

from llmops.local_extraction import extract_invoice_fields_local, parse_model_json_or_fallback
from llmops.registry import load_prompt_registry
from llmops.schema import validate_invoice_fields
from llmops.tracing import write_trace_record


class Milestone1NotebookAPI:
    """Backend API adapted from 03_milestone1_easyocr_only notebook logic."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.dataset_root = workspace_root / "Datasets"
        self.output_dir = workspace_root / "outputs"
        self.output_dir.mkdir(exist_ok=True)
        self.trace_path = self.output_dir / "llmops_traces.jsonl"

        self.easyocr_model_dir = workspace_root
        self.craft_model_path = self.easyocr_model_dir / "craft_mlt_25k.pth"
        self.english_model_path = self.easyocr_model_dir / "english_g2.pth"
        self.ocr_available = self.craft_model_path.exists() and self.english_model_path.exists()
        self.ocr_unavailable_reason: Optional[str] = None
        self.reader = None
        if not self.ocr_available:
            self.ocr_unavailable_reason = (
                "Required EasyOCR model files not found: craft_mlt_25k.pth and english_g2.pth"
            )
        else:
            self.reader = easyocr.Reader(
                ["en"],
                gpu=False,
                model_storage_directory=str(self.easyocr_model_dir),
                detect_network="craft",
                recog_network="english_g2",
                download_enabled=False,
                verbose=False,
            )

        env_file = workspace_root / ".env"
        load_dotenv(env_file)
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.openai_api_base = os.getenv(
            "OPENAI_API_BASE", "https://aibe.mygreatlearning.com/openai/v1"
        )
        self.model_name = "gpt-4o-mini"
        self.field_extractor_mode = os.getenv("FIELD_EXTRACTOR_MODE", "auto").strip().lower()
        if self.field_extractor_mode not in {"auto", "gpt", "qwen"}:
            self.field_extractor_mode = "auto"

        self.prompt_version = os.getenv("LLMOPS_PROMPT_VERSION", "v1").strip() or "v1"
        self.schema_version = os.getenv("LLMOPS_SCHEMA_VERSION", "v1").strip() or "v1"
        self.trace_include_text = os.getenv("LLMOPS_TRACE_TEXT", "false").strip().lower() == "true"

        self.prompt_registry = load_prompt_registry(workspace_root)
        self.invoice_prompt_entry = self.prompt_registry.get_invoice_entry(self.prompt_version)
        self.invoice_prompt = self.invoice_prompt_entry.prompt_path.read_text(encoding="utf-8")
        self.invoice_schema_path = self.invoice_prompt_entry.schema_path

        self.qwen_model_dir = workspace_root / "qwen3-vl-8b-instruct"
        self.qwen_model_name = "Qwen3-VL-8B-Instruct (local)"
        self._qwen_model = None
        self._qwen_processor = None
        self._qwen_device = "cpu"
        self._qwen_load_error: Optional[str] = None

        self.openai_client = None
        if self.openai_api_key:
            http_client = httpx.Client(verify=False)
            self.openai_client = OpenAI(
                api_key=self.openai_api_key,
                base_url=self.openai_api_base,
                http_client=http_client,
            )

    def quality_metrics(self, image_rgb: np.ndarray) -> Dict[str, float]:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))
        return {"blur_var": blur, "brightness": brightness, "contrast": contrast}

    def preprocess_for_ocr(self, image_rgb: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        thresholded = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            5,
        )
        return cv2.cvtColor(thresholded, cv2.COLOR_GRAY2RGB)

    def easyocr_on_image_array(self, image_rgb: np.ndarray) -> Tuple[str, float, int]:
        if self.reader is None:
            raise RuntimeError(self.ocr_unavailable_reason or "EasyOCR reader unavailable")
        results = self.reader.readtext(image_rgb)
        texts, confidences = [], []
        for item in results:
            if len(item) < 3:
                continue
            text = str(item[1]).strip()
            if not text:
                continue
            texts.append(text)
            confidences.append(float(item[2]))

        merged_text = "\n".join(texts)
        avg_confidence = float(sum(confidences) / len(confidences)) if confidences else 0.0
        return merged_text, avg_confidence, len(texts)

    def extract_fields_local(self, text: str) -> Dict[str, Any]:
        return extract_invoice_fields_local(text)

    def _local_with_reason(
        self,
        text: str,
        reason: str,
        detail: Optional[str] = None,
    ) -> Dict[str, Any]:
        fields = self.extract_fields_local(text)
        fields["fallback_reason"] = reason
        if detail:
            fields["fallback_detail"] = detail
        return fields

    def _parse_json_or_local(self, content: str, ocr_text: str, reason: str) -> Dict[str, Any]:
        parsed = parse_model_json_or_fallback(content, ocr_text, reason)
        validation_errors = validate_invoice_fields(parsed, self.invoice_schema_path)
        if validation_errors:
            return self._local_with_reason(
                ocr_text,
                f"{reason}_schema_invalid",
                "; ".join(validation_errors),
            )
        return parsed

    @staticmethod
    def is_policy_block(message: str) -> bool:
        markers = [
            "zscaler",
            "violates compliance category",
            "posting content to this website is not allowed",
            "<!doctype html",
            "dlp policy",
            "internet security by zscaler",
        ]
        lowered = message.lower()
        return any(marker in lowered for marker in markers)

    def extract_fields_gpt4omini(self, ocr_text: str) -> Dict[str, Any]:
        if self.openai_client is None:
            return self._local_with_reason(ocr_text, "gpt_unavailable_local")

        try:
            response = self.openai_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a precise invoice field extraction engine."},
                    {"role": "user", "content": f"{self.invoice_prompt}\n\nOCR_TEXT:\n{ocr_text}"},
                ],
                temperature=0,
                max_tokens=500,
            )

            content = (response.choices[0].message.content or "").strip()
            if self.is_policy_block(content):
                return self._local_with_reason(ocr_text, "gpt_policy_block_local")

            return self._parse_json_or_local(content, ocr_text, "gpt_parse_fallback_local")
        except Exception as exc:
            if self.is_policy_block(str(exc)):
                return self._local_with_reason(ocr_text, "gpt_policy_block_local")
            return self._local_with_reason(ocr_text, "gpt_error_local", str(exc))

    def _ensure_qwen_loaded(self) -> bool:
        if self._qwen_model is not None and self._qwen_processor is not None:
            return True
        if self._qwen_load_error is not None:
            return False
        if not self.qwen_model_dir.exists():
            self._qwen_load_error = f"Qwen model directory not found: {self.qwen_model_dir}"
            return False

        try:
            import torch
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"

            self._qwen_processor = AutoProcessor.from_pretrained(
                str(self.qwen_model_dir),
                local_files_only=True,
                trust_remote_code=True,
            )
            self._qwen_model = Qwen2VLForConditionalGeneration.from_pretrained(
                str(self.qwen_model_dir),
                local_files_only=True,
                trust_remote_code=True,
                torch_dtype="auto",
            )
            self._qwen_model.to(device)
            self._qwen_model.eval()
            self._qwen_device = device
            return True
        except Exception as exc:
            self._qwen_load_error = str(exc)
            return False

    def extract_fields_qwen(self, ocr_text: str) -> Dict[str, Any]:
        if not self._ensure_qwen_loaded():
            return self._local_with_reason(
                ocr_text,
                "qwen_unavailable_local",
                self._qwen_load_error,
            )

        try:
            import torch

            messages = [
                {"role": "system", "content": "You are a precise invoice field extraction engine."},
                {"role": "user", "content": f"{self.invoice_prompt}\n\nOCR_TEXT:\n{ocr_text}"},
            ]
            chat_text = self._qwen_processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            inputs = self._qwen_processor(text=[chat_text], padding=True, return_tensors="pt")
            inputs = {
                key: (value.to(self._qwen_device) if hasattr(value, "to") else value)
                for key, value in inputs.items()
            }

            with torch.inference_mode():
                generated_ids = self._qwen_model.generate(
                    **inputs,
                    max_new_tokens=500,
                    do_sample=False,
                    temperature=0.0,
                )

            trimmed_ids = [
                output_ids[len(input_ids) :]
                for input_ids, output_ids in zip(inputs["input_ids"], generated_ids)
            ]
            content = self._qwen_processor.batch_decode(
                trimmed_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0].strip()
            return self._parse_json_or_local(content, ocr_text, "qwen_parse_fallback_local")
        except Exception as exc:
            return self._local_with_reason(ocr_text, "qwen_error_local", str(exc))

    def _build_llmops_metadata(
        self,
        provider: str,
        model_name: str,
        fields: Dict[str, Any],
        started_at: float,
    ) -> Dict[str, Any]:
        validation_errors = validate_invoice_fields(fields, self.invoice_schema_path)
        return {
            "provider": provider,
            "model": model_name,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "validation_status": "valid" if not validation_errors else "invalid",
            "validation_errors": validation_errors,
            "fallback_reason": fields.get("fallback_reason"),
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        }

    def extract_fields_with_mode(self, ocr_text: str) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
        mode = self.field_extractor_mode
        started_at = time.perf_counter()

        if mode == "gpt":
            fields = self.extract_fields_gpt4omini(ocr_text)
            extraction_mode = "gpt-4o-mini"
            if fields.get("fallback_reason"):
                extraction_mode = "local_fallback_after_gpt"
            return (
                fields,
                extraction_mode,
                self._build_llmops_metadata("openai", extraction_mode, fields, started_at),
            )

        if mode == "qwen":
            fields = self.extract_fields_qwen(ocr_text)
            extraction_mode = "qwen-vl-local"
            if fields.get("fallback_reason"):
                extraction_mode = "local_fallback_after_qwen"
            return (
                fields,
                extraction_mode,
                self._build_llmops_metadata("qwen", extraction_mode, fields, started_at),
            )

        if self.openai_client is not None:
            gpt_fields = self.extract_fields_gpt4omini(ocr_text)
            if not gpt_fields.get("fallback_reason"):
                return (
                    gpt_fields,
                    "gpt-4o-mini",
                    self._build_llmops_metadata("openai", "gpt-4o-mini", gpt_fields, started_at),
                )

        qwen_fields = self.extract_fields_qwen(ocr_text)
        if not qwen_fields.get("fallback_reason"):
            return (
                qwen_fields,
                "qwen-vl-local",
                self._build_llmops_metadata("qwen", "qwen-vl-local", qwen_fields, started_at),
            )

        return (
            qwen_fields,
            "local_fallback",
            self._build_llmops_metadata("local", "local_fallback", qwen_fields, started_at),
        )

    def _record_trace(
        self,
        source_name: str,
        document_type: str,
        ocr_text: str,
        fields: Dict[str, Any],
        extraction_mode: str,
        llmops_metadata: Dict[str, Any],
    ) -> None:
        write_trace_record(
            trace_path=self.trace_path,
            include_text=self.trace_include_text,
            record={
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "source_name": source_name,
                "document_type": document_type,
                "ocr_text": ocr_text,
                "fields": fields,
                "extraction_mode": extraction_mode,
                "llmops": llmops_metadata,
            },
        )

    def _missing_ocr_result(self) -> Dict[str, Any]:
        return {
            "status": "error",
            "error_code": "ocr_models_missing",
            "error": self.ocr_unavailable_reason or "Required OCR models are unavailable",
        }

    def ocr_jpg_upload(self, uploaded_file) -> Dict[str, Any]:
        if not self.ocr_available:
            return self._missing_ocr_result()
        file_bytes = np.asarray(bytearray(uploaded_file.read()), dtype=np.uint8)
        bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if bgr is None:
            return {"status": "error", "error": "Image decode failed"}

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        metrics = self.quality_metrics(rgb)
        preprocessed = self.preprocess_for_ocr(rgb)
        text, confidence, detections = self.easyocr_on_image_array(preprocessed)
        fields, extraction_mode, llmops_metadata = self.extract_fields_with_mode(text)
        self._record_trace(uploaded_file.name, "jpg", text, fields, extraction_mode, llmops_metadata)

        return {
            "status": "success",
            "type": "jpg",
            "avg_confidence": confidence,
            "detections": detections,
            "quality": metrics,
            "text": text,
            "fields": fields,
            "extraction_mode": extraction_mode,
            "llmops": llmops_metadata,
        }

    def ocr_pdf_upload(self, uploaded_file) -> Dict[str, Any]:
        if not self.ocr_available:
            return self._missing_ocr_result()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = Path(tmp.name)

        doc = pdfium.PdfDocument(str(tmp_path))
        page_outputs: List[Dict[str, Any]] = []
        all_text: List[str] = []
        all_confidences: List[float] = []

        for index in range(len(doc)):
            arr = np.array(doc[index].render(scale=2.0).to_pil().convert("RGB"))
            preprocessed = self.preprocess_for_ocr(arr)
            text, confidence, detections = self.easyocr_on_image_array(preprocessed)
            page_outputs.append(
                {"page": index + 1, "avg_confidence": confidence, "detections": detections}
            )
            all_text.append(f"=== PAGE {index + 1} ===\n{text}")
            if detections > 0:
                all_confidences.append(confidence)

        merged = "\n\n".join(all_text)
        fields, extraction_mode, llmops_metadata = self.extract_fields_with_mode(merged)
        self._record_trace(uploaded_file.name, "pdf", merged, fields, extraction_mode, llmops_metadata)

        return {
            "status": "success",
            "type": "pdf",
            "pages": len(doc),
            "avg_confidence": (
                float(sum(all_confidences) / len(all_confidences)) if all_confidences else 0.0
            ),
            "page_stats": page_outputs,
            "text": merged,
            "fields": fields,
            "extraction_mode": extraction_mode,
            "llmops": llmops_metadata,
        }

    def ocr_docx_upload(self, uploaded_file) -> Dict[str, Any]:
        if not self.ocr_available:
            return self._missing_ocr_result()
        import io
        import zipfile

        import docx

        raw = uploaded_file.read()

        doc_obj = docx.Document(io.BytesIO(raw))
        native_parts: List[str] = []
        for para in doc_obj.paragraphs:
            text = para.text.strip()
            if text:
                native_parts.append(text)
        for table in doc_obj.tables:
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_cells:
                    native_parts.append(" | ".join(row_cells))
        native_text = "\n".join(native_parts)

        image_texts: List[str] = []
        image_stats: List[Dict[str, Any]] = []
        supported_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            media_files = [
                name
                for name in archive.namelist()
                if name.startswith("word/media/") and Path(name).suffix.lower() in supported_exts
            ]
            for index, media_name in enumerate(media_files):
                img_bytes = archive.read(media_name)
                arr = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
                if arr is None:
                    continue
                rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
                preprocessed = self.preprocess_for_ocr(rgb)
                text, confidence, detections = self.easyocr_on_image_array(preprocessed)
                image_texts.append(
                    f"=== EMBEDDED IMAGE {index + 1} ({Path(media_name).name}) ===\n{text}"
                )
                image_stats.append(
                    {
                        "image": Path(media_name).name,
                        "avg_confidence": confidence,
                        "detections": detections,
                    }
                )

        sections: List[str] = []
        if native_text.strip():
            sections.append(f"=== DOCUMENT TEXT ===\n{native_text}")
        sections.extend(image_texts)
        merged = "\n\n".join(sections) if sections else native_text

        fields, extraction_mode, llmops_metadata = self.extract_fields_with_mode(merged)
        self._record_trace(uploaded_file.name, "docx", merged, fields, extraction_mode, llmops_metadata)

        return {
            "status": "success",
            "type": "docx",
            "native_text_lines": len(native_parts),
            "embedded_images": len(media_files),
            "image_stats": image_stats,
            "text": merged,
            "fields": fields,
            "extraction_mode": extraction_mode,
            "llmops": llmops_metadata,
        }

    def process_upload(self, uploaded_file) -> Dict[str, Any]:
        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix == ".pdf":
            return self.ocr_pdf_upload(uploaded_file)
        if suffix == ".docx":
            return self.ocr_docx_upload(uploaded_file)
        return self.ocr_jpg_upload(uploaded_file)
