"""
src/ui/components/stepfunctions.py — AWS Step Functions helpers for the Streamlit UI.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from ui import config


def _get_sf_client():
    """Return a Step Functions client, respecting the configured AWS profile."""
    if config.AWS_PROFILE:
        session = boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)
    else:
        session = boto3.Session(region_name=config.AWS_REGION)
    return session.client("stepfunctions")


def list_executions(
    sm_arn: str | None = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    Return the most recent `max_results` executions for the State Machine.

    Each entry contains:
      executionArn, name, status, startDate, stopDate.
    """
    arn = sm_arn or config.STEP_FUNCTIONS_ARN
    sf = _get_sf_client()

    response = sf.list_executions(
        stateMachineArn=arn,
        maxResults=max_results,
    )
    return response.get("executions", [])


def start_execution(
    input_dict: dict[str, Any],
    sm_arn: str | None = None,
    name: str | None = None,
) -> str:
    """
    Start a Step Functions execution.

    Parameters
    ----------
    input_dict: Python dict to serialise as the execution input JSON.
    sm_arn:     State Machine ARN (defaults to config.STEP_FUNCTIONS_ARN).
    name:       Optional human-readable execution name (must be unique per SM).

    Returns
    -------
    The execution ARN string.
    """
    arn = sm_arn or config.STEP_FUNCTIONS_ARN
    sf = _get_sf_client()

    kwargs: dict[str, Any] = {
        "stateMachineArn": arn,
        "input": json.dumps(input_dict),
    }
    if name:
        kwargs["name"] = name

    response = sf.start_execution(**kwargs)
    return response["executionArn"]


def get_execution_status(execution_arn: str) -> dict[str, Any]:
    """
    Describe a single execution and return its status fields.

    Returns a dict with keys: executionArn, status, startDate, stopDate, input, output.
    """
    sf = _get_sf_client()
    response = sf.describe_execution(executionArn=execution_arn)
    return {
        "executionArn": response.get("executionArn"),
        "status": response.get("status"),
        "startDate": response.get("startDate"),
        "stopDate": response.get("stopDate"),
        "input": response.get("input"),
        "output": response.get("output"),
    }
