#!/usr/bin/env bash
# Remove only monitorize-vkms DKMS state. The running module is left untouched.

set -euo pipefail

readonly PACKAGE_NAME="monitorize-vkms"
readonly KERNEL="$(uname -r)"

log() { printf '[Monitorize VKMS] %s\n' "$*"; }
die() { printf '[Monitorize VKMS] Error: %s\n' "$*" >&2; exit 1; }

require_root() {
	(( EUID == 0 )) || die "Please run: sudo ./uninstall.sh"
}

dkms_versions() {
	local status line version
	if ! status="$(dkms status -m "$PACKAGE_NAME" 2>/dev/null)"; then
		return 0
	fi
	while IFS= read -r line; do
		[[ "$line" == "${PACKAGE_NAME}/"* ]] || continue
		version="${line#"${PACKAGE_NAME}/"}"
		printf '%s\n' "${version%%,*}"
	done <<< "$status" | sort -u
}

remove_staged_source() {
	local path="$1"
	case "$path" in
		"/usr/src/${PACKAGE_NAME}-"*) rm -rf -- "$path" ;;
		*) die "Refusing to remove unexpected source path: ${path}" ;;
	esac
}

remove_dkms_packages() {
	command -v dkms >/dev/null || die "dkms is required to remove ${PACKAGE_NAME}"
	local version
	while IFS= read -r version; do
		[[ -n "$version" ]] || continue
		log "Removing ${PACKAGE_NAME}/${version} from DKMS"
		dkms remove -m "$PACKAGE_NAME" -v "$version" --all
	done < <(dkms_versions)
}

remove_staged_sources() {
	local path
	shopt -s nullglob
	for path in "/usr/src/${PACKAGE_NAME}-"*; do
		[[ -d "$path" ]] || continue
		log "Removing staged source ${path}"
		remove_staged_source "$path"
	done
}

verify_distro_module() {
	local preferred
	preferred="$(modinfo -k "$KERNEL" -n vkms)" ||
		die "modinfo cannot resolve vkms for ${KERNEL} after removal"
	log "Preferred VKMS after removal: ${preferred}"
	case "$preferred" in
		"/lib/modules/${KERNEL}/kernel/drivers/gpu/drm/vkms/"*) ;;
		*) die "VKMS does not resolve to the distro module; inspect DKMS status before rebooting" ;;
	esac
}

main() {
	require_root
	remove_dkms_packages
	remove_staged_sources
	rm -f /etc/modprobe.d/monitorize-vkms.conf
	depmod -a "$KERNEL"
	verify_distro_module

	printf '\n==========================================\n'
	printf 'Monitorize VKMS removed successfully\n'
	printf '==========================================\n\n'
	printf 'Reboot to return to the distro VKMS module:\n\n    sudo reboot\n'
}

main "$@"
