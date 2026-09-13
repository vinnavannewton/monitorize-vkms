#!/usr/bin/env bash
# Read-only post-reboot inspection for monitorize-vkms.

set -euo pipefail

readonly PACKAGE_NAME="monitorize-vkms"
readonly KERNEL="$(uname -r)"

printf 'Kernel: %s\n' "$KERNEL"

if preferred="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null)"; then
	printf 'Preferred VKMS: %s\n' "$preferred"
	case "$preferred" in
		"/lib/modules/${KERNEL}/updates/"*|"/lib/modules/${KERNEL}/extra/"*)
			printf 'Monitorize VKMS appears preferred: yes\n'
			;;
		*) printf 'Monitorize VKMS appears preferred: no\n' ;;
	esac
	if modinfo "$preferred" | grep -q '^parm: *create_default_dev:'; then
		printf 'create_default_dev parameter: available\n'
	else
		printf 'create_default_dev parameter: unavailable\n'
	fi
else
	printf 'Preferred VKMS: unavailable\n'
fi

if command -v dkms >/dev/null; then
	printf 'DKMS status:\n'
	dkms status -m "$PACKAGE_NAME" 2>&1 || printf 'No %s DKMS package registered.\n' "$PACKAGE_NAME"
else
	printf 'DKMS status: dkms is unavailable\n'
fi
