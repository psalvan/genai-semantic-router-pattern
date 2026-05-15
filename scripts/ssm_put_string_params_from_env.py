#!/usr/bin/env python3
"""Push String (non-secret) parameters to `/{env}/nvidia-demo/` from a .env file (file:// values).

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STRING_ENV_TO_SUFFIX: tuple[tuple[str, str], ...] = (
    ("MAIN_LLM_API", "main_llm_api"),
    ("MAIN_LLM_MODEL", "main_llm_model"),
    ("MAIN_LLM_TIMEOUT_S", "main_llm_timeout"),
    ("MAIN_LLM_API_VERSION", "main_llm_api_version"),
    ("META_PHONE_NUMBER_ID", "meta_phone_number_id"),
)


def _load_ssm_put_module():
    """Dynamically load sibling module ssm_put_secrets_from_env for shared helpers."""
    path = REPO_ROOT / "scripts" / "ssm_put_secrets_from_env.py"
    spec = importlib.util.spec_from_file_location("ssm_put_secrets_from_env", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put_string_parameter(
    *,
    name: str,
    value: str,
    region: str,
    profile: str | None,
    dry_run: bool,
) -> int:
    """Run aws ssm put-parameter for one String parameter; return process exit code."""
    cmd_prefix = ["aws", "ssm", "put-parameter", "--name", name, "--type", "String", "--overwrite"]
    if profile:
        cmd_prefix.extend(["--profile", profile])
    cmd_prefix.extend(["--region", region])

    if dry_run:
        print(f"[dry-run] would put String: {name}")
        return 0

    fd, tmp_path = tempfile.mkstemp(prefix="nvidia-ssm-str-", suffix=".txt")
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        cmd = [*cmd_prefix, f"--value=file://{tmp_path}"]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr or proc.stdout or f"aws failed ({proc.returncode})\n")
            return proc.returncode
        print(f"OK {name}")
        return 0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main() -> int:
    """CLI entry: push String SSM parameters from .env (MAIN_LLM_*, META_PHONE_NUMBER_ID, …)."""
    ssm = _load_ssm_put_module()
    parser = argparse.ArgumentParser(description="Push Nvidia demo SSM String parameters from .env")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--environment", "-e")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-samconfig", action="store_true")
    args = parser.parse_args()

    sam_deploy = {} if args.no_samconfig else ssm.load_samconfig_deploy_params()
    dotenv = ssm.load_dotenv(args.env_file.resolve())

    env_name = ssm.resolve_environment(args.environment, dotenv, sam_deploy)
    if not env_name:
        print("Set ENVIRONMENT.", file=sys.stderr)
        return 2
    if env_name not in ssm.ALLOWED_ENVIRONMENTS:
        allowed = ", ".join(sorted(ssm.ALLOWED_ENVIRONMENTS))
        print(
            f"Invalid ENVIRONMENT: {env_name!r}. Allowed: {allowed}.",
            file=sys.stderr,
        )
        return 2

    region = ssm.resolve_region(dotenv, sam_deploy)
    profile = ssm.resolve_profile(dotenv, sam_deploy)

    if not args.dry_run and not shutil.which("aws"):
        print("AWS CLI not found.", file=sys.stderr)
        return 127

    prefix = f"/{env_name}/nvidia-demo/"
    exit_code = 0
    any_value = False
    for var_name, suffix in STRING_ENV_TO_SUFFIX:
        val = dotenv.get(var_name)
        if val is None or not str(val).strip():
            continue
        any_value = True
        rc = put_string_parameter(
            name=f"{prefix}{suffix}",
            value=str(val).strip(),
            region=region,
            profile=profile,
            dry_run=args.dry_run,
        )
        if rc != 0:
            exit_code = rc

    if not any_value:
        print(
            "No string variables set (e.g. MAIN_LLM_API, MAIN_LLM_MODEL, MAIN_LLM_TIMEOUT_S, "
            "MAIN_LLM_API_VERSION, META_PHONE_NUMBER_ID).",
            file=sys.stderr,
        )
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
