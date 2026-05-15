#!/usr/bin/env bash
# EC2 user data (cloud-init) for Ubuntu 24.04 ARM64 + t4g.small.
# Paste into the launch wizard or pass as user_data in Terraform (base64-encoded).
#
# Configure either S3 sync (needs an instance profile with s3:GetObject) OR leave
# empty and run bootstrap.sh manually after scp/git (see README).
set -euo pipefail

exec > >(tee /var/log/demo-router-user-data.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
# awscli apt package is unavailable on Ubuntu 24.04+ default repos; use official AWS CLI v2 bundle.
apt-get install -y --no-install-recommends ca-certificates curl unzip
ARCH="$(uname -m)"
case "$ARCH" in
  aarch64) AWS_CLI_URL="https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" ;;
  x86_64)  AWS_CLI_URL="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" ;;
  *) echo "unsupported architecture for AWS CLI: $ARCH" >&2; exit 1 ;;
esac
curl -fsSL "$AWS_CLI_URL" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install -i /usr/local/aws-cli -b /usr/local/bin
rm -rf /tmp/aws /tmp/awscliv2.zip

# --- Set these before launch if using S3 artifacts ---
ARTIFACT_BUCKET=""
ARTIFACT_PREFIX="semantic-router/"

if [[ -n "${ARTIFACT_BUCKET}" ]]; then
  install -d -m 0755 /tmp/demo-semantic-router
  aws s3 sync "s3://${ARTIFACT_BUCKET}/${ARTIFACT_PREFIX}" /tmp/demo-semantic-router/
  chmod +x /tmp/demo-semantic-router/bootstrap.sh
  /bin/bash /tmp/demo-semantic-router/bootstrap.sh /tmp/demo-semantic-router
else
  echo "$(date -uIs) ARTIFACT_BUCKET empty: copy services/semantic-router to the instance and run:" \
    "sudo /path/to/bootstrap.sh /path/to/semantic-router" | tee /var/log/demo-router-userdata-hint.txt
fi
