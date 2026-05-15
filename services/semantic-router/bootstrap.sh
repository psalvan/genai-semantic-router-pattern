#!/usr/bin/env bash
# One-time (or idempotent) setup for Demo semantic-router on Ubuntu 24.04+ (ARM64).
# Run as root: sudo ./bootstrap.sh [SOURCE_DIR]
# SOURCE_DIR defaults to the directory containing this script (expects main.py, requirements.txt, demo-router.service).
set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${1:-$SCRIPT_DIR}"
APP_ROOT="/opt/demo-router"
VENV_PATH="${APP_ROOT}/venv"
CACHE_DIR="${APP_ROOT}/.cache/huggingface"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends python3-pip python3-venv

install -d -o ubuntu -g ubuntu -m 0755 "${APP_ROOT}"
install -d -o ubuntu -g ubuntu -m 0755 "${CACHE_DIR}"

install -m 0644 "${SOURCE_DIR}/main.py" "${APP_ROOT}/main.py"
install -m 0644 "${SOURCE_DIR}/requirements.txt" "${APP_ROOT}/requirements.txt"
install -m 0644 "${SOURCE_DIR}/demo-router.service" /etc/systemd/system/demo-router.service
install -m 0755 "${SOURCE_DIR}/demo-router-sync-ssm-env.sh" /usr/local/sbin/demo-router-sync-ssm-env.sh

if [[ ! -d "${VENV_PATH}" ]]; then
  sudo -u ubuntu python3 -m venv "${VENV_PATH}"
fi

sudo -u ubuntu "${VENV_PATH}/bin/python" -m pip install --upgrade pip
sudo -u ubuntu "${VENV_PATH}/bin/pip" install -r "${APP_ROOT}/requirements.txt"

chown -R ubuntu:ubuntu "${APP_ROOT}"

systemctl daemon-reload
systemctl enable demo-router.service
systemctl restart demo-router.service

echo "Done. Check: sudo systemctl status demo-router && curl -sS http://127.0.0.1:8000/health"
