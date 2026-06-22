import json
from pathlib import Path
from typing import Any


def write_trace_record(
    trace_path: Path,
    record: dict[str, Any],
    include_text: bool = False,
) -> None:
    payload = dict(record)
    if not include_text and "ocr_text" in payload:
        payload.pop("ocr_text", None)
        payload["ocr_text_redacted"] = True

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
