#!/usr/bin/env python3
"""Exit non-zero if SemanticRouterEc2Enabled=true but VPC/subnet are placeholders or invalid.

Used by the Makefile before `sam deploy` when EC2 hosting is enabled.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import re
import sys

_VPC_RE = re.compile(r"^vpc-[0-9a-f]{8,17}$", re.I)
_SUBNET_RE = re.compile(r"^subnet-[0-9a-f]{8,17}$", re.I)


def parse_override_token(overrides: str, key: str) -> str | None:
    """Return the value for `key=` inside a space/comma-separated overrides string."""
    for token in overrides.replace(",", " ").split():
        token = token.strip()
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1].strip()
    return None


def main() -> int:
    """CLI entry: validate EC2 network parameters when EC2 is enabled."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("parameter_overrides", help='e.g. Environment=dev SemanticRouterVpcId=vpc-...')
    args = ap.parse_args()
    po = args.parameter_overrides.strip()

    ec2_flag = (parse_override_token(po, "SemanticRouterEc2Enabled") or "false").lower()
    if ec2_flag != "true":
        return 0

    vpc = parse_override_token(po, "SemanticRouterVpcId") or ""
    subnet = parse_override_token(po, "SemanticRouterPublicSubnetId") or ""

    errors: list[str] = []
    if vpc in ("", "vpc-REPLACE") or not _VPC_RE.match(vpc):
        errors.append(
            f"SemanticRouterVpcId is invalid or a placeholder ({vpc!r}). "
            "Set a real VPC id in the target region (e.g. vpc-0a1b2c3d…).",
        )
    if subnet in ("", "subnet-REPLACE") or not _SUBNET_RE.match(subnet):
        errors.append(
            f"SemanticRouterPublicSubnetId is invalid or a placeholder ({subnet!r}). "
            "Set a public subnet with a route to the IGW (e.g. subnet-0a1b2c3d…).",
        )
    if errors:
        print(
            "SemanticRouterEc2Enabled=true but EC2 network is not configured:\n",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nUpdate `parameter_overrides` in samconfig.toml (or pass overrides on the CLI).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
