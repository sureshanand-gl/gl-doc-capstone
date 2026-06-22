from pathlib import Path

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
