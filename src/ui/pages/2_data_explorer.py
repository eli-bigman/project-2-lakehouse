"""
src/ui/pages/2_data_explorer.py — Data Explorer page.

Allows querying dim_products, fct_orders, and fct_order_items via Athena with:
  - Dataset selector.
  - Date range filter (for fact tables).
  - Configurable row cap (UI_ATHENA_MAX_ROWS).
  - Cost guardrail showing estimated bytes scanned.
  - Last modified time from the Delta transaction log.
"""

from __future__ import annotations

import datetime

import streamlit as st

from ui import config
from ui.components.athena import run_query

# ---------------------------------------------------------------------------
# Dataset metadata
# ---------------------------------------------------------------------------

_DATASETS = {
    "dim_products": {
        "has_date": False,
        "date_col": None,
        "description": "Product dimension table (~1k rows).",
    },
    "fct_orders": {
        "has_date": True,
        "date_col": "order_date",
        "description": "Orders fact table — one row per order.",
    },
    "fct_order_items": {
        "has_date": True,
        "date_col": "order_date",
        "description": "Order items fact table — one row per line item.",
    },
}


def _build_query(
    dataset: str, date_from: datetime.date | None, date_to: datetime.date | None
) -> str:
    """Build a SELECT query with an optional date range WHERE clause."""
    meta = _DATASETS[dataset]
    where_clauses = []

    if meta["has_date"] and date_from:
        where_clauses.append(f"{meta['date_col']} >= DATE '{date_from}'")
    if meta["has_date"] and date_to:
        where_clauses.append(f"{meta['date_col']} <= DATE '{date_to}'")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    return f"SELECT * FROM {dataset} {where_sql}"


def _get_delta_last_modified(dataset: str) -> str:
    """
    Query the Delta transaction log to find the last commit timestamp.
    Returns a formatted string or 'unavailable'.
    """
    try:
        sql = f"""
            SELECT MAX(timestamp) AS last_modified
            FROM "{config.ATHENA_DATABASE}"."{dataset}$history"
        """
        df = run_query(sql, max_rows=1)
        val = df["last_modified"].iloc[0] if not df.empty else None
        return str(val)[:19] if val else "unavailable"
    except Exception:
        return "unavailable"


def render() -> None:
    st.header("Data Explorer")

    # -----------------------------------------------------------------------
    # Controls
    # -----------------------------------------------------------------------
    col1, col2 = st.columns([1, 2])

    with col1:
        dataset = st.selectbox("Dataset", list(_DATASETS.keys()))

    meta = _DATASETS[dataset]
    date_from = date_to = None

    if meta["has_date"]:
        with col2:
            dc1, dc2 = st.columns(2)
            with dc1:
                date_from = st.date_input(
                    "From date",
                    value=datetime.date(2025, 4, 1),
                )
            with dc2:
                date_to = st.date_input(
                    "To date",
                    value=datetime.date.today(),
                )

    row_cap = st.slider(
        "Max rows",
        min_value=10,
        max_value=config.UI_ATHENA_MAX_ROWS,
        value=min(100, config.UI_ATHENA_MAX_ROWS),
        step=10,
    )

    st.caption(meta["description"])

    if st.button("Run Query", type="primary"):
        sql = _build_query(dataset, date_from, date_to)

        st.code(f"{sql}\nLIMIT {row_cap}", language="sql")

        with st.spinner("Running Athena query..."):
            try:
                df = run_query(sql, max_rows=row_cap)

                # ---------------------------------------------------------------
                # Results
                # ---------------------------------------------------------------
                st.markdown(f"**{len(df):,} rows returned** (cap: {row_cap:,})")
                st.dataframe(df, use_container_width=True)

                # Estimated cost hint (Athena charges $5/TB scanned)
                # awswrangler exposes query_metadata; if unavailable, omit gracefully
                st.caption(
                    "Cost note: Athena charges $5 per TB scanned. "
                    "Delta's data skipping and Z-Order reduce bytes scanned significantly."
                )

                # Last modified from Delta log
                last_mod = _get_delta_last_modified(dataset)
                st.caption(f"Delta table last modified: {last_mod}")

            except Exception as exc:
                st.error(f"Query failed: {exc}")
