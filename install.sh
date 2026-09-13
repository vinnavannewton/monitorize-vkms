#!/usr/bin/env bash
# Install monitorize-vkms through DKMS for activation after a normal reboot.

set -euo pipefail

readonly PACKAGE_NAME="monitorize-vkms"
readonly REPOSITORY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly VERSION="$(<"${REPOSITORY_DIR}/VERSION")"
readonly KERNEL="$(uname -r)"
readonly KERNEL_BUILD_DIR="/lib/modules/${KERNEL}/build"
readonly SOURCE_DIR="/usr/src/${PACKAGE_NAME}-${VERSION}"

DISTRO_FAMILY=""
SECURE_BOOT_STATE="unknown"

log() { printf '[Monitorize VKMS] %s\n' "$*"; }
warn() { printf '[Monitorize VKMS] Warning: %s\n' "$*" >&2; }
die() { printf '[Monitorize VKMS] Error: %s\n' "$*" >&2; exit 1; }

require_root() {
	(( EUID == 0 )) || die "Please run: sudo ./install.sh"
}

validate_metadata() {
	[[ "$VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9.+_-]*$ ]] || die "Invalid VERSION: $VERSION"
	grep -Fqx "PACKAGE_VERSION=\"${VERSION}\"" "${REPOSITORY_DIR}/dkms.conf" ||
		die "dkms.conf PACKAGE_VERSION must match VERSION (${VERSION})"
}

detect_distro() {
	[[ -r /etc/os-release ]] || die "Unsupported distribution: /etc/os-release is unavailable"
	# shellcheck disable=SC1091
	. /etc/os-release
	local id="${ID:-unknown}"
	local like="${ID_LIKE:-}"

	case "$id" in
		fedora|rhel|centos|rocky|almalinux)
			DISTRO_FAMILY="fedora"
			;;
		ubuntu|debian)
			DISTRO_FAMILY="debian"
			;;
		arch|manjaro|endeavouros)
			DISTRO_FAMILY="arch"
			;;
		nixos)
			die "Unsupported distribution: NixOS is intentionally unsupported"
			;;
		opensuse*|sles)
			DISTRO_FAMILY="suse"
			;;
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

	log "Detected ${PRETTY_NAME:-$id}"
}

requirements_ready() {
	command -v dkms >/dev/null &&
	command -v make >/dev/null &&
	command -v gcc >/dev/null &&
	[[ -f "${KERNEL_BUILD_DIR}/Makefile" ]]
}

install_dependencies() {
	if requirements_ready; then
		log "Build dependencies already available"
		return
	fi

	log "Checking build dependencies..."
	case "$DISTRO_FAMILY" in
		fedora)
			dnf -y install dkms gcc make "kernel-devel-${KERNEL}"
			;;
		debian)
			DEBIAN_FRONTEND=noninteractive apt-get update
			DEBIAN_FRONTEND=noninteractive apt-get install -y \
				dkms build-essential "linux-headers-${KERNEL}"
			;;
		arch)
			local pkgbase_file="/usr/lib/modules/${KERNEL}/pkgbase"
			[[ -r "$pkgbase_file" ]] ||
				die "Cannot determine the Arch headers package for ${KERNEL}: ${pkgbase_file} is missing"
			local pkgbase
			pkgbase="$(<"$pkgbase_file")"
			[[ -n "$pkgbase" ]] || die "Arch pkgbase metadata is empty for ${KERNEL}"
			pacman -S --needed --noconfirm dkms base-devel "${pkgbase}-headers"
			;;
		suse)
			local flavor="${KERNEL##*-}"
			zypper --non-interactive install -y dkms gcc make "kernel-${flavor}-devel"
			;;
		*)
			die "Unsupported distribution family"
			;;
	esac
}

check_kernel_build_tree() {
	log "Kernel: ${KERNEL}"
	log "Kernel build directory: ${KERNEL_BUILD_DIR}"
	[[ -f "${KERNEL_BUILD_DIR}/Makefile" ]] ||
		die "Missing kernel build tree: ${KERNEL_BUILD_DIR}/Makefile"
	command -v dkms >/dev/null || die "dkms is unavailable after dependency setup"
	command -v make >/dev/null || die "make is unavailable after dependency setup"
	command -v gcc >/dev/null || die "gcc is unavailable after dependency setup"
}

check_secure_boot() {
	if ! command -v mokutil >/dev/null; then
		log "Secure Boot state: unknown (mokutil is not installed)"
		return
	fi

	local state
	if ! state="$(mokutil --sb-state 2>&1)"; then
		warn "Secure Boot state: unknown (${state})"
		return
	fi
	case "$state" in
		*[Ee]nabled*) SECURE_BOOT_STATE="enabled" ;;
		*[Dd]isabled*) SECURE_BOOT_STATE="disabled" ;;
		*) SECURE_BOOT_STATE="unknown" ;;
	esac
	log "Secure Boot state: ${SECURE_BOOT_STATE}"
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
	local version="$1"
	local path="/usr/src/${PACKAGE_NAME}-${version}"
	case "$path" in
		"/usr/src/${PACKAGE_NAME}-"*) rm -rf -- "$path" ;;
		*) die "Refusing to remove unexpected source path: ${path}" ;;
	esac
}

remove_old_monitorize_dkms() {
	local version
	while IFS= read -r version; do
		[[ -n "$version" ]] || continue
		log "Removing existing ${PACKAGE_NAME}/${version} DKMS package"
		dkms remove -m "$PACKAGE_NAME" -v "$version" --all
		remove_staged_source "$version"
	done < <(dkms_versions)
}

stage_dkms_source() {
	log "Staging DKMS source in ${SOURCE_DIR}"
	remove_staged_source "$VERSION"
	install -d -m 0755 "${SOURCE_DIR}/src/vkms" "${SOURCE_DIR}/scripts"
	install -m 0644 "${REPOSITORY_DIR}/VERSION" "${REPOSITORY_DIR}/dkms.conf" "${SOURCE_DIR}/"
	install -m 0644 "${REPOSITORY_DIR}/src/vkms/Makefile" "${REPOSITORY_DIR}/src/vkms/ORIGIN.md" \
		"${SOURCE_DIR}/src/vkms/"
	install -m 0755 "${REPOSITORY_DIR}/scripts/probe-drm-atomic-api.sh" "${SOURCE_DIR}/scripts/"

	local source base
	for source in "${REPOSITORY_DIR}"/src/vkms/*.c "${REPOSITORY_DIR}"/src/vkms/*.h; do
		base="${source##*/}"
		[[ "$base" == "vkms_oot_features.h" ]] && continue
		install -m 0644 "$source" "${SOURCE_DIR}/src/vkms/${base}"
	done

	chown -R root:root "$SOURCE_DIR"
	[[ -f "${SOURCE_DIR}/dkms.conf" ]] || die "DKMS configuration was not staged"
	[[ -f "${SOURCE_DIR}/src/vkms/vkms_drv.c" ]] || die "VKMS source was not staged"
	[[ -f "${SOURCE_DIR}/src/vkms/vkms_oot_compat.h" ]] || die "Compatibility header was not staged"
	[[ -x "${SOURCE_DIR}/scripts/probe-drm-atomic-api.sh" ]] || die "Atomic API probe was not staged"
	[[ ! -e "${SOURCE_DIR}/src/vkms/vkms_oot_features.h" ]] ||
		die "Generated feature header must not be staged"
}

install_dkms() {
	log "Adding ${PACKAGE_NAME}/${VERSION} to DKMS"
	dkms add -m "$PACKAGE_NAME" -v "$VERSION"
	log "Building vkms for ${KERNEL}"
	dkms build -m "$PACKAGE_NAME" -v "$VERSION" -k "$KERNEL"
	log "Installing DKMS module for ${KERNEL}"
	dkms install -m "$PACKAGE_NAME" -v "$VERSION" -k "$KERNEL"
	depmod -a "$KERNEL"
}

rollback_unsigned_install() {
	warn "Rolling back the unsigned DKMS module to preserve the distro VKMS path"
	dkms remove -m "$PACKAGE_NAME" -v "$VERSION" --all
	remove_staged_source "$VERSION"
	depmod -a "$KERNEL"
}

verify_installation() {
	log "Verifying module precedence..."
	local preferred distro_module module_name vermagic signer
	preferred="$(modinfo -k "$KERNEL" -n vkms)" || die "modinfo cannot resolve vkms for ${KERNEL}"
	distro_module=""
	local distro_directory="/lib/modules/${KERNEL}/kernel/drivers/gpu/drm/vkms"
	if [[ -d "$distro_directory" ]]; then
		distro_module="$(find "$distro_directory" -maxdepth 1 -type f \
			-name 'vkms.ko*' -print -quit)"
	fi
	log "Preferred VKMS: ${preferred}"
	[[ -n "$distro_module" ]] && log "Distro VKMS: ${distro_module}"
	case "$preferred" in
		"/lib/modules/${KERNEL}/updates/"*|"/lib/modules/${KERNEL}/extra/"*) ;;
		*) die "DKMS module is not preferred over the distro VKMS module" ;;
	esac

	module_name="$(modinfo -F name "$preferred")"
	vermagic="$(modinfo -F vermagic "$preferred")"
	[[ "$module_name" == "vkms" ]] || die "Installed module name is ${module_name}, expected vkms"
	[[ "$vermagic" == "${KERNEL}"* ]] || die "Installed module vermagic does not match ${KERNEL}: ${vermagic}"
	modinfo "$preferred" | grep -q '^parm: *create_default_dev:' ||
		die "Installed module lacks create_default_dev"

	if [[ "$SECURE_BOOT_STATE" == "enabled" ]]; then
		signer="$(modinfo -F signer "$preferred")"
		if [[ -z "$signer" ]]; then
			rollback_unsigned_install
			die "Secure Boot is enabled and DKMS produced an unsigned module. Configure MOK/module signing, then rerun the installer."
		fi
		log "DKMS module signer: ${signer}"
		warn "Confirm that the signing key is trusted by this system before rebooting; the installer does not enroll MOK keys automatically."
	fi
}

write_modprobe_configuration() {
	install -d -m 0755 /etc/modprobe.d
	cat > /etc/modprobe.d/monitorize-vkms.conf <<'EOF'
# Monitorize creates VKMS devices through configfs; do not create the legacy
# default virtual display when vkms is loaded.
options vkms create_default_dev=0
EOF
	chmod 0644 /etc/modprobe.d/monitorize-vkms.conf
}

main() {
	require_root
	validate_metadata
	detect_distro
	install_dependencies
	check_kernel_build_tree
	check_secure_boot
	remove_old_monitorize_dkms
	stage_dkms_source
	install_dkms
	verify_installation
	write_modprobe_configuration

	printf '\n==========================================\n'
	printf 'Monitorize VKMS installed successfully\n'
	printf '==========================================\n\n'
	printf 'Reboot to activate the new VKMS module:\n\n    sudo reboot\n'
}

main "$@"
