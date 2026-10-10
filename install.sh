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
readonly DBUS_SOURCE="${REPOSITORY_DIR}/monitorize_vkms/dbus_service.py"
readonly DBUS_PATH="/usr/libexec/monitorize-vkms/monitorize-vkms-dbus-service"
readonly DBUS_UNIT_SOURCE="${REPOSITORY_DIR}/systemd/monitorize-vkms.service"
readonly DBUS_UNIT_PATH="/etc/systemd/system/monitorize-vkms.service"
readonly DBUS_ACTIVATION_SOURCE="${REPOSITORY_DIR}/packaging/io.github.vinnavannewton.MonitorizeVkms1.service"
readonly DBUS_ACTIVATION_PATH="/usr/share/dbus-1/system-services/io.github.vinnavannewton.MonitorizeVkms1.service"
readonly DBUS_POLICY_SOURCE="${REPOSITORY_DIR}/packaging/io.github.vinnavannewton.MonitorizeVkms1.conf"
readonly DBUS_POLICY_PATH="/etc/dbus-1/system.d/io.github.vinnavannewton.MonitorizeVkms1.conf"
readonly DBUS_INTERFACE_SOURCE="${REPOSITORY_DIR}/packaging/io.github.vinnavannewton.MonitorizeVkms1.xml"
readonly DBUS_INTERFACE_PATH="/usr/share/dbus-1/interfaces/io.github.vinnavannewton.MonitorizeVkms1.xml"
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
TARGET_KERNEL_ATTEMPTED=0
SOURCE_STAGED=0
INSTALL_COMMITTED=0
STOCK_VKMS_BEFORE=""
declare -a BACKUP_TARGETS=()
declare -a BACKUP_EXISTS=()
BOOTSTRAP_WAS_ENABLED=0
SYSTEM_MUTATION_STARTED=0
declare -a ROLLBACK_KERNELS=("$KERNEL")

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
			printf '[Monitorize VKMS] Fedora: sudo dnf install dkms gcc make kernel-devel-%s python3 python3-dbus python3-gobject polkit\n' "$KERNEL" >&2
			;;
		debian)
			printf '[Monitorize VKMS] Debian/Ubuntu: sudo apt install dkms build-essential linux-headers-%s python3 python3-dbus python3-gi pkexec\n' "$KERNEL" >&2
			;;
		arch)
			printf '[Monitorize VKMS] Arch: fully upgrade and reboot first.\n' >&2
			printf '[Monitorize VKMS] Install: sudo pacman -S --needed dkms base-devel python python-dbus python-gobject polkit <matching-kernel-headers>\n' >&2
			printf '[Monitorize VKMS] Examples: linux-headers, linux-lts-headers, linux-zen-headers, linux-hardened-headers\n' >&2
			;;
		suse)
			printf '[Monitorize VKMS] openSUSE: run zypper dup and reboot first; then install dkms gcc make kernel-devel kernel-default-devel python3 pkexec.\n' >&2
			;;
	esac
}

check_prerequisites() {
	[[ -d /run/systemd/system ]] || die "This source installer requires a running systemd system and systemd-logind"
	local missing=()
	local command
	for command in dkms make gcc install modinfo depmod find sort flock nm grep cmp mktemp chmod mv chown systemctl loginctl setpriv modprobe readlink tr mkdir ln python3 pkexec pkcheck; do
		command -v "$command" >/dev/null || missing+=("$command")
	done
	python3 -I -c 'import dbus; import gi; gi.require_version("GLib", "2.0"); from gi.repository import GLib' >/dev/null 2>&1 || missing+=("python3-dbus and python3-gobject/gi")
	if [[ -d /sys/firmware/efi ]] && ! command -v mokutil >/dev/null; then
		missing+=("mokutil (required on EFI systems)")
	fi
	[[ -f "${KERNEL_BUILD_DIR}/Makefile" ]] || missing+=("${KERNEL_BUILD_DIR}/Makefile")

	if ((${#missing[@]})); then
		printf '[Monitorize VKMS] Missing prerequisites: %s\n' "${missing[*]}" >&2
		print_dependency_help
		die "Prerequisite check failed before any system change"
	fi
	log "Prerequisites are already installed for running kernel ${KERNEL}"
}

acquire_global_lock() {
	if [[ -L "$LOCK_PATH" ]]; then
		die "Refusing symlinked lifecycle lock: ${LOCK_PATH}"
	fi
	if [[ ! -e "$LOCK_PATH" ]]; then
		( set -o noclobber; : >"$LOCK_PATH" ) 2>/dev/null || true
	fi
	[[ -f "$LOCK_PATH" && -O "$LOCK_PATH" ]] || die "Lifecycle lock must be a root-owned regular file: ${LOCK_PATH}"
	chmod 600 -- "$LOCK_PATH"
	exec 9<>"$LOCK_PATH"
	flock -n 9 || die "Another Monitorize VKMS installation or display operation is running"
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
	for source in VERSION dkms.conf src/vkms/Makefile scripts/probe-drm-atomic-api.sh; do
		cmp -s "${REPOSITORY_DIR}/${source}" "${SOURCE_DIR}/${source}" || return 1
	done
	for source in "${REPOSITORY_DIR}"/src/vkms/*.c "${REPOSITORY_DIR}"/src/vkms/*.h; do
		base="${source##*/}"
		[[ "$base" == "vkms_oot_features.h" ]] && continue
		cmp -s "$source" "${staged}/${base}" || return 1
	done
	for source in "${staged}"/*.c "${staged}"/*.h; do
		base="${source##*/}"
		[[ "$base" == "vkms_oot_features.h" ]] && continue
		[[ -f "${REPOSITORY_DIR}/src/vkms/${base}" ]] || return 1
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
	[[ ! -e "$SOURCE_DIR" ]] || die "Refusing to replace existing DKMS source ${SOURCE_DIR}; remove or repair it explicitly first"
	SOURCE_STAGED=1
	prepare_source_tree "$SOURCE_DIR"
	chown -R root:root "$SOURCE_DIR"
}

install_dkms() {
	if dkms status -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" 2>/dev/null | grep -q .; then
		source_matches_installed || die "Existing ${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} source differs; preserving it for manual repair. Use a new package version for changed module sources"
		log "Reusing registered ${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} source for ${KERNEL}"
	else
		stage_dkms_source
		log "Adding ${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} to DKMS"
		NEW_PACKAGE_ADDED=1
		dkms add -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION"
	fi
	TARGET_KERNEL_ATTEMPTED=1
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
		"(builtin)") log "Kernel ${KERNEL} provides built-in VKMS" ;;
		"") log "Kernel ${KERNEL} has no stock VKMS implementation" ;;
		*) die "Legacy external vkms still resolves after migration: ${stock}" ;;
	esac
}

record_stock_module() {
	# Capture the packaged module after retiring any old release that replaced it.
	STOCK_VKMS_BEFORE="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null || true)"
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

	# The safety invariant: installing Monitorize must not replace the distro
	# vkms module under its original name.
	local stock
	stock="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null || true)"
	[[ "$stock" == "$STOCK_VKMS_BEFORE" ]] ||
		die "Stock VKMS changed during installation: ${STOCK_VKMS_BEFORE:-absent} -> ${stock:-absent}"
	case "$stock" in
		"/lib/modules/${KERNEL}/kernel/"*) log "Distro VKMS remains untouched: ${stock}" ;;
		"(builtin)") log "Built-in VKMS remains untouched" ;;
		"") log "No stock VKMS implementation is installed for ${KERNEL}" ;;
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
	log "Enabled persistent VKMS bootstrap for the next boot"
}

install_cli_and_tools() {
	log "Installing standalone CLI, helper, and Polkit policy"
	install -D -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_PATH"
	install -D -o root -g root -m 0755 "$DBUS_SOURCE" "$DBUS_PATH"
	install -D -o root -g root -m 0644 "$DBUS_UNIT_SOURCE" "$DBUS_UNIT_PATH"
	install -D -o root -g root -m 0644 "$DBUS_ACTIVATION_SOURCE" "$DBUS_ACTIVATION_PATH"
	install -D -o root -g root -m 0644 "$DBUS_POLICY_SOURCE" "$DBUS_POLICY_PATH"
	install -D -o root -g root -m 0644 "$DBUS_INTERFACE_SOURCE" "$DBUS_INTERFACE_PATH"
	systemctl daemon-reload
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
	log "Installed standalone CLI: ${CLI_PATH}"
}

backup_one_target() {
	local target="$1" index backup existing
	for existing in "${BACKUP_TARGETS[@]}"; do [[ "$existing" == "$target" ]] && return 0; done
	index=${#BACKUP_TARGETS[@]}
	backup="${WORK_DIR}/system-backups/${index}"
	mkdir -p -- "${WORK_DIR}/system-backups" || return 1
	if [[ -e "$target" || -L "$target" ]]; then
		cp -a -- "$target" "$backup" || return 1
		BACKUP_EXISTS+=(1)
	else
		BACKUP_EXISTS+=(0)
	fi
	BACKUP_TARGETS+=("$target")
}

backup_install_targets() {
	local target version module_file module_directory target_kernel
	for target in "$MODPROBE_CONFIG" "$CONFIG_BACKUP" "$BOOTSTRAP_PATH" "$BOOTSTRAP_UNIT_PATH" \
		"$HELPER_PATH" "$DBUS_PATH" "$DBUS_UNIT_PATH" "$DBUS_ACTIVATION_PATH" "$DBUS_POLICY_PATH" "$DBUS_INTERFACE_PATH" \
		"$POLKIT_POLICY_PATH" "$CLI_PATH" "$PYTHON_LIB_DIR" "$SOURCE_DIR" \
		"/var/lib/dkms/${PACKAGE_NAME}"; do
		backup_one_target "$target"
	done
	# Preserve the previously installed module artifacts and legacy DKMS records
	# until the new installation has passed verification.
	for module_directory in /lib/modules/*; do
		[[ -d "$module_directory" ]] || continue
		target_kernel="${module_directory##*/}"
		for module_file in "$(modinfo -k "$target_kernel" -n "$MODULE_NAME" 2>/dev/null || true)" \
			"$(modinfo -k "$target_kernel" -n vkms 2>/dev/null || true)"; do
			case "$module_file" in
				"/lib/modules/${target_kernel}/updates/"*|"/lib/modules/${target_kernel}/extra/"*)
					backup_one_target "$module_file"
					[[ "$target_kernel" == "$KERNEL" ]] || ROLLBACK_KERNELS+=("$target_kernel") ;;
			esac
		done
	done
	while IFS= read -r version; do
		[[ -n "$version" ]] || continue
		version_uses_legacy_module_name "$version" || continue
		backup_one_target "/usr/src/${PACKAGE_NAME}-${version}"
	done < <(dkms_versions)
	if systemctl is-enabled --quiet monitorize-vkms-bootstrap.service 2>/dev/null; then
		BOOTSTRAP_WAS_ENABLED=1
	fi
}

restore_install_targets() {
	local index target backup restore_status=0
	for ((index=0; index<${#BACKUP_TARGETS[@]}; index++)); do
		target="${BACKUP_TARGETS[index]}"
		backup="${WORK_DIR}/system-backups/${index}"
		if [[ "${BACKUP_EXISTS[index]}" == 1 && ! -e "$backup" && ! -L "$backup" ]]; then
			warn "Missing rollback snapshot for ${target}; leaving its current contents in place"
			restore_status=1
			continue
		fi
		rm -rf -- "$target" || { restore_status=1; continue; }
		if [[ "${BACKUP_EXISTS[index]}" == 1 ]]; then
			mkdir -p -- "$(dirname -- "$target")" || { restore_status=1; continue; }
			cp -a -- "$backup" "$target" || restore_status=1
		fi
	done
	if ((BOOTSTRAP_WAS_ENABLED)); then
		systemctl enable monitorize-vkms-bootstrap.service >/dev/null 2>&1 || restore_status=1
	else
		systemctl disable monitorize-vkms-bootstrap.service >/dev/null 2>&1 || restore_status=1
	fi
	systemctl daemon-reload >/dev/null 2>&1 || restore_status=1
	return "$restore_status"
}

rollback_failed_install() {
	local status="$1" rollback_status=0 target_kernel
	if ((status != 0 && ! INSTALL_COMMITTED && SYSTEM_MUTATION_STARTED)); then
		if ((NEW_PACKAGE_ADDED)); then
			warn "Installation failed; removing the new DKMS package"
			dkms remove -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" --all >/dev/null 2>&1 || rollback_status=1
		elif ((TARGET_KERNEL_ATTEMPTED)); then
			dkms remove -m "$PACKAGE_NAME" -v "$MODULE_PACKAGE_VERSION" -k "$KERNEL" >/dev/null 2>&1 || rollback_status=1
		fi
		if ((${#BACKUP_TARGETS[@]})); then restore_install_targets || rollback_status=1; fi
		if ((SOURCE_STAGED && !rollback_status)); then remove_staged_source "$MODULE_PACKAGE_VERSION"; fi
		for target_kernel in "${ROLLBACK_KERNELS[@]}"; do
			depmod -a "$target_kernel" >/dev/null 2>&1 || rollback_status=1
		done
	fi
	if ((rollback_status)); then
		warn "Rollback was incomplete; recovery snapshots remain at ${WORK_DIR}"
		return 0
	fi
	[[ "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf -- "$WORK_DIR"
}

main() {
	require_root
	validate_metadata
	detect_distro
	check_prerequisites
	acquire_global_lock
	check_secure_boot
	trap 'rollback_failed_install $?' EXIT
	prebuild_source
	backup_install_targets
	SYSTEM_MUTATION_STARTED=1
	remove_legacy_versions
	record_stock_module

	if current_version_installed && source_matches_installed; then
		log "${PACKAGE_NAME}/${MODULE_PACKAGE_VERSION} is already installed and matches source for ${KERNEL}; verifying it"
	else
		install_dkms
	fi
	# Retire legacy same-name releases before checking that distro vkms resolves
	# back to its packaged path. A failure still rolls the new package back.
	verify_installation
	write_modprobe_configuration
	install_bootstrap_service
	install_cli_and_tools
	INSTALL_COMMITTED=1

	printf '\n==========================================\n'
	printf 'Monitorize VKMS installed safely\n'
	printf '==========================================\n\n'
	printf 'Stock VKMS state was preserved. Monitorize VKMS installed. Reboot required.\n'
	printf 'At the next boot the persistent DRM card is created before the display manager.\n\n'
	printf 'Standalone CLI commands:\n'
	printf '  monitorize-vkms doctor\n'
	printf '  monitorize-vkms create 2340x1080@60\n'
	printf '  monitorize-vkms list\n'
	printf '  monitorize-vkms status\n'
	printf '  monitorize-vkms remove\n'
}

main "$@"
