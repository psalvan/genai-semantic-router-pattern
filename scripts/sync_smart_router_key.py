#!/usr/bin/env python3
"""Sync SMART_ROUTER_KEY from .env to SSM at `/{env}/nvidia-demo/SMART_ROUTER_KEY` (SecureString).

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_ssm_put_module():
    """Dynamically load ssm_put_secrets_from_env for shared helpers."""
    path = REPO_ROOT / "scripts" / "ssm_put_secrets_from_env.py"
    spec = importlib.util.spec_from_file_location("ssm_put_secrets_from_env", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    """CLI entry: push SMART_ROUTER_KEY only."""
    ssm = _load_ssm_put_module()
    parser = argparse.ArgumentParser(description="Sync SMART_ROUTER_KEY to SSM (nvidia-demo path).")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--environment", "-e")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-if-empty", action="store_true")
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

    router_key = (dotenv.get("SMART_ROUTER_KEY") or "").strip()
    if not router_key:
        if args.skip_if_empty:
            print("SMART_ROUTER_KEY empty — skipped (--skip-if-empty).")
            return 0
        print("SMART_ROUTER_KEY is empty in .env.", file=sys.stderr)
        return 1

    param_name = f"/{env_name}/nvidia-demo/SMART_ROUTER_KEY"
    return ssm.put_parameter(
        name=param_name,
        value=router_key,
        region=region,
        profile=profile,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
