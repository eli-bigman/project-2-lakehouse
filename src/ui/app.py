"""
src/ui/app.py — ecom-lakehouse Streamlit dashboard entry point.

Run with:
    streamlit run src/ui/app.py

Environment variables (see src/ui/config.py for full list):
    AWS_PROFILE         Named boto3 profile (local dev; omit in production)
    UI_ENV_LABEL        Label shown in the sidebar badge (default: "dev")
    UI_ENABLE_TRIGGER   Set to "true" to enable the Batch Trigger page
"""

import importlib

import streamlit as st

from ui import config

# ---------------------------------------------------------------------------
# Page configuration (must be the first Streamlit call in the script)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ecom-lakehouse Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("ecom-lakehouse")

    # Environment badge
    env_colour = "green" if config.UI_ENV_LABEL == "prod" else "orange"
    st.markdown(
        f"<span style='background-color:{env_colour};color:white;"
        f"padding:2px 8px;border-radius:4px;font-weight:bold;'>"
        f"{config.UI_ENV_LABEL.upper()}</span>",
        unsafe_allow_html=True,
    )
    st.divider()

    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # Navigation
    pages = {
        "Pipeline Dashboard": "1_pipeline_dashboard",
        "Data Explorer": "2_data_explorer",
        "Data Quality": "3_data_quality",
    }
    if config.UI_ENABLE_TRIGGER:
        pages["Batch Trigger"] = "4_batch_trigger"

    selection = st.radio("Navigation", list(pages.keys()), label_visibility="collapsed")

# ---------------------------------------------------------------------------
# Page routing — dynamically import and run the selected page module
# ---------------------------------------------------------------------------

page_module_name = f"ui.pages.{pages[selection]}"

try:
    page_mod = importlib.import_module(page_module_name)
    page_mod.render()
except ModuleNotFoundError as exc:
    st.error(f"Page module not found: {page_module_name}\n\n{exc}")
except Exception as exc:
    st.exception(exc)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.caption(
    f"Region: {config.AWS_REGION}  |  "
    f"DB: {config.ATHENA_DATABASE}  |  "
    f"Workgroup: {config.ATHENA_WORKGROUP}"
)
