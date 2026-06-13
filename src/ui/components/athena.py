"""
src/ui/components/athena.py — Athena query helper using AWS Data Wrangler.
"""

from __future__ import annotations

import pandas as pd

from ui import config


def run_query(
    sql: str,
    database: str | None = None,
    workgroup: str | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """
    Execute a SQL query against Athena and return the results as a pandas DataFrame.

    Parameters
    ----------
    sql:       The SQL query to execute.
    database:  Glue database name (defaults to config.ATHENA_DATABASE).
    workgroup: Athena workgroup (defaults to config.ATHENA_WORKGROUP).
    max_rows:  If set, append LIMIT to the query to cap result size.

    Returns
    -------
    pandas.DataFrame with query results.
    """
    import awswrangler as wr
    import boto3

    db = database or config.ATHENA_DATABASE
    wg = workgroup or config.ATHENA_WORKGROUP
    cap = max_rows if max_rows is not None else config.UI_ATHENA_MAX_ROWS

    # Inject a LIMIT clause only when the query does not already have one
    effective_sql = sql.rstrip("; \n")
    if cap and "limit" not in effective_sql.lower():
        effective_sql = f"{effective_sql}\nLIMIT {cap}"

    # Build a boto3 session (supports named profile for local dev)
    if config.AWS_PROFILE:
        boto3_session = boto3.Session(
            profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION
        )
    else:
        boto3_session = boto3.Session(region_name=config.AWS_REGION)

    df = wr.athena.read_sql_query(
        sql=effective_sql,
        database=db,
        workgroup=wg,
        boto3_session=boto3_session,
    )
    return df
