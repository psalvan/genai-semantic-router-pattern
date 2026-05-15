#!/usr/bin/env python3
"""Push SecureString parameters to SSM from a .env file (values via file://).

Prefix: `/{ENVIRONMENT}/nvidia-demo/`

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SECRET_ENV_TO_SUFFIX: tuple[tuple[str, str], ...] = (
    ("SMART_ROUTER_KEY", "SMART_ROUTER_KEY"),
    ("MAIN_LLM_KEY", "main_llm_key"),
    ("META_WEBHOOK_APP_SECRET", "meta_webhook_app_secret"),
    ("META_WEBHOOK_VERIFY_TOKEN", "meta_webhook_verify_token"),
    ("META_WEBHOOK_WHATSAPP_API_TOKEN", "meta_webhook_whatsapp_api_token"),
)

ALLOWED_ENVIRONMENTS = frozenset({"dev", "uat", "prod", "poc"})


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env-style file into a dict."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        val = rest.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def load_samconfig_deploy_params() -> dict[str, str]:
    """Read deploy.parameters from samconfig.toml default section, if present."""
    cfg = REPO_ROOT / "samconfig.toml"
    if not cfg.is_file():
        return {}
    with cfg.open("rb") as f:
        data = tomllib.load(f)
    try:
        params = data["default"]["deploy"]["parameters"]
        return {str(k): str(v) if v is not None else "" for k, v in params.items()}
    except (KeyError, TypeError):
        return {}


def environment_from_overrides(overrides: str) -> str | None:
    """Extract Environment= value from a SAM parameter_overrides string."""
    match = re.search(r"\bEnvironment=(\w+)", overrides)
    return match.group(1) if match else None


def resolve_environment(
    cli_env: str | None,
    dotenv: dict[str, str],
    sam_deploy: dict[str, str],
) -> str:
    """Resolve deployment environment name from CLI, .env, or samconfig overrides."""
    if cli_env:
        return cli_env.strip().lower()
    for key in ("ENVIRONMENT", "NVIDIA_ENVIRONMENT"):
        value = (dotenv.get(key) or "").strip().lower()
        if value:
            return value
    value = environment_from_overrides(sam_deploy.get("parameter_overrides", ""))
    if value:
        return value.lower()
    return ""


def resolve_region(dotenv: dict[str, str], sam_deploy: dict[str, str]) -> str:
    """Pick AWS region from .env or samconfig, defaulting to sa-east-1."""
    value = (dotenv.get("AWS_REGION") or dotenv.get("AWS_DEFAULT_REGION") or "").strip()
    if value:
        return value
    value = (sam_deploy.get("region") or "").strip()
    if value:
        return value
    return "sa-east-1"


def resolve_profile(dotenv: dict[str, str], sam_deploy: dict[str, str]) -> str | None:
    """Pick AWS CLI profile from .env or samconfig."""
    value = (dotenv.get("AWS_PROFILE") or "").strip()
    if value:
        return value
    value = (sam_deploy.get("profile") or "").strip()
    return value or None


def put_parameter(
    *,
    name: str,
    value: str,
    region: str,
    profile: str | None,
    dry_run: bool,
) -> int:
    """Run aws ssm put-parameter for one SecureString; return process exit code."""
    cmd_prefix = ["aws", "ssm", "put-parameter", "--name", name, "--type", "SecureString", "--overwrite"]
    if profile:
        cmd_prefix.extend(["--profile", profile])
    cmd_prefix.extend(["--region", region])

    if dry_run:
        print(f"[dry-run] would put SecureString: {name}")
        return 0

    fd, tmp_path = tempfile.mkstemp(prefix="nvidia-ssm-", suffix=".secret")
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
    """CLI entry: push all configured secrets from .env to SSM."""
    parser = argparse.ArgumentParser(description="Push Nvidia demo SSM SecureString parameters from .env")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--environment", "-e")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-samconfig", action="store_true")
    args = parser.parse_args()

    sam_deploy = {} if args.no_samconfig else load_samconfig_deploy_params()
    dotenv = load_dotenv(args.env_file.resolve())

    env_name = resolve_environment(args.environment, dotenv, sam_deploy)
    if not env_name:
        print("Set ENVIRONMENT in .env, pass --environment, or use parameter_overrides.", file=sys.stderr)
        return 2
    if env_name not in ALLOWED_ENVIRONMENTS:
        print(f"Invalid ENVIRONMENT: {env_name!r}", file=sys.stderr)
        return 2

    region = resolve_region(dotenv, sam_deploy)
    profile = resolve_profile(dotenv, sam_deploy)

    if not args.dry_run and not shutil.which("aws"):
        print("AWS CLI not found on PATH.", file=sys.stderr)
        return 127

    exit_code = 0
    any_value = False
    prefix = f"/{env_name}/nvidia-demo/"
    for var_name, suffix in SECRET_ENV_TO_SUFFIX:
        secret_value = dotenv.get(var_name)
        if secret_value is None or not str(secret_value).strip():
            continue
        any_value = True
        rc = put_parameter(
            name=f"{prefix}{suffix}",
            value=str(secret_value),
            region=region,
            profile=profile,
            dry_run=args.dry_run,
        )
        if rc != 0:
            exit_code = rc

    if not any_value:
        print(
            "No secrets set. Expected at least one of: SMART_ROUTER_KEY, MAIN_LLM_KEY, "
            "META_WEBHOOK_APP_SECRET, META_WEBHOOK_VERIFY_TOKEN, META_WEBHOOK_WHATSAPP_API_TOKEN.",
            file=sys.stderr,
        )
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
