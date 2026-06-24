import numpy as np

from app_frontend_helpers import build_rag_chunks, draw_layout_boxes, retrieve_chunks


def test_build_rag_chunks_splits_long_documents_with_overlap():
    docs = [
        {
            "file": "invoice-a.pdf",
            "text": "Alpha " * 400,
        }
    ]

    chunks = build_rag_chunks(docs, chunk_size=120, overlap=20)

    assert len(chunks) >= 2
    assert chunks[0]["file"] == "invoice-a.pdf"
    assert chunks[0]["chunk_id"] == 0


def test_retrieve_chunks_returns_highest_overlap_hits():
    chunks = [
        {"file": "a.pdf", "chunk_id": 0, "text": "invoice total due customer alpha"},
        {"file": "b.pdf", "chunk_id": 1, "text": "layout region preview only"},
        {"file": "c.pdf", "chunk_id": 2, "text": "invoice total due subtotal tax"},
    ]

    hits = retrieve_chunks("invoice total tax", chunks, top_k=2)

    assert [hit["file"] for hit in hits] == ["c.pdf", "a.pdf"]
    assert hits[0]["score"] >= hits[1]["score"]


def test_draw_layout_boxes_modifies_preview_pixels():
    image = np.zeros((60, 60, 3), dtype=np.uint8)
    regions = [{"bbox": [5, 5, 30, 30], "class_id": 0, "score": 0.95}]

    rendered = draw_layout_boxes(image, regions)

    assert rendered.shape == image.shape
    assert rendered.sum() > image.sum()
