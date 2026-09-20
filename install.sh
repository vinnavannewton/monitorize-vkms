#!/usr/bin/env bash
# Safely install monitorize-vkms through DKMS for the currently running kernel.

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

readonly PACKAGE_NAME="monitorize-vkms"
readonly MODULE_NAME="monitorize_vkms"
readonly REPOSITORY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MODULE_PACKAGE_VERSION="$(<"${REPOSITORY_DIR}/VERSION")"
readonly KERNEL="$(uname -r)"
readonly KERNEL_BUILD_DIR="/lib/modules/${KERNEL}/build"
readonly SOURCE_DIR="/usr/src/${PACKAGE_NAME}-${MODULE_PACKAGE_VERSION}"
readonly MODPROBE_CONFIG="/etc/modprobe.d/monitorize-vkms.conf"
readonly STATE_DIR="/var/lib/monitorize-vkms"
readonly CONFIG_BACKUP="${STATE_DIR}/preexisting-modprobe.conf"
readonly LOCK_PATH="/run/lock/monitorize-vkms.lock"
readonly BOOTSTRAP_SOURCE="${REPOSITORY_DIR}/scripts/monitorize-vkms-bootstrap.sh"
readonly BOOTSTRAP_UNIT_SOURCE="${REPOSITORY_DIR}/systemd/monitorize-vkms-bootstrap.service"
readonly BOOTSTRAP_PATH="/usr/libexec/monitorize-vkms/monitorize-vkms-bootstrap"
readonly BOOTSTRAP_UNIT_PATH="/etc/systemd/system/monitorize-vkms-bootstrap.service"
readonly HELPER_SOURCE="${REPOSITORY_DIR}/monitorize_vkms/helper.py"
readonly HELPER_PATH="/usr/libexec/monitorize-vkms/monitorize-vkms-helper"
readonly POLKIT_POLICY_SOURCE="${REPOSITORY_DIR}/packaging/io.github.vinnavannewton.monitorize-vkms.policy"
readonly POLKIT_POLICY_PATH="/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy"
readonly CLI_SOURCE="${REPOSITORY_DIR}/scripts/monitorize-vkms-cli"
readonly CLI_PATH="/usr/bin/monitorize-vkms"
readonly PYTHON_LIB_DIR="/usr/lib/monitorize-vkms"

DISTRO_FAMILY=""
DISTRO_NAME="Linux"
SECURE_BOOT_STATE="unknown"
WORK_DIR=""
NEW_PACKAGE_ADDED=0
SOURCE_STAGED=0
INSTALL_COMMITTED=0
BOOTSTRAP_INSTALLED=0
CLI_INSTALLED=0

log() { printf '[Monitorize VKMS] %s\n' "$*"; }
warn() { printf '[Monitorize VKMS] Warning: %s\n' "$*" >&2; }
die() { printf '[Monitorize VKMS] Error: %s\n' "$*" >&2; exit 1; }

require_root() {
	(( EUID == 0 )) || die "Please run: sudo ./install.sh"
}

validate_metadata() {
	[[ "$MODULE_PACKAGE_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9.+_-]*$ ]] ||
		die "Invalid VERSION: $MODULE_PACKAGE_VERSION"
	grep -Fqx "PACKAGE_VERSION=\"${MODULE_PACKAGE_VERSION}\"" "${REPOSITORY_DIR}/dkms.conf" ||
		die "dkms.conf PACKAGE_VERSION must match VERSION (${MODULE_PACKAGE_VERSION})"
	grep -Fqx "BUILT_MODULE_NAME[0]=\"${MODULE_NAME}\"" "${REPOSITORY_DIR}/dkms.conf" ||
		die "dkms.conf must build ${MODULE_NAME}, not replace the distro vkms module"
}

detect_distro() {
	[[ -r /etc/os-release ]] || die "Unsupported distribution: /etc/os-release is unavailable"
	# shellcheck disable=SC1091
	. /etc/os-release
	local id="${ID:-unknown}"
	local like="${ID_LIKE:-}"
	DISTRO_NAME="${PRETTY_NAME:-$id}"

	case "$id" in
		fedora|rhel|centos|rocky|almalinux) DISTRO_FAMILY="fedora" ;;
		ubuntu|debian) DISTRO_FAMILY="debian" ;;
		arch|manjaro|endeavouros) DISTRO_FAMILY="arch" ;;
		nixos) die "Unsupported distribution: NixOS is intentionally unsupported" ;;
		opensuse*|sles) DISTRO_FAMILY="suse" ;;
		*)
			case " ${like} " in
				*' rhel '*|*' fedora '*) DISTRO_FAMILY="fedora" ;;
				*' debian '*|*' ubuntu '*) DISTRO_FAMILY="debian" ;;
				*' arch '*) DISTRO_FAMILY="arch" ;;
				*' suse '*|*' opensuse '*) DISTRO_FAMILY="suse" ;;
				*) die "Unsupported distribution (ID=${id}, ID_LIKE=${like})" ;;
			esac
			;;
	esac
	log "Detected ${DISTRO_NAME}"
}

print_dependency_help() {
	printf '[Monitorize VKMS] Install the missing prerequisites yourself, then rerun this script.\n' >&2
	printf '[Monitorize VKMS] The installer will never invoke a package manager or install a kernel.\n' >&2
	case "$DISTRO_FAMILY" in
		fedora)
			printf '[Monitorize VKMS] Fedora: install dkms, gcc, make, and the exact headers for %s.\n' "$KERNEL" >&2
			;;
		debian)
			printf '[Monitorize VKMS] Debian/Ubuntu: install dkms, build-essential, and linux-headers-%s.\n' "$KERNEL" >&2
			;;
		arch)
			printf '[Monitorize VKMS] Arch: fully upgrade/reboot first, then install dkms, base-devel, and matching kernel headers.\n' >&2
			;;
		suse)
			printf '[Monitorize VKMS] openSUSE: fully update/reboot first, then install dkms, gcc, make, and matching kernel-devel packages.\n' >&2
			;;
	esac
}

check_prerequisites() {
	local missing=()
	local command
	for command in dkms make gcc install modinfo depmod find sort flock; do
		command -v "$command" >/dev/null || missing+=("$command")
	done
	[[ -f "${KERNEL_BUILD_DIR}/Makefile" ]] || missing+=("${KERNEL_BUILD_DIR}/Makefile")

	if ((${#missing[@]})); then
		printf '[Monitorize VKMS] Missing prerequisites: %s\n' "${missing[*]}" >&2
		print_dependency_help
		die "Prerequisite check failed before any system change"
	fi
	log "Prerequisites are already installed for running kernel ${KERNEL}"
}

acquire_global_lock() {
	exec 9>"$LOCK_PATH"
	flock -n 9 || die "Another Monitorize VKMS installation or display operation is running"
}

check_replaceable_module() {
	local config
	for config in "/boot/config-${KERNEL}" "${KERNEL_BUILD_DIR}/.config"; do
		[[ -r "$config" ]] || continue
		if grep -Fqx 'CONFIG_DRM_VKMS=y' "$config"; then
			die "${KERNEL} has VKMS built into the kernel; an external VKMS implementation cannot safely coexist"
		fi
		return
	done
	die "Kernel configuration is unavailable; built-in VKMS cannot be ruled out safely"
}

check_secure_boot() {
	# Legacy BIOS guests have no EFI variables, even if mokutil is installed.
	if [[ ! -d /sys/firmware/efi ]]; then
		SECURE_BOOT_STATE="disabled"
		log "Secure Boot state: disabled (non-EFI boot)"
		return
	fi

	if ! command -v mokutil >/dev/null; then
		die "mokutil is required to determine Secure Boot state on this EFI system"
	fi

	local state
	state="$(mokutil --sb-state 2>&1)" || die "Could not determine Secure Boot state: ${state}"
	case "$state" in
		*[Ee]nabled*) SECURE_BOOT_STATE="enabled" ;;
		*[Dd]isabled*) SECURE_BOOT_STATE="disabled" ;;
		*) die "Unrecognized Secure Boot state: ${state}" ;;
	esac
	log "Secure Boot state: ${SECURE_BOOT_STATE}"
}

prepare_source_tree() {
	local target="$1"
	install -d -m 0755 "${target}/src/vkms" "${target}/scripts"
	install -m 0644 "${REPOSITORY_DIR}/VERSION" "${REPOSITORY_DIR}/dkms.conf" "${target}/"
	install -m 0644 "${REPOSITORY_DIR}/src/vkms/Makefile" "${REPOSITORY_DIR}/src/vkms/ORIGIN.md" \
		"${target}/src/vkms/"
	install -m 0755 "${REPOSITORY_DIR}/scripts/probe-drm-atomic-api.sh" "${target}/scripts/"

	local source base
	for source in "${REPOSITORY_DIR}"/src/vkms/*.c "${REPOSITORY_DIR}"/src/vkms/*.h; do
		base="${source##*/}"
		[[ "$base" == "vkms_oot_features.h" ]] && continue
		install -m 0644 "$source" "${target}/src/vkms/${base}"
	done
	[[ -f "${target}/src/vkms/vkms_drv.c" ]] || die "VKMS source is incomplete"
	[[ -f "${target}/src/vkms/vkms_oot_compat.h" ]] || die "Compatibility header is missing"
	[[ -x "${target}/scripts/probe-drm-atomic-api.sh" ]] || die "Kernel API probe is missing"
}

verify_module_file() {
	local module_file="$1"
	local module_name vermagic
	module_name="$(modinfo -F name "$module_file")"
	vermagic="$(modinfo -F vermagic "$module_file")"
	[[ "$module_name" == "$MODULE_NAME" ]] ||
		die "Built module name is ${module_name}, expected ${MODULE_NAME}"
	[[ "$vermagic" == "${KERNEL}"* ]] ||
		die "Built module vermagic does not match ${KERNEL}: ${vermagic}"
	modinfo "$module_file" | grep -q '^parm: *create_default_dev:' ||
		die "Built module lacks create_default_dev"
}

prebuild_source() {
	WORK_DIR="$(mktemp -d /tmp/monitorize-vkms-install.XXXXXX)"
	local prebuild_source="${WORK_DIR}/source"
	log "Compiling a disposable compatibility preflight before changing the system"
	prepare_source_tree "$prebuild_source"
	make -C "${prebuild_source}/src/vkms" KDIR="$KERNEL_BUILD_DIR"
	verify_module_file "${prebuild_source}/src/vkms/${MODULE_NAME}.ko"
	log "Compatibility preflight passed for ${KERNEL}"
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

current_version_installed() {
	dkms status -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" -k "$KERNEL" 2>/dev/null |
		grep -Eq '[:,][[:space:]]*(installed|weak-installed)($|,)'
}

source_matches_installed() {
	local staged="${SOURCE_DIR}/src/vkms"
	[[ -d "$staged" ]] || return 1
	local source base
	for source in "${REPOSITORY_DIR}"/src/vkms/*.c "${REPOSITORY_DIR}"/src/vkms/*.h; do
		base="${source##*/}"
		[[ "$base" == "vkms_oot_features.h" ]] && continue
		cmp -s "$source" "${staged}/${base}" || return 1
	done
	return 0
}

version_uses_legacy_module_name() {
	local version="$1"
	local configuration
	[[ "$version" == "0.1.0" ]] && return 0
	for configuration in \
		"/usr/src/${PACKAGE_NAME}-${version}/dkms.conf" \
		"/var/lib/dkms/${PACKAGE_NAME}/${version}/source/dkms.conf"; do
		[[ -r "$configuration" ]] || continue
		grep -Fqx 'BUILT_MODULE_NAME[0]="vkms"' "$configuration" && return 0
	done
	return 1
}

remove_staged_source() {
	local version="$1"
	local path="/usr/src/${PACKAGE_NAME}-${version}"
	case "$path" in
		"/usr/src/${PACKAGE_NAME}-"*) rm -rf -- "$path" ;;
		*) die "Refusing to remove unexpected source path: ${path}" ;;
	esac
}

stage_dkms_source() {
	log "Staging validated DKMS source in ${SOURCE_DIR}"
	remove_staged_source "$MODULE_PACKAGE_VERSION"
	SOURCE_STAGED=1
	prepare_source_tree "$SOURCE_DIR"
	chown -R root:root "$SOURCE_DIR"
}

install_dkms() {
	if dkms status -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" 2>/dev/null | grep -q .; then
		log "Removing incomplete ${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} state"
		dkms remove -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" --all
	fi
	stage_dkms_source
	log "Adding ${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} to DKMS"
	dkms add -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION"
	NEW_PACKAGE_ADDED=1
	log "Building ${MODULE_NAME} for ${KERNEL}"
	dkms build -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" -k "$KERNEL"
	log "Installing ${MODULE_NAME} for ${KERNEL}"
	dkms install -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" -k "$KERNEL"
	depmod -a "$KERNEL"
}

remove_legacy_versions() {
	local version stock
	while IFS= read -r version; do
		[[ -n "$version" && "$version" != "$MODULE_PACKAGE_VERSION" ]] || continue
		version_uses_legacy_module_name "$version" || continue
		log "Restoring distro vkms before migrating legacy ${PACKAGE_NAME}/${version}"
		dkms remove -m "$PACKAGE_NAME" -v "$version" --all
		remove_staged_source "$version"
	done < <(dkms_versions)
	depmod -a "$KERNEL"
	stock="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null || true)"
	case "$stock" in
		"/lib/modules/${KERNEL}/kernel/"*) ;;
		"") die "Distro vkms did not return after removing the legacy replacement" ;;
		*) die "Legacy external vkms still resolves after migration: ${stock}" ;;
	esac
}

secure_boot_key_is_enrolled() {
	local certificate
	for certificate in \
		/var/lib/dkms/mok.pub \
		/var/lib/shim-signed/mok/MOK.der \
		/etc/dkms/mok.pub; do
		[[ -r "$certificate" ]] || continue
		if mokutil --test-key "$certificate" >/dev/null 2>&1; then
			log "Secure Boot trusts DKMS certificate ${certificate}"
			return 0
		fi
	done
	return 1
}

verify_installation() {
	log "Verifying isolated module installation..."
	local preferred signer
	preferred="$(modinfo -k "$KERNEL" -n "$MODULE_NAME")" ||
		die "modinfo cannot resolve ${MODULE_NAME} for ${KERNEL}"
	log "Installed Monitorize VKMS: ${preferred}"
	case "$preferred" in
		"/lib/modules/${KERNEL}/updates/"*|"/lib/modules/${KERNEL}/extra/"*) ;;
		*) die "${MODULE_NAME} is outside a recognized external-module directory" ;;
	esac
	verify_module_file "$preferred"

	# The safety invariant: installing Monitorize must not relocate or outrank
	# the distro vkms module under its original name.
	local stock
	stock="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null || true)"
	case "$stock" in
		"/lib/modules/${KERNEL}/kernel/"*) log "Distro VKMS remains untouched: ${stock}" ;;
		"") die "The distro vkms module stopped resolving after installation" ;;
		*) die "A legacy external module still overrides distro vkms: ${stock}" ;;
	esac

	if [[ "$SECURE_BOOT_STATE" == "enabled" ]]; then
		signer="$(modinfo -F signer "$preferred")"
		[[ -n "$signer" ]] || die "Secure Boot is enabled but DKMS produced an unsigned module"
		secure_boot_key_is_enrolled ||
			die "${MODULE_NAME} is signed by ${signer}, but no enrolled DKMS certificate could be verified; enroll the DKMS MOK first"
	fi
}

write_modprobe_configuration() {
	install -d -m 0755 /etc/modprobe.d "$STATE_DIR"
	local legacy_owned=0
	if [[ -f "$MODPROBE_CONFIG" ]] &&
		grep -Fq '# Monitorize creates VKMS devices through configfs' "$MODPROBE_CONFIG" &&
		grep -Fqx 'options vkms create_default_dev=0' "$MODPROBE_CONFIG"; then
		legacy_owned=1
	fi
	if [[ -f "$MODPROBE_CONFIG" ]] &&
		! grep -Fq '# Managed by monitorize-vkms' "$MODPROBE_CONFIG" &&
		((!legacy_owned)) &&
		[[ ! -e "$CONFIG_BACKUP" ]]; then
		install -m 0644 "$MODPROBE_CONFIG" "$CONFIG_BACKUP"
		log "Backed up pre-existing modprobe configuration"
	fi

	local temporary
	temporary="$(mktemp /etc/modprobe.d/.monitorize-vkms.XXXXXX)"
	printf '%s\n' \
		'# Managed by monitorize-vkms. Monitorize creates devices through configfs.' \
		'options monitorize_vkms create_default_dev=0' > "$temporary"
	chmod 0644 "$temporary"
	mv -f "$temporary" "$MODPROBE_CONFIG"
}

install_bootstrap_service() {
	[[ -x "$BOOTSTRAP_SOURCE" ]] || die "Bootstrap helper is missing or not executable"
	[[ -f "$BOOTSTRAP_UNIT_SOURCE" ]] || die "Bootstrap systemd unit is missing"
	command -v systemctl >/dev/null || die "systemctl is required for the VKMS bootstrap service"
	install -D -o root -g root -m 0755 "$BOOTSTRAP_SOURCE" "$BOOTSTRAP_PATH"
	install -D -o root -g root -m 0644 "$BOOTSTRAP_UNIT_SOURCE" "$BOOTSTRAP_UNIT_PATH"
	systemctl daemon-reload
	systemctl enable monitorize-vkms-bootstrap.service
	BOOTSTRAP_INSTALLED=1
	log "Enabled persistent VKMS bootstrap for the next boot"
}

install_cli_and_tools() {
	log "Installing standalone CLI, helper, and Polkit policy"
	install -D -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_PATH"
	if [[ -d "/usr/share/polkit-1/actions" ]]; then
		install -D -o root -g root -m 0644 "$POLKIT_POLICY_SOURCE" "$POLKIT_POLICY_PATH"
		log "Installed Polkit policy: ${POLKIT_POLICY_PATH}"
	else
		warn "Polkit actions directory not found; Polkit policy not installed"
	fi
	install -d -m 0755 "${PYTHON_LIB_DIR}/monitorize_vkms"
	install -m 0644 "${REPOSITORY_DIR}/VERSION" "${PYTHON_LIB_DIR}/"
	local pyfile
	for pyfile in "${REPOSITORY_DIR}"/monitorize_vkms/*.py; do
		install -m 0644 "$pyfile" "${PYTHON_LIB_DIR}/monitorize_vkms/"
	done
	chmod 0755 "${PYTHON_LIB_DIR}/monitorize_vkms/helper.py"
	install -D -o root -g root -m 0755 "$CLI_SOURCE" "$CLI_PATH"
	CLI_INSTALLED=1
	log "Installed standalone CLI: ${CLI_PATH}"
}

remove_old_versions() {
	local version
	while IFS= read -r version; do
		[[ -n "$version" && "$version" != "$MODULE_PACKAGE_VERSION" ]] || continue
		log "Removing superseded ${PACKAGE_NAME}/${version} after the new module was installed"
		if dkms remove -m "$PACKAGE_NAME" -v "$version" --all; then
			remove_staged_source "$version"
		else
			die "Could not remove old DKMS version ${version}; rerun uninstall.sh before retrying"
		fi
	done < <(dkms_versions)
	depmod -a "$KERNEL"
}

rollback_failed_install() {
	local status="$1"
	[[ "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf -- "$WORK_DIR"
	if ((status != 0 && ! INSTALL_COMMITTED)); then
		if ((CLI_INSTALLED)); then
			rm -f -- "$CLI_PATH" "$POLKIT_POLICY_PATH" "$HELPER_PATH"
			rm -rf -- "$PYTHON_LIB_DIR"
		fi
		if ((BOOTSTRAP_INSTALLED)); then
			systemctl disable monitorize-vkms-bootstrap.service >/dev/null 2>&1 || true
			rm -f -- "$BOOTSTRAP_UNIT_PATH" "$BOOTSTRAP_PATH"
			systemctl daemon-reload >/dev/null 2>&1 || true
		fi
		if ((NEW_PACKAGE_ADDED)); then
			warn "Installation failed; removing the new DKMS package"
			dkms remove -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" --all >/dev/null 2>&1 || true
		fi
		((SOURCE_STAGED)) && remove_staged_source "$MODULE_PACKAGE_VERSION"
		depmod -a "$KERNEL" >/dev/null 2>&1 || true
	fi
}

main() {
	require_root
	validate_metadata
	detect_distro
	check_prerequisites
	acquire_global_lock
	check_replaceable_module
	check_secure_boot
	trap 'rollback_failed_install $?' EXIT
	prebuild_source
	remove_legacy_versions

	if current_version_installed && source_matches_installed; then
		log "${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} is already installed and matches source for ${KERNEL}; verifying it"
	else
		install_dkms
	fi
	# Retire legacy same-name releases before checking that distro vkms resolves
	# back to its packaged path. A failure still rolls the new package back.
	remove_old_versions
	verify_installation
	write_modprobe_configuration
	install_bootstrap_service
	install_cli_and_tools
	INSTALL_COMMITTED=1

	printf '\n==========================================\n'
	printf 'Monitorize VKMS installed safely\n'
	printf '==========================================\n\n'
	printf 'The distro vkms.ko was not replaced. Monitorize VKMS installed. Reboot required.\n'
	printf 'At the next boot the persistent DRM card is created before the display manager.\n\n'
	printf 'Standalone CLI commands:\n'
	printf '  monitorize-vkms doctor\n'
	printf '  monitorize-vkms create 2340x1080@60\n'
	printf '  monitorize-vkms list\n'
	printf '  monitorize-vkms status\n'
	printf '  monitorize-vkms remove\n'
}

main "$@"
