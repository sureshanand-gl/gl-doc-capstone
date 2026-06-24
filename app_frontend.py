from pathlib import Path

import streamlit as st

from app_backend import Milestone1NotebookAPI
from app_frontend_helpers import (
    answer_with_gpt,
    build_rag_chunks,
    draw_layout_boxes,
    load_preview_image_rgb,
    retrieve_chunks,
)


st.set_page_config(page_title="Milestone 2 Invoice Understanding App", layout="wide")
st.title("Milestone 2: Layout-Aware Invoice Field Extraction")

ROOT = Path(__file__).resolve().parent
api = Milestone1NotebookAPI(ROOT)

if not api.ocr_available:
    st.warning(
        "OCR models missing. Add craft_mlt_25k.pth and english_g2.pth to repository root to enable processing."
    )

if "rag_docs" not in st.session_state:
    st.session_state.rag_docs = []
if "rag_chat_history" not in st.session_state:
    st.session_state.rag_chat_history = []
if "layout_preview" not in st.session_state:
    st.session_state.layout_preview = None

mode_options = ["auto", "gpt", "qwen"]
default_mode = api.field_extractor_mode if api.field_extractor_mode in mode_options else "auto"
selected_mode = st.selectbox("Field Extraction Mode", mode_options, index=mode_options.index(default_mode))
api.field_extractor_mode = selected_mode

st.caption(
    f"Configured mode: {api.field_extractor_mode} | GPT Model: {api.model_name} | API Base: {api.openai_api_base}"
)

uploaded = st.file_uploader("Upload JPG, PDF, or DOCX", type=["jpg", "jpeg", "png", "pdf", "docx"])
process_clicked = st.button("Upload and Process", type="primary", disabled=uploaded is None or not api.ocr_available)

if process_clicked and uploaded is not None:
    with st.spinner("Running OCR and extracting fields..."):
        try:
            uploaded_bytes = uploaded.getvalue()
            uploaded.seek(0)
            result = api.process_upload(uploaded)
            st.success("Processing complete.")
            fields = result.get("fields", {})
            used_mode = result.get("extraction_mode", "unknown")
            llmops_metadata = result.get("llmops", {})
            fallback_reason = fields.get("fallback_reason")
            fallback_detail = fields.get("fallback_detail")

            st.caption(f"Configured mode: {api.field_extractor_mode} | Extraction mode used: {used_mode}")

            layout_info = result.get("layout")
            layout_summary = result.get("layout_summary")
            if layout_info or layout_summary:
                st.subheader("Layout-Aware OCR Summary")
                if layout_info:
                    st.json(layout_info)
                if layout_summary:
                    st.json(layout_summary)

            file_suffix = Path(uploaded.name).suffix.lower()
            if file_suffix in {".jpg", ".jpeg", ".png", ".pdf"}:
                preview_rgb = load_preview_image_rgb(uploaded_bytes, file_suffix)
                if preview_rgb is not None:
                    layout_det = api.detect_layout_regions(preview_rgb)
                    overlay_rgb = draw_layout_boxes(preview_rgb, layout_det.get("regions", []))
                    st.session_state.layout_preview = {
                        "file_name": uploaded.name,
                        "original": preview_rgb,
                        "overlay": overlay_rgb,
                        "layout_status": layout_det.get("status"),
                        "region_count": len(layout_det.get("regions", [])),
                    }

            if fallback_reason:
                st.warning(f"Fallback activated: {fallback_reason}")
                if fallback_detail:
                    with st.expander("Fallback details"):
                        st.text(fallback_detail)

            st.subheader("Extracted Text")
            st.text_area("OCR Output", result["text"], height=320)

            st.subheader("Extracted Fields")
            st.json(fields)

            st.subheader("LLMOps Metadata")
            st.json(llmops_metadata)

            text = result.get("text", "")
            if text.strip():
                existing = [doc for doc in st.session_state.rag_docs if doc.get("file") != uploaded.name]
                existing.append({"file": uploaded.name, "type": result.get("type", "unknown"), "text": text})
                st.session_state.rag_docs = existing
        except Exception as exc:
            st.error(f"Processing failed: {exc}")

if st.session_state.layout_preview is not None:
    preview = st.session_state.layout_preview
    st.subheader("Layout Output (Box Overlay)")
    st.caption(f"File: {preview['file_name']}")
    col1, col2 = st.columns(2)
    with col1:
        st.image(preview["original"], caption="Original Preview", use_container_width=True)
    with col2:
        st.image(preview["overlay"], caption="Layout Worker Box Overlay", use_container_width=True)
    st.caption(f"Layout status: {preview['layout_status']} | regions: {preview['region_count']}")

st.divider()
st.subheader("GPT-Aided RAG Chat")

if not st.session_state.rag_docs:
    st.info("Upload and process at least one file to start RAG chat.")
else:
    st.caption(f"Indexed documents: {len(st.session_state.rag_docs)}")
    with st.expander("Indexed Files"):
        for doc in st.session_state.rag_docs:
            st.write(f"- {doc.get('file')} ({doc.get('type')})")

    rag_query = st.text_input("Ask a question about uploaded invoices")
    rag_top_k = st.slider("Retrieved chunks", min_value=1, max_value=6, value=3)
    ask_clicked = st.button("Ask RAG", type="secondary", disabled=not rag_query.strip())

    if ask_clicked:
        chunks = build_rag_chunks(st.session_state.rag_docs)
        hits = retrieve_chunks(rag_query, chunks, top_k=rag_top_k)
        answer = answer_with_gpt(api, rag_query, hits)
        st.session_state.rag_chat_history.append({"question": rag_query, "answer": answer, "hits": hits})

    if st.session_state.rag_chat_history:
        st.subheader("Chat History")
        for index, item in enumerate(reversed(st.session_state.rag_chat_history), start=1):
            st.markdown(f"Question {index}: {item['question']}")
            st.write(item["answer"])
            if item.get("hits"):
                with st.expander(f"Retrieved Context for Question {index}"):
                    for hit in item["hits"]:
                        st.write(f"File: {hit['file']} | chunk: {hit['chunk_id']} | score: {hit['score']}")
                        st.text(hit["text"][:700])
