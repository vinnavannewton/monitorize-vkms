#!/usr/bin/env bash
# Remove monitorize-vkms without unloading a module used by the current session.

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

readonly PACKAGE_NAME="monitorize-vkms"
readonly MODULE_NAME="monitorize_vkms"
readonly MODPROBE_CONFIG="/etc/modprobe.d/monitorize-vkms.conf"
readonly STATE_DIR="/var/lib/monitorize-vkms"
readonly CONFIG_BACKUP="${STATE_DIR}/preexisting-modprobe.conf"
readonly LOCK_PATH="/run/lock/monitorize-vkms.lock"
readonly BOOTSTRAP_PATH="/usr/libexec/monitorize-vkms/monitorize-vkms-bootstrap"
readonly BOOTSTRAP_UNIT_PATH="/etc/systemd/system/monitorize-vkms-bootstrap.service"

log() { printf '[Monitorize VKMS] %s\n' "$*"; }
warn() { printf '[Monitorize VKMS] Warning: %s\n' "$*" >&2; }
die() { printf '[Monitorize VKMS] Error: %s\n' "$*" >&2; exit 1; }

require_root() {
	(( EUID == 0 )) || die "Please run: sudo ./uninstall.sh"
}

acquire_global_lock() {
	exec 9>"$LOCK_PATH"
	flock -n 9 || die "Another Monitorize VKMS installation or display operation is running"
}

dkms_versions() {
	local status line version
	status="$(dkms status -m "$PACKAGE_NAME" 2>/dev/null || true)"
	while IFS= read -r line; do
		[[ "$line" == "${PACKAGE_NAME}/"* ]] || continue
		version="${line#"${PACKAGE_NAME}/"}"
		printf '%s\n' "${version%%,*}"
	done <<< "$status" | sort -u
}

remove_dkms_packages() {
	local version
	while IFS= read -r version; do
		[[ -n "$version" ]] || continue
		log "Removing ${PACKAGE_NAME}/${version} from DKMS"
		dkms remove -m "$PACKAGE_NAME" -v "$version" --all
	done < <(dkms_versions)
}

restore_legacy_original_modules() {
	local origin_file stored_module original_location kernel_path kernel external_module
	[[ -d "/var/lib/dkms/${PACKAGE_NAME}/original_module" ]] || return
	warn "Recovering legacy distro VKMS backups from retained DKMS state"
	while IFS= read -r -d '' origin_file; do
		stored_module="${origin_file%.origin}"
		[[ -f "$stored_module" ]] || {
			warn "Missing stored module for ${origin_file}"
			continue
		}
		IFS= read -r original_location < "$origin_file" || continue
		case "$original_location" in
			/lib/modules/*/vkms.ko|/lib/modules/*/vkms.ko.*|/lib/modules/*/vkms.ko*) ;;
			*) warn "Refusing unexpected legacy restore target: ${original_location}"; continue ;;
		esac
		install -D -m 0644 "$stored_module" "$original_location"
		log "Restored distro module ${original_location}"
		kernel_path="${original_location#/lib/modules/}"
		kernel="${kernel_path%%/*}"
		while IFS= read -r -d '' external_module; do
			[[ "$external_module" == "$original_location" ]] && continue
			case "$external_module" in
				/lib/modules/"${kernel}"/updates/*|/lib/modules/"${kernel}"/extra/*)
					log "Removing legacy same-name replacement ${external_module}"
					rm -f -- "$external_module"
					;;
			esac
		done < <(find "/lib/modules/${kernel}" -type f -name 'vkms.ko*' -print0 2>/dev/null)
	done < <(find "/var/lib/dkms/${PACKAGE_NAME}/original_module" -type f -name '*.origin' -print0)
}

remove_orphan_custom_modules() {
	local module_file
	while IFS= read -r -d '' module_file; do
		case "$module_file" in
			/lib/modules/*/updates/*|/lib/modules/*/extra/*)
				log "Removing orphan custom module ${module_file}"
				rm -f -- "$module_file"
				;;
		esac
	done < <(find /lib/modules -type f -name "${MODULE_NAME}.ko*" -print0 2>/dev/null)
}

remove_staged_sources() {
	local path
	shopt -s nullglob
	for path in "/usr/src/${PACKAGE_NAME}-"*; do
		[[ -d "$path" ]] || continue
		case "$path" in
			"/usr/src/${PACKAGE_NAME}-"*)
				log "Removing staged source ${path}"
				rm -rf -- "$path"
				;;
		esac
	done
}

restore_modprobe_configuration() {
	if [[ -f "$CONFIG_BACKUP" ]]; then
		install -m 0644 "$CONFIG_BACKUP" "$MODPROBE_CONFIG"
		rm -f -- "$CONFIG_BACKUP"
		log "Restored pre-existing modprobe configuration"
	elif [[ -f "$MODPROBE_CONFIG" ]]; then
		if grep -Fq '# Managed by monitorize-vkms' "$MODPROBE_CONFIG" ||
			{ grep -Fq '# Monitorize creates VKMS devices through configfs' "$MODPROBE_CONFIG" &&
			  grep -Fqx 'options vkms create_default_dev=0' "$MODPROBE_CONFIG"; }; then
			rm -f -- "$MODPROBE_CONFIG"
		fi
	fi
	rmdir "$STATE_DIR" 2>/dev/null || true
}

remove_bootstrap_service() {
	if command -v systemctl >/dev/null; then
		systemctl disable monitorize-vkms-bootstrap.service >/dev/null 2>&1 || true
	fi
	rm -f -- "$BOOTSTRAP_UNIT_PATH" "$BOOTSTRAP_PATH"
	rmdir /usr/libexec/monitorize-vkms 2>/dev/null || true
	if command -v systemctl >/dev/null; then
		systemctl daemon-reload || warn 'systemd daemon-reload failed'
	fi
	log 'Removed persistent VKMS bootstrap service; the running GPU was not touched'
}

refresh_module_indexes() {
	local kernel_dir kernel
	for kernel_dir in /lib/modules/*; do
		[[ -d "$kernel_dir" ]] || continue
		kernel="${kernel_dir##*/}"
		depmod -a "$kernel" || warn "depmod failed for ${kernel}"
	done
}

verify_removal() {
	local kernel_dir kernel custom stock
	for kernel_dir in /lib/modules/*; do
		[[ -d "$kernel_dir" ]] || continue
		kernel="${kernel_dir##*/}"
		custom="$(modinfo -k "$kernel" -n "$MODULE_NAME" 2>/dev/null || true)"
		[[ -z "$custom" ]] || die "${MODULE_NAME} still resolves for ${kernel}: ${custom}"
		stock="$(modinfo -k "$kernel" -n vkms 2>/dev/null || true)"
		case "$stock" in
			"/lib/modules/${kernel}/kernel/"*) log "Distro VKMS for ${kernel}: ${stock}" ;;
			"(builtin)") log "Kernel ${kernel} provides built-in VKMS" ;;
			"") warn "Kernel ${kernel} does not expose a distro vkms module" ;;
			*) die "An external vkms replacement still resolves for ${kernel}: ${stock}" ;;
		esac
	done
}

main() {
	require_root
	command -v find >/dev/null || die "find is required"
	command -v depmod >/dev/null || die "depmod is required"
	command -v modinfo >/dev/null || die "modinfo is required"
	command -v flock >/dev/null || die "flock is required"
	acquire_global_lock
	remove_bootstrap_service

	if command -v dkms >/dev/null; then
		remove_dkms_packages
	fi
	restore_legacy_original_modules
	remove_orphan_custom_modules
	remove_staged_sources
	restore_modprobe_configuration
	refresh_module_indexes
	verify_removal

	printf '\n==========================================\n'
	printf 'Monitorize VKMS removed successfully\n'
	printf '==========================================\n\n'
	printf 'No running module was forcibly unloaded. Reboot to finish returning to distro VKMS.\n'
}

main "$@"
