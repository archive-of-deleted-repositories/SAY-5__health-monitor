#!/usr/bin/env bash
# Sample recovery hook. Replace the body with whatever brings your service back.
#
# Conventions:
#   * exit 0 = recovery succeeded
#   * exit non-zero = recovery failed; the audit row will record this
#   * stdout/stderr are captured (truncated to 4 KiB each) into the audit log
set -euo pipefail

NAME="${1:-api}"
echo "restart_api.sh: pretending to restart ${NAME}"

# In production you would do something like:
#   sudo systemctl restart "${NAME}"
# or:
#   docker compose restart "${NAME}"
# or:
#   kubectl rollout restart deployment/"${NAME}"

exit 0
