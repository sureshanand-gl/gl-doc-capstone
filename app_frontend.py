
from pathlib import Path
import streamlit as st
from app_backend import Milestone1NotebookAPI

st.set_page_config(page_title='Milestone 1 EasyOCR + GPT-4o-mini App', layout='wide')
st.title('Milestone 1: EasyOCR Field Extraction')

ROOT = Path(__file__).resolve().parent
api = Milestone1NotebookAPI(ROOT)

if not api.ocr_available:
    st.warning(
        "OCR models missing. Add craft_mlt_25k.pth and english_g2.pth to repository root to enable processing."
    )

mode_options = ['auto', 'gpt', 'qwen']
default_mode = api.field_extractor_mode if api.field_extractor_mode in mode_options else 'auto'
selected_mode = st.selectbox('Field Extraction Mode', mode_options, index=mode_options.index(default_mode))
api.field_extractor_mode = selected_mode

st.caption(
    f"Configured mode: {api.field_extractor_mode} | GPT Model: {api.model_name} | API Base: {api.openai_api_base}"
)

uploaded = st.file_uploader('Upload JPG, PDF, or DOCX', type=['jpg', 'jpeg', 'png', 'pdf', 'docx'])
process_clicked = st.button('Upload and Process', type='primary', disabled=uploaded is None or not api.ocr_available)

if process_clicked and uploaded is not None:
    with st.spinner('Running OCR and extracting fields...'):
        try:
            result = api.process_upload(uploaded)
            st.success('Processing complete.')
            fields = result.get('fields', {})
            used_mode = result.get('extraction_mode', 'unknown')
            llmops_metadata = result.get('llmops', {})
            fallback_reason = fields.get('fallback_reason')
            fallback_detail = fields.get('fallback_detail')

            st.caption(f"Configured mode: {api.field_extractor_mode} | Extraction mode used: {used_mode}")

            if fallback_reason:
                st.warning(f"Fallback activated: {fallback_reason}")
                if fallback_detail:
                    with st.expander('Fallback details'):
                        st.text(fallback_detail)

            st.subheader('Extracted Text')
            st.text_area('OCR Output', result['text'], height=320)

            st.subheader('Extracted Fields')
            st.json(fields)

            st.subheader('LLMOps Metadata')
            st.json(llmops_metadata)
        except Exception as exc:
            st.error(f'Processing failed: {exc}')
