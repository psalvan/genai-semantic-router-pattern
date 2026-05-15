"""AWS Systems Manager Parameter Store helpers with in-process caching.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import os

from typing import Any

import boto3
from botocore.exceptions import ClientError

_ssm_client = None
_parameter_cache: dict[str, str] = {}


def _get_ssm_client() -> Any:
    """Lazily construct and cache the boto3 SSM client for the Lambda/task region."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client(
            "ssm",
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        )
    return _ssm_client


def ssm_base_path() -> str:
    """Return the parameter path prefix for this deployment (e.g. /dev/nvidia-demo)."""
    env = (os.environ.get("ENVIRONMENT") or "dev").strip().lower()
    return f"/{env}/nvidia-demo"


def get_parameter(relative_name: str, *, decrypt: bool = False) -> str:
    """Fetch a parameter by suffix under ssm_base_path(); whitespace-only values are treated as empty."""
    name = f"{ssm_base_path()}/{relative_name.strip().lstrip('/')}"
    if name in _parameter_cache:
        return _parameter_cache[name]
    try:
        response = _get_ssm_client().get_parameter(Name=name, WithDecryption=decrypt)
        value = (response.get("Parameter") or {}).get("Value") or ""
    except ClientError:
        value = ""
    value = value.strip()
    if value in ("", "None"):
        value = ""
    _parameter_cache[name] = value
    return value


def clear_ssm_cache() -> None:
    """Clear the in-memory parameter cache (call at the start of each Lambda invocation)."""
    _parameter_cache.clear()
