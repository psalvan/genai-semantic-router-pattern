#!/usr/bin/env python3
"""Print SAM `parameter_overrides` for a samconfig profile with optional .env network overrides.

Reads `parameter_overrides` from samconfig.toml for a given config section and replaces
`Environment=<value>` with `--environment` (to align with .env / Makefile).

If repo-root `.env` defines SEMANTIC_ROUTER_VPC_ID and/or SEMANTIC_ROUTER_PUBLIC_SUBNET_ID,
injects SemanticRouterVpcId and SemanticRouterPublicSubnetId into the overrides string.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMCONFIG = REPO_ROOT / "samconfig.toml"
DOTENV_PATH = REPO_ROOT / ".env"


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


def apply_semantic_router_network_from_dotenv(
    parameter_overrides: str,
    dotenv: dict[str, str],
) -> str:
    """Merge VPC/subnet IDs from .env into a parameter_overrides string."""
    vpc = (dotenv.get("SEMANTIC_ROUTER_VPC_ID") or "").strip()
    subnet = (dotenv.get("SEMANTIC_ROUTER_PUBLIC_SUBNET_ID") or "").strip()
    out = parameter_overrides
    if vpc:
        if re.search(r"SemanticRouterVpcId=", out):
            out = re.sub(
                r"SemanticRouterVpcId=\S+",
                f"SemanticRouterVpcId={vpc}",
                out,
                count=1,
            )
        else:
            out = f"{out} SemanticRouterVpcId={vpc}"
    if subnet:
        if re.search(r"SemanticRouterPublicSubnetId=", out):
            out = re.sub(
                r"SemanticRouterPublicSubnetId=\S+",
                f"SemanticRouterPublicSubnetId={subnet}",
                out,
                count=1,
            )
        else:
            out = f"{out} SemanticRouterPublicSubnetId={subnet}"
    return out


def main() -> None:
    """CLI entry: write resolved parameter_overrides to stdout."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "config_env",
        choices=("default", "deploy-router-phase1", "deploy-router-phase2"),
    )
    ap.add_argument("--environment", required=True, help="e.g. dev, uat, prod, poc")
    ap.add_argument(
        "--no-dotenv",
        action="store_true",
        help="Do not read .env for VPC/subnet (samconfig values only).",
    )
    args = ap.parse_args()

    raw = SAMCONFIG.read_text(encoding="utf-8")
    cfg = tomllib.loads(raw)
    try:
        po = cfg[args.config_env]["deploy"]["parameters"]["parameter_overrides"]
    except KeyError as exc:
        print(f"samconfig: missing section or key: {exc}", file=sys.stderr)
        sys.exit(1)
    po = str(po).strip()
    if not re.search(r"Environment=\S+", po):
        print(
            "samconfig: parameter_overrides missing Environment=… token",
            file=sys.stderr,
        )
        sys.exit(1)
    out = re.sub(
        r"Environment=\S+",
        f"Environment={args.environment}",
        po,
        count=1,
    )
    if not args.no_dotenv:
        out = apply_semantic_router_network_from_dotenv(out, load_dotenv(DOTENV_PATH))
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
