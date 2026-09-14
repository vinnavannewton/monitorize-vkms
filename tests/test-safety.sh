#!/usr/bin/env bash

set -euo pipefail

readonly ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

assert_contains() {
	local file="$1" text="$2"
	grep -Fq -- "$text" "$file" || {
		printf 'missing safety contract in %s: %s\n' "$file" "$text" >&2
		exit 1
	}
}

assert_not_contains() {
	local file="$1" text="$2"
	if grep -Fq -- "$text" "$file"; then
		printf 'unsafe text remains in %s: %s\n' "$file" "$text" >&2
		exit 1
	fi
}

bash -n \
	"${ROOT}/install.sh" \
	"${ROOT}/uninstall.sh" \
	"${ROOT}/scripts/monitorize-vkms-bootstrap.sh" \
	"${ROOT}/scripts/verify-install.sh" \
	"${ROOT}/scripts/probe-drm-atomic-api.sh"

for package_manager in 'dnf ' 'apt-get ' 'pacman ' 'zypper '; do
	assert_not_contains "${ROOT}/install.sh" "$package_manager"
done

assert_contains "${ROOT}/dkms.conf" 'BUILT_MODULE_NAME[0]="monitorize_vkms"'
assert_contains "${ROOT}/dkms.conf" 'DEST_MODULE_NAME[0]="monitorize_vkms"'
assert_contains "${ROOT}/dkms.conf" 'AUTOINSTALL="no"'
assert_contains "${ROOT}/dkms.conf" 'NO_WEAK_MODULES="yes"'
assert_contains "${ROOT}/install.sh" 'Compiling a disposable compatibility preflight before changing the system'
assert_contains "${ROOT}/install.sh" "trap 'rollback_failed_install \$?' EXIT"
assert_contains "${ROOT}/install.sh" 'mokutil --test-key'
assert_contains "${ROOT}/install.sh" 'CONFIG_DRM_VKMS=y'
assert_contains "${ROOT}/install.sh" 'Distro VKMS remains untouched'
assert_contains "${ROOT}/install.sh" 'remove_legacy_versions'
assert_contains "${ROOT}/install.sh" 'PATH=/usr/sbin:/usr/bin:/sbin:/bin'
assert_contains "${ROOT}/install.sh" 'flock -n 9'
assert_contains "${ROOT}/uninstall.sh" 'restore_legacy_original_modules'
assert_contains "${ROOT}/scripts/verify-install.sh" 'Distro vkms remains in its packaged module tree'
assert_contains "${ROOT}/install.sh" 'install_bootstrap_service'
assert_not_contains "${ROOT}/install.sh" 'systemctl start monitorize-vkms-bootstrap.service'
assert_contains "${ROOT}/systemd/monitorize-vkms-bootstrap.service" 'Before=display-manager.service'
assert_contains "${ROOT}/systemd/monitorize-vkms-bootstrap.service" 'Requires=sys-kernel-config.mount'
assert_contains "${ROOT}/systemd/monitorize-vkms-bootstrap.service" 'After=sys-kernel-config.mount'
assert_contains "${ROOT}/scripts/monitorize-vkms-bootstrap.sh" "printf '1' > \"\${CONNECTOR}/dynamic\""
assert_contains "${ROOT}/scripts/monitorize-vkms-bootstrap.sh" "printf '0' > \"\${CONNECTOR}/enabled\""
assert_contains "${ROOT}/scripts/monitorize-vkms-bootstrap.sh" 'find_monitorize_drm_card'

printf 'monitorize-vkms safety checks passed\n'
