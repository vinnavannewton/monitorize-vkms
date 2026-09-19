#!/usr/bin/env bash
# Read-only post-reboot verification for monitorize-vkms.

set -euo pipefail

readonly PACKAGE_NAME="monitorize-vkms"
readonly MODULE_NAME="monitorize_vkms"
readonly KERNEL="$(uname -r)"

failures=0
pass() { printf '[PASS] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; failures=$((failures + 1)); }
info() { printf '[INFO] %s\n' "$*"; }

printf 'Kernel: %s\n' "$KERNEL"

custom="$(modinfo -k "$KERNEL" -n "$MODULE_NAME" 2>/dev/null || true)"
if [[ -n "$custom" ]]; then
	case "$custom" in
		"/lib/modules/${KERNEL}/updates/"*|"/lib/modules/${KERNEL}/extra/"*)
			pass "Monitorize module is installed separately: ${custom}"
			;;
		*) fail "Monitorize module has an unexpected location: ${custom}" ;;
	esac
	[[ "$(modinfo -F name "$custom")" == "$MODULE_NAME" ]] || fail "Installed module metadata has the wrong name"
	[[ "$(modinfo -F vermagic "$custom")" == "${KERNEL}"* ]] || fail "Installed module vermagic does not match the running kernel"
	modinfo "$custom" | grep -q '^parm: *create_default_dev:' || fail "Installed module lacks create_default_dev"
else
	fail "${MODULE_NAME} is not installed for ${KERNEL}"
fi

stock="$(modinfo -k "$KERNEL" -n vkms 2>/dev/null || true)"
if [[ -n "$stock" ]]; then
	case "$stock" in
		"/lib/modules/${KERNEL}/kernel/"*) pass "Distro vkms remains in its packaged module tree: ${stock}" ;;
		*) fail "Distro vkms resolves outside its packaged module tree: ${stock}" ;;
	esac
else
	fail "Distro vkms no longer resolves for ${KERNEL}"
fi

if [[ -d "/sys/module/${MODULE_NAME}" ]]; then
	pass "${MODULE_NAME} is the loaded implementation"
	parameter="$(cat -- "/sys/module/${MODULE_NAME}/parameters/create_default_dev" 2>/dev/null || true)"
	[[ "$parameter" == "N" || "$parameter" == "0" ]] || fail "create_default_dev is not disabled (${parameter:-unavailable})"
elif [[ -d /sys/module/vkms ]]; then
	fail "The stock vkms module is loaded; reboot before using Monitorize custom VKMS modes"
else
	fail "No VKMS implementation is loaded; start Monitorize once or run: sudo modprobe ${MODULE_NAME} create_default_dev=0"
fi

configfs_root=""
for candidate in /sys/kernel/config/vkms /config/vkms; do
	if [[ -d "$candidate" ]]; then
		configfs_root="$candidate"
		break
	fi
done
if [[ -n "$configfs_root" ]]; then
	pass "VKMS configfs is registered at ${configfs_root}"
	connector="$(find "$configfs_root" -mindepth 3 -maxdepth 3 -type d -path '*/connectors/*' -print -quit 2>/dev/null || true)"
	if [[ -n "$connector" ]]; then
		[[ -e "${connector}/edid" && -e "${connector}/edid_enabled" ]] &&
			pass "Connector exposes custom EDID attributes" ||
			fail "Connector is missing custom EDID attributes: ${connector}"
	else
		info "No connector exists yet; EDID attributes will be checked when Monitorize creates a display"
	fi
else
	fail "VKMS configfs is not registered"
fi

if command -v dkms >/dev/null; then
	status="$(dkms status -m "$PACKAGE_NAME" -k "$KERNEL" 2>/dev/null || true)"
	printf 'DKMS status: %s\n' "${status:-unavailable}"
	grep -Eq '[:,][[:space:]]*(installed|weak-installed)($|,)' <<< "$status" ||
		fail "DKMS does not report an installed package for ${KERNEL}"
else
	fail "dkms is unavailable"
fi

if ((failures)); then
	printf '%d verification check(s) failed.\n' "$failures" >&2
	exit 1
fi
printf 'All post-reboot safety checks passed.\n'
