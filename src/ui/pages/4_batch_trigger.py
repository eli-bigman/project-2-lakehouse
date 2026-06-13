"""
src/ui/pages/4_batch_trigger.py — Batch Trigger page.

Only rendered when UI_ENABLE_TRIGGER=true (see config.py).

Allows a user to:
  1. Upload a .csv or .xlsx file.
  2. Select the target dataset.
  3. Submit — uploads to the raw S3 bucket and starts a Step Functions execution.
  4. Poll execution status with a progress indicator.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import boto3
import streamlit as st

from ui import config
from ui.components import stepfunctions as sf_helper

# Guard — this page should never render if triggering is disabled
if not config.UI_ENABLE_TRIGGER:
    st.warning(
        "Batch triggering is disabled in this environment. "
        "Set UI_ENABLE_TRIGGER=true to enable it."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATASETS = ["dim_products", "fct_orders", "fct_order_items"]

_ALLOWED_EXTENSIONS = {".csv", ".xlsx"}

_POLL_INTERVAL_SEC = 5
_POLL_TIMEOUT_SEC = 300  # 5 minutes


def _s3_client():
    if config.AWS_PROFILE:
        session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    else:
        session = boto3.Session(region_name=config.AWS_REGION)
    return session.client("s3")


def _upload_to_raw(file_bytes: bytes, filename: str, dataset: str, batch_id: str) -> str:
    """Upload file to the raw S3 bucket and return the S3 URI."""
    now = datetime.now(timezone.utc)
    key = f"{dataset}/{now.year:04d}/{now.month:02d}/{batch_id}/{filename}"
    s3 = _s3_client()
    s3.put_object(Bucket=config.S3_RAW_BUCKET, Key=key, Body=file_bytes)
    return f"s3://{config.S3_RAW_BUCKET}/{key}"


def _poll_execution(execution_arn: str, status_placeholder) -> str:
    """Poll execution until terminal state or timeout. Updates a Streamlit placeholder."""
    terminal = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
    elapsed = 0

    while elapsed < _POLL_TIMEOUT_SEC:
        info = sf_helper.get_execution_status(execution_arn)
        status = info.get("status", "UNKNOWN")
        status_placeholder.info(f"Execution status: **{status}** (elapsed: {elapsed}s)")

        if status in terminal:
            return status

        time.sleep(_POLL_INTERVAL_SEC)
        elapsed += _POLL_INTERVAL_SEC

    return "POLL_TIMEOUT"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render() -> None:
    st.header("Batch Trigger")
    st.warning(
        "This page uploads a file to S3 and starts a live pipeline execution. "
        "Use with caution in shared environments."
    )

    # -----------------------------------------------------------------------
    # Controls
    # -----------------------------------------------------------------------
    uploaded_file = st.file_uploader(
        "Upload a dataset file (.csv or .xlsx)",
        type=["csv", "xlsx"],
    )

    dataset = st.selectbox("Target dataset", _DATASETS)

    submit = st.button("Upload & Trigger Pipeline", type="primary", disabled=uploaded_file is None)

    # -----------------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------------
    if submit and uploaded_file is not None:
        filename = uploaded_file.name
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in _ALLOWED_EXTENSIONS:
            st.error(f"Unsupported file type '{ext}'. Allowed: {_ALLOWED_EXTENSIONS}")
            return

        batch_id = (
            f"{dataset}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-"
            f"{uuid.uuid4().hex[:6]}"
        )

        with st.spinner(f"Uploading {filename} to {config.S3_RAW_BUCKET}..."):
            try:
                s3_uri = _upload_to_raw(uploaded_file.read(), filename, dataset, batch_id)
                st.success(f"Uploaded → {s3_uri}")
            except Exception as exc:
                st.error(f"Upload failed: {exc}")
                return

        with st.spinner("Starting Step Functions execution..."):
            try:
                execution_input = {
                    "dataset": dataset,
                    "batch_id": batch_id,
                    "source_file": s3_uri,
                }
                execution_arn = sf_helper.start_execution(
                    input_dict=execution_input,
                    name=batch_id,
                )
                st.info(f"Execution started: `{execution_arn}`")
            except Exception as exc:
                st.error(f"Failed to start execution: {exc}")
                return

        # Poll status
        st.markdown("---")
        st.subheader("Execution Progress")
        status_placeholder = st.empty()

        with st.spinner("Waiting for execution to complete..."):
            final_status = _poll_execution(execution_arn, status_placeholder)

        if final_status == "SUCCEEDED":
            st.success(f"Pipeline completed successfully for batch `{batch_id}`.")
        elif final_status == "POLL_TIMEOUT":
            st.warning(
                f"Polling timed out after {_POLL_TIMEOUT_SEC}s. "
                "Check the Step Functions console for the final status."
            )
        else:
            st.error(f"Execution ended with status: **{final_status}**. Check CloudWatch logs.")
