"""
src/ui/pages/3_data_quality.py — Data Quality page.

Shows:
  - Quarantine counts per dataset per batch.
  - Bar chart of reject reasons.
  - Table of quarantined records (latest batch).
  - Referential integrity check (orphan FK counts).
  - Dedup sanity check (duplicate primary keys in Delta tables).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import config
from ui.components.athena import run_query

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_QUARANTINE_COUNTS_SQL = """
SELECT
    dataset,
    batch_id,
    reason,
    COUNT(*) AS count
FROM ecom_lakehouse_quarantine
GROUP BY dataset, batch_id, reason
ORDER BY batch_id DESC, count DESC
"""

_QUARANTINE_LATEST_SQL = """
SELECT *
FROM ecom_lakehouse_quarantine
WHERE batch_id = (
    SELECT MAX(batch_id) FROM ecom_lakehouse_quarantine
)
ORDER BY dataset, reason
"""

_RI_CHECK_SQL = """
SELECT
    i.id            AS item_id,
    i.order_id      AS orphan_order_id,
    i.product_id    AS orphan_product_id,
    i._batch_id     AS batch_id
FROM fct_order_items i
LEFT JOIN fct_orders o ON i.order_id = o.order_id
LEFT JOIN dim_products p ON i.product_id = p.product_id
WHERE o.order_id IS NULL OR p.product_id IS NULL
"""

_DEDUP_CHECK_SQL_TEMPLATE = """
SELECT
    '{dataset}' AS dataset,
    '{pk}' AS pk_column,
    COUNT(*) - COUNT(DISTINCT {pk}) AS duplicate_count
FROM {dataset}
"""


@st.cache_data(ttl=120)
def _load_quarantine_counts():
    return run_query(_QUARANTINE_COUNTS_SQL, max_rows=5000)


@st.cache_data(ttl=120)
def _load_quarantine_latest():
    return run_query(_QUARANTINE_LATEST_SQL, max_rows=config.UI_ATHENA_MAX_ROWS)


@st.cache_data(ttl=300)
def _load_ri_check():
    return run_query(_RI_CHECK_SQL, max_rows=500)


@st.cache_data(ttl=300)
def _load_dedup_checks():
    pk_map = {
        "dim_products": "product_id",
        "fct_orders": "order_id",
        "fct_order_items": "id",
    }
    results = []
    for dataset, pk in pk_map.items():
        sql = _DEDUP_CHECK_SQL_TEMPLATE.format(dataset=dataset, pk=pk)
        try:
            df = run_query(sql, max_rows=1)
            if not df.empty:
                results.append(df.iloc[0].to_dict())
        except Exception as exc:
            results.append({"dataset": dataset, "pk_column": pk, "duplicate_count": f"ERROR: {exc}"})
    return pd.DataFrame(results) if results else pd.DataFrame()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render() -> None:
    st.header("Data Quality")

    # -----------------------------------------------------------------------
    # Quarantine counts
    # -----------------------------------------------------------------------
    st.subheader("Quarantine Counts by Dataset & Batch")
    try:
        df_counts = _load_quarantine_counts()
        if df_counts.empty:
            st.success("No quarantined records found.")
        else:
            st.dataframe(df_counts, use_container_width=True)

            # Bar chart of reject reasons across all batches
            st.subheader("Reject Reasons — Distribution")
            reason_totals = (
                df_counts.groupby("reason")["count"].sum().reset_index().sort_values("count", ascending=False)
            )
            st.bar_chart(reason_totals.set_index("reason")["count"])
    except Exception as exc:
        st.error(f"Could not load quarantine counts: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # Latest batch quarantined records
    # -----------------------------------------------------------------------
    st.subheader("Quarantined Records — Latest Batch")
    try:
        df_latest = _load_quarantine_latest()
        if df_latest.empty:
            st.success("No quarantined records in the latest batch.")
        else:
            st.markdown(f"**{len(df_latest):,} quarantined rows in latest batch**")
            st.dataframe(df_latest, use_container_width=True)
    except Exception as exc:
        st.error(f"Could not load latest quarantine records: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # Referential integrity check
    # -----------------------------------------------------------------------
    st.subheader("Referential Integrity — Orphan FK Check")
    st.caption("Rows in fct_order_items without a matching order_id or product_id.")
    try:
        df_ri = _load_ri_check()
        if df_ri.empty:
            st.success("RI check passed — 0 orphan FKs detected.")
        else:
            st.warning(f"{len(df_ri):,} orphan FK rows detected (should be 0 in a healthy load).")
            st.dataframe(df_ri, use_container_width=True)
    except Exception as exc:
        st.error(f"RI check failed: {exc}")

    st.divider()

    # -----------------------------------------------------------------------
    # Dedup sanity check
    # -----------------------------------------------------------------------
    st.subheader("Dedup Sanity Check — Duplicate Primary Keys")
    st.caption("Duplicate PK count per table. Should be 0 after every pipeline run.")
    try:
        df_dedup = _load_dedup_checks()
        if df_dedup.empty:
            st.info("Could not run dedup checks.")
        else:
            # Highlight rows with duplicates
            def _highlight(row):
                try:
                    return ["background-color: #ffe0e0" if int(row["duplicate_count"]) > 0 else ""] * len(row)
                except (ValueError, TypeError):
                    return ["background-color: #fff3cd"] * len(row)

            st.dataframe(df_dedup.style.apply(_highlight, axis=1), use_container_width=True)
    except Exception as exc:
        st.error(f"Dedup check failed: {exc}")
