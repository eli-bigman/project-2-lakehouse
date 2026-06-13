"""
src/ui/pages/1_pipeline_dashboard.py — Pipeline Dashboard page.

Shows:
  - Step Functions execution history (last 10 runs) with status badges.
  - DynamoDB ledger summary stats (total runs, success/failed counts, last run time).
  - Watermarks table (last processed period per dataset).
  - CloudWatch metric sparklines (rows_in, rows_valid, rows_rejected — last 7 days).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import config
from ui.components import ledger as ledger_helper
from ui.components import stepfunctions as sf_helper
from ui.components.cloudwatch import get_metric_history

# ---------------------------------------------------------------------------
# Status badge colours
# ---------------------------------------------------------------------------

_STATUS_COLOURS = {
    "SUCCEEDED": "green",
    "RUNNING": "blue",
    "FAILED": "red",
    "TIMED_OUT": "orange",
    "ABORTED": "gray",
}


def _status_badge(status: str) -> str:
    colour = _STATUS_COLOURS.get(status, "gray")
    return (
        f"<span style='background:{colour};color:white;"
        f"padding:2px 8px;border-radius:4px;font-size:0.8em;'>{status}</span>"
    )


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


@st.cache_data(ttl=60)
def _load_executions():
    return sf_helper.list_executions(max_results=10)


@st.cache_data(ttl=60)
def _load_ledger_stats():
    runs = ledger_helper.get_recent_runs(n=200)
    return runs


@st.cache_data(ttl=60)
def _load_watermarks():
    return ledger_helper.get_watermarks()


@st.cache_data(ttl=300)
def _load_metric(metric_name: str, dataset: str) -> pd.DataFrame:
    return get_metric_history(
        namespace="ecom-lakehouse/pipeline",
        metric_name=metric_name,
        dimensions=[{"Name": "dataset", "Value": dataset}],
        days=7,
    )


def render() -> None:
    st.header("Pipeline Dashboard")

    # -----------------------------------------------------------------------
    # Step Functions — execution history
    # -----------------------------------------------------------------------
    st.subheader("Step Functions — Last 10 Executions")
    try:
        executions = _load_executions()
        if executions:
            rows = []
            for ex in executions:
                rows.append(
                    {
                        "Name": ex.get("name", ""),
                        "Status": ex.get("status", ""),
                        "Start": ex.get("startDate", ""),
                        "Stop": ex.get("stopDate", ""),
                    }
                )
            df_ex = pd.DataFrame(rows)
            # Render status as coloured badges
            status_col = df_ex["Status"].apply(lambda s: _status_badge(s))
            df_display = df_ex.copy()
            df_display["Status"] = status_col
            st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.info("No executions found.")
    except Exception as exc:
        st.error(f"Could not load executions: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # DynamoDB ledger stats
    # -----------------------------------------------------------------------
    st.subheader("Ingestion Ledger Summary")
    try:
        all_runs = _load_ledger_stats()
        total = len(all_runs)
        succeeded = sum(1 for r in all_runs if r.get("status") == "SUCCEEDED")
        failed = sum(1 for r in all_runs if r.get("status") == "FAILED")
        last_run_ts = (
            max((r.get("ingest_ts", "") for r in all_runs), default="—")
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Runs", total)
        col2.metric("Succeeded", succeeded)
        col3.metric("Failed", failed)
        col4.metric("Last Run", str(last_run_ts)[:19])
    except Exception as exc:
        st.error(f"Could not load ledger stats: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # Watermarks
    # -----------------------------------------------------------------------
    st.subheader("Dataset Watermarks")
    try:
        watermarks = _load_watermarks()
        if watermarks:
            st.dataframe(pd.DataFrame(watermarks), use_container_width=True)
        else:
            st.info("No watermarks found.")
    except Exception as exc:
        st.error(f"Could not load watermarks: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # CloudWatch metrics
    # -----------------------------------------------------------------------
    st.subheader("Pipeline Metrics — Last 7 Days")
    datasets = ["dim_products", "fct_orders", "fct_order_items"]
    metrics = ["rows_in", "rows_valid", "rows_rejected"]

    for dataset in datasets:
        st.markdown(f"**{dataset}**")
        cols = st.columns(len(metrics))
        for col, metric_name in zip(cols, metrics):
            with col:
                try:
                    df_m = _load_metric(metric_name, dataset)
                    if df_m.empty:
                        st.caption(f"{metric_name}: no data")
                    else:
                        st.caption(metric_name)
                        st.line_chart(df_m.set_index("Timestamp")["Value"])
                except Exception as exc:
                    st.caption(f"{metric_name}: error — {exc}")
        st.divider()
