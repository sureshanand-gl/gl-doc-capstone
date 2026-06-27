"""UI helper functions for layout previews and lightweight OCR-backed RAG workflows."""

import os
import re
import tempfile
from typing import Any

import cv2
import numpy as np
import pypdfium2 as pdfium


LAYOUT_CLASS_NAMES = ["text", "title", "list", "table", "figure"]


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        chunks.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return chunks


def build_rag_chunks(
    docs: list[dict[str, str]],
    chunk_size: int = 900,
    overlap: int = 150,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        file_name = doc.get("file", "unknown")
        for index, chunk in enumerate(chunk_text(doc.get("text", ""), chunk_size, overlap)):
            rows.append({"file": file_name, "chunk_id": index, "text": chunk})
    return rows


def retrieve_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    qset = set(tokenize(query))
    if not qset or not chunks:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk in chunks:
        cset = set(tokenize(str(chunk.get("text", ""))))
        overlap = len(qset.intersection(cset))
        if overlap <= 0:
            continue
        scored.append((overlap, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    for score, chunk in scored[:top_k]:
        row = dict(chunk)
        row["score"] = int(score)
        hits.append(row)
    return hits


def answer_with_gpt(api: Any, question: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No relevant context found in processed OCR documents."

    context = "\n\n---\n\n".join(
        [
            f"FILE: {hit['file']} | CHUNK: {hit['chunk_id']} | SCORE: {hit['score']}\n{hit['text']}"
            for hit in hits
        ]
    )
    if api.openai_client is None:
        return (
            "GPT client unavailable. Top retrieved context:\n\n"
            + "\n\n".join(
                [f"- {hit['file']} (chunk {hit['chunk_id']}): {hit['text'][:280]}" for hit in hits]
            )
        )

    prompt = (
        "Answer user question using only retrieved OCR context. "
        "If answer is not present, say it is not available in uploaded documents.\n\n"
        f"QUESTION:\n{question}\n\nCONTEXT:\n{context}"
    )
    try:
        response = api.openai_client.chat.completions.create(
            model=api.model_name,
            messages=[
                {"role": "system", "content": "You are an invoice RAG assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=350,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return f"GPT answer error: {exc}"


def load_preview_image_rgb(file_bytes: bytes, suffix: str) -> np.ndarray | None:
    suffix = suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        arr = np.frombuffer(file_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            doc = pdfium.PdfDocument(tmp_path)
            if len(doc) == 0:
                return None
            return np.array(doc[0].render(scale=2.0).to_pil().convert("RGB"))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return None


def draw_layout_boxes(image_rgb: np.ndarray, regions: list[dict[str, Any]]) -> np.ndarray:
    canvas = image_rgb.copy()
    for region in regions:
        bbox = region.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = [int(value) for value in bbox]
        class_id = int(region.get("class_id", -1))
        score = float(region.get("score", 0.0))
        color = (0, 255, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = (
            LAYOUT_CLASS_NAMES[class_id]
            if 0 <= class_id < len(LAYOUT_CLASS_NAMES)
            else f"cls{class_id}"
        )
        cv2.putText(
            canvas,
            f"{label}:{score:.2f}",
            (x1, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas
