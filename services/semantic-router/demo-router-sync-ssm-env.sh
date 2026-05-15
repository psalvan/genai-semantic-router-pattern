#!/bin/bash
# Lê SMART_ROUTER_KEY do Parameter Store e escreve /etc/demo-router.env (systemd EnvironmentFile).
# Contexto: /etc/demo-router/instance.env (DEMO_ENVIRONMENT, DEMO_AWS_REGION) — criado no user-data.
set -u

LOG=/var/log/demo-router-sync-ssm-env.log
INSTANCE_ENV=/etc/demo-router/instance.env
OUT=/etc/demo-router.env

write_empty() {
  umask 077
  printf 'SMART_ROUTER_KEY=\n' >"$OUT"
  chmod 600 "$OUT" 2>/dev/null || true
}

{
  echo "---- $(date -u +%Y-%m-%dT%H:%M:%SZ) ----"
} >>"$LOG" 2>/dev/null || true

if [[ ! -f "$INSTANCE_ENV" ]]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $INSTANCE_ENV missing" >>"$LOG" 2>/dev/null || true
  write_empty
  exit 0
fi

set -a
# shellcheck source=/dev/null
source "$INSTANCE_ENV" || true
set +a

if [[ -z "${DEMO_ENVIRONMENT:-}" || -z "${DEMO_AWS_REGION:-}" ]]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DEMO_ENVIRONMENT or DEMO_AWS_REGION empty" >>"$LOG" 2>/dev/null || true
  write_empty
  exit 0
fi

PARAM_NAME="/${DEMO_ENVIRONMENT}/nvidia-demo/SMART_ROUTER_KEY"

if ! VAL=$(aws ssm get-parameter --name "$PARAM_NAME" --with-decryption --region "$DEMO_AWS_REGION" --query Parameter.Value --output text 2>>"$LOG"); then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) get-parameter failed for $PARAM_NAME" >>"$LOG" 2>/dev/null || true
  write_empty
  exit 0
fi

if [[ -z "$VAL" || "$VAL" == "None" ]]; then
  write_empty
  exit 0
fi

umask 077
printf 'SMART_ROUTER_KEY=%s\n' "$VAL" >"$OUT"
chmod 600 "$OUT"
exit 0
