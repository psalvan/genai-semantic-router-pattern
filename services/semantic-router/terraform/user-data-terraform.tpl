#!/bin/bash
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
install -d -m 0755 /etc/demo-router
printf '%s\n' 'DEMO_ENVIRONMENT=${environment}' 'DEMO_AWS_REGION=${region}' > /etc/demo-router/instance.env
chmod 644 /etc/demo-router/instance.env
BUCKET="${bucket}"
PREFIX="semantic-router"
RID="${region}"
install -d -m 0755 /tmp/demo-semantic-router
set +e
aws s3 sync "s3://$${BUCKET}/$${PREFIX}/" /tmp/demo-semantic-router/ --region "$${RID}"
SYNEXIT=$$?
set -e
if [ "$$SYNEXIT" -eq 0 ] && [ -f /tmp/demo-semantic-router/main.py ]; then
  chmod +x /tmp/demo-semantic-router/bootstrap.sh
  /bin/bash /tmp/demo-semantic-router/bootstrap.sh /tmp/demo-semantic-router
else
  echo "User-data: s3 sync exit $$SYNEXIT or main.py missing — publica artefactos e reinicia" | tee /var/log/demo-router-userdata-hint.txt
fi
