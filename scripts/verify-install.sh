#!/usr/bin/env bash
# Read-only verification shared with monitorize-vkms verify.
set -euo pipefail
repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec /usr/bin/python3 -I -B "$repository/monitorize_vkms/verification.py" "$@"
