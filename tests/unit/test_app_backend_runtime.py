from pathlib import Path

import numpy as np

from app_backend import Milestone1NotebookAPI


class DummyUpload:
    def __init__(self, name: str, payload: bytes):
        self.name = name
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def test_backend_initializes_in_degraded_mode_without_ocr_models(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("easyocr.Reader should not initialize when model files are missing")

    monkeypatch.setattr("app_backend.easyocr.Reader", fail_if_called)

    api = Milestone1NotebookAPI(repo_root)

    assert api.ocr_available is False
    assert "craft_mlt_25k.pth" in api.ocr_unavailable_reason


def test_process_upload_returns_error_when_ocr_models_missing(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("easyocr.Reader should not initialize when model files are missing")

    monkeypatch.setattr("app_backend.easyocr.Reader", fail_if_called)

    api = Milestone1NotebookAPI(repo_root)
    result = api.process_upload(DummyUpload("sample.jpg", b"fake-bytes"))

    assert result["status"] == "error"
    assert result["error_code"] == "ocr_models_missing"
    assert "craft_mlt_25k.pth" in result["error"]


def test_backend_initializes_in_degraded_mode_when_cv2_missing(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr("app_backend.cv2", None)

    api = Milestone1NotebookAPI(repo_root)

    assert api.ocr_available is False
    assert api.ocr_error_code == "ocr_runtime_missing"
    assert "cv2" in api.ocr_unavailable_reason


def test_process_upload_returns_runtime_error_when_cv2_missing(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr("app_backend.cv2", None)

    api = Milestone1NotebookAPI(repo_root)
    result = api.process_upload(DummyUpload("sample.jpg", b"fake-bytes"))

    assert result["status"] == "error"
    assert result["error_code"] == "ocr_runtime_missing"
    assert "cv2" in result["error"]


def test_layout_worker_resolution_prefers_explicit_env_override(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LAYOUT_WORKER_PYTHON", r"C:\layout\python.exe")

    api = Milestone1NotebookAPI(repo_root)

    assert str(api.layout_worker_python) == r"C:\layout\python.exe"


def test_model_path_resolution_prefers_env_overrides(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("EASYOCR_MODEL_DIR", r"C:\models\easyocr")
    monkeypatch.setenv("QWEN_MODEL_DIR", r"C:\models\qwen")

    api = Milestone1NotebookAPI(repo_root)

    assert str(api.easyocr_model_dir) == r"C:\models\easyocr"
    assert str(api.qwen_model_dir) == r"C:\models\qwen"


def test_cross_platform_absolute_path_detection_handles_windows_and_posix_forms():
    assert Milestone1NotebookAPI._is_cross_platform_absolute(r"C:\models\easyocr") is True
    assert Milestone1NotebookAPI._is_cross_platform_absolute("/models/easyocr") is True
    assert Milestone1NotebookAPI._is_cross_platform_absolute("models/easyocr") is False


def test_build_llmops_metadata_includes_usage_fields(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    api = Milestone1NotebookAPI(repo_root)

    metadata = api._build_llmops_metadata(
        provider="openai",
        model_name="gpt-4o-mini",
        fields={"invoice_number": None, "order_items": []},
        started_at=0.0,
        usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        cost_usd=0.0000135,
        usage_source="provider",
        surface="streamlit_app",
    )

    assert metadata["prompt_tokens"] == 50
    assert metadata["completion_tokens"] == 10
    assert metadata["total_tokens"] == 60
    assert metadata["cost_usd"] == 0.0000135
    assert metadata["usage_source"] == "provider"
    assert metadata["surface"] == "streamlit_app"


def test_layout_aware_ocr_merges_region_text_and_summary(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    api = Milestone1NotebookAPI(repo_root)

    monkeypatch.setattr(api, "preprocess_for_ocr", lambda image_rgb: image_rgb)

    calls = {"count": 0}

    def fake_easyocr(image_rgb):
        calls["count"] += 1
        if calls["count"] == 1:
            return "FULL PAGE", 0.8, 2
        return "REGION TEXT", 0.9, 1

    monkeypatch.setattr(api, "easyocr_on_image_array", fake_easyocr)
    monkeypatch.setattr(
        api,
        "detect_layout_regions",
        lambda image_rgb: {
            "status": "ready",
            "regions": [{"bbox": [0, 0, 20, 20], "class_id": 0, "score": 0.99}],
        },
    )

    text, confidence, detections, layout_meta = api.ocr_image_layout_aware(
        np.zeros((30, 30, 3), dtype=np.uint8)
    )

    assert "FULL PAGE OCR" in text
    assert "LAYOUT REGION OCR" in text
    assert "REGION TEXT" in text
    assert confidence == 0.85
    assert detections == 3
    assert layout_meta == {
        "layout_status": "ready",
        "layout_regions": 1,
        "layout_crop_chunks": 1,
    }


def test_layout_aware_ocr_returns_baseline_when_worker_has_no_regions(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    api = Milestone1NotebookAPI(repo_root)

    monkeypatch.setattr(api, "preprocess_for_ocr", lambda image_rgb: image_rgb)
    monkeypatch.setattr(api, "easyocr_on_image_array", lambda image_rgb: ("FULL PAGE", 0.8, 2))
    monkeypatch.setattr(api, "detect_layout_regions", lambda image_rgb: {"status": "worker_unavailable", "regions": []})

    text, confidence, detections, layout_meta = api.ocr_image_layout_aware(
        np.zeros((30, 30, 3), dtype=np.uint8)
    )

    assert text == "FULL PAGE"
    assert confidence == 0.8
    assert detections == 2
    assert layout_meta == {
        "layout_status": "worker_unavailable",
        "layout_regions": 0,
        "layout_crop_chunks": 0,
    }
