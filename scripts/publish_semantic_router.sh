#!/usr/bin/env bash
# Publica services/semantic-router/ para o bucket do stack (prefixo semantic-router/).
set -euo pipefail

STACK_NAME="${STACK_NAME:-}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"

if [[ -z "$STACK_NAME" ]]; then
  echo "Defina STACK_NAME (ex.: dev-nvidia-demo)." >&2
  exit 1
fi
if [[ -z "$REGION" ]]; then
  echo "Defina AWS_REGION ou AWS_DEFAULT_REGION." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ARGS=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
  PROFILE_ARGS=(--profile "${AWS_PROFILE}")
fi

BUCKET="$(
  aws cloudformation describe-stacks \
    "${PROFILE_ARGS[@]}" \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='SemanticRouterArtifactBucketName'].OutputValue" \
    --output text
)"

if [[ -z "$BUCKET" || "$BUCKET" == "None" ]]; then
  echo "Output SemanticRouterArtifactBucketName não encontrado no stack $STACK_NAME." >&2
  exit 1
fi

echo "A sincronizar para s3://${BUCKET}/semantic-router/ (região $REGION)..."
aws s3 sync "${ROOT}/services/semantic-router/" "s3://${BUCKET}/semantic-router/" \
  --region "$REGION" \
  "${PROFILE_ARGS[@]}" \
  --delete \
  --exclude ".git/*" \
  --exclude "venv/*" \
  --exclude ".venv/*" \
  --exclude "__pycache__/*" \
  --exclude "*.pyc" \
  --exclude ".terraform/*" \
  --exclude "*.tfstate*"

echo "OK. Se a EC2 já arrancou sem artefactos, reinicia a instância."
