#!/usr/bin/env bash
# Register two independent, initially disconnected Monitorize VKMS outputs.

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly MODULE='monitorize_vkms'
readonly LOCK_PATH='/run/lock/monitorize-vkms.lock'
readonly ROOT='/sys/kernel/config/monitorize-vkms'
readonly DEVICE="${ROOT}/monitorize"
readonly PLANE="${DEVICE}/planes/plane0"
readonly CRTC="${DEVICE}/crtcs/crtc0"
readonly ENCODER="${DEVICE}/encoders/encoder0"
readonly CONNECTOR="${DEVICE}/connectors/connector0"
readonly PLANE2="${DEVICE}/planes/plane1"
readonly CRTC2="${DEVICE}/crtcs/crtc1"
readonly ENCODER2="${DEVICE}/encoders/encoder1"
readonly CONNECTOR2="${DEVICE}/connectors/connector1"

log() { printf '[Monitorize VKMS bootstrap] %s\n' "$*"; }
die() { printf '[Monitorize VKMS bootstrap] Error: %s\n' "$*" >&2; exit 1; }
read_value() { tr -d '\n' < "$1"; }

acquire_global_lock() {
	if [[ -L "$LOCK_PATH" ]]; then
		die "refusing symlinked lifecycle lock: ${LOCK_PATH}"
	fi
	if [[ ! -e "$LOCK_PATH" ]]; then
		( set -o noclobber; : >"$LOCK_PATH" ) 2>/dev/null || true
	fi
	[[ -f "$LOCK_PATH" && -O "$LOCK_PATH" ]] || die "lifecycle lock must be a root-owned regular file: ${LOCK_PATH}"
	chmod 600 -- "$LOCK_PATH"
	exec 9<>"$LOCK_PATH"
	flock -n 9 || die 'another Monitorize VKMS install or display operation is running'
}

require_exact_children() {
	local directory="$1" child base
	shift
	for child in "${directory}"/*; do
		[[ -e "$child" ]] || continue
		[[ -d "$child" ]] || continue
		base="${child##*/}"
		[[ " $* " == *" ${base} "* ]] || die "unexpected topology entry ${child}"
	done
}

ensure_dir() {
	local path="$1"
	[[ -d "$path" ]] || mkdir "$path"
}

ensure_link() {
	local link="$1" target="$2"
	if [[ -L "$link" ]]; then
		[[ "$(readlink "$link")" == "$target" ]] || die "unexpected link ${link}"
	else
		ln -s "$target" "$link"
	fi
}

require_persistent_topology() {
	local path
	for path in "$PLANE" "$CRTC" "$ENCODER" "$CONNECTOR" "$PLANE2" "$CRTC2" "$ENCODER2" "$CONNECTOR2"; do
		[[ -d "$path" ]] || die "persistent topology is incomplete: ${path}"
	done
	for path in \
		"${PLANE}/possible_crtcs/crtc0" \
		"${ENCODER}/possible_crtcs/crtc0" \
		"${CONNECTOR}/possible_encoders/encoder0"; do
		[[ -L "$path" ]] || die "persistent topology is missing link: ${path}"
	done
	for path in \
		"${PLANE2}/possible_crtcs/crtc1" \
		"${ENCODER2}/possible_crtcs/crtc1" \
		"${CONNECTOR2}/possible_encoders/encoder1"; do
		[[ -L "$path" ]] || die "persistent topology is missing link: ${path}"
	done
}

find_monitorize_drm_card() {
	local entry base resolved
	for entry in /sys/class/drm/card*; do
		[[ -e "$entry" ]] || continue
		base="${entry##*/}"
		[[ "$base" =~ ^card[0-9]+$ ]] || continue
		[[ -e "${entry}/device" ]] || continue
		resolved="$(readlink -f "${entry}/device")"
		[[ "$resolved" == *'/faux/monitorize' ]] && {
			printf '%s\n' "$entry"
			return 0
		}
	done
	return 1
}

main() {
	local drm_card
	[[ $EUID -eq 0 ]] || die 'must run as root'
	acquire_global_lock
	log "loading ${MODULE} with create_default_dev=0"
	modprobe "$MODULE" create_default_dev=0
	[[ -d "/sys/module/${MODULE}" ]] || die "${MODULE} did not load"
	[[ -d "$ROOT" ]] || die "${ROOT} is unavailable; configfs did not initialize"

	if [[ -d "$DEVICE" ]] && [[ "$(read_value "${DEVICE}/enabled")" == '1' ]]; then
		[[ -d "$CONNECTOR2" ]] || die 'one-display topology is already registered; reboot after installing this version'
		require_persistent_topology
		[[ "$(read_value "${CONNECTOR}/dynamic")" == '1' ]] || die 'existing connector is not dynamic'
		[[ "$(read_value "${CONNECTOR}/enabled")" == '1' ]] || die 'existing connector is not registered; reboot after reinstalling monitorize-vkms'
		[[ "$(read_value "${CONNECTOR2}/dynamic")" == '1' ]] || die 'second connector is not dynamic'
		[[ "$(read_value "${CONNECTOR2}/enabled")" == '1' ]] || die 'second connector is not registered; reboot after reinstalling monitorize-vkms'
		drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
		log "persistent topology already initialized; DRM card ${drm_card}"
		return
	fi

	[[ -d "$DEVICE" ]] || mkdir "$DEVICE"
	require_exact_children "${DEVICE}/planes" plane0 plane1
	require_exact_children "${DEVICE}/crtcs" crtc0 crtc1
	require_exact_children "${DEVICE}/encoders" encoder0 encoder1
	require_exact_children "${DEVICE}/connectors" connector0 connector1
	ensure_dir "$PLANE"
	ensure_dir "$CRTC"
	ensure_dir "$ENCODER"
	ensure_dir "$CONNECTOR"
	ensure_dir "$PLANE2"
	ensure_dir "$CRTC2"
	ensure_dir "$ENCODER2"
	ensure_dir "$CONNECTOR2"

	log 'creating/reusing persistent topology'
	printf '1' > "${PLANE}/type"
	ensure_link "${PLANE}/possible_crtcs/crtc0" "$CRTC"
	ensure_link "${ENCODER}/possible_crtcs/crtc0" "$CRTC"
	ensure_link "${CONNECTOR}/possible_encoders/encoder0" "$ENCODER"
	printf '1' > "${PLANE2}/type"
	ensure_link "${PLANE2}/possible_crtcs/crtc1" "$CRTC2"
	ensure_link "${ENCODER2}/possible_crtcs/crtc1" "$CRTC2"
	ensure_link "${CONNECTOR2}/possible_encoders/encoder1" "$ENCODER2"
	printf '1' > "${CONNECTOR}/dynamic"
	printf '2' > "${CONNECTOR}/status"
	printf '1' > "${CONNECTOR2}/dynamic"
	printf '2' > "${CONNECTOR2}/status"
	# Attach the DRM EDID property when the connector is first registered. The
	# helper can then replace or disable the EDID while the connector remains
	# registered and disconnected.
	printf '1' > "${CONNECTOR}/edid_enabled"
	printf '1' > "${CONNECTOR}/enabled"
	printf '1' > "${CONNECTOR2}/edid_enabled"
	printf '1' > "${CONNECTOR2}/enabled"
	log 'connector registered in disconnected state for compositor discovery'
	printf '1' > "${DEVICE}/enabled"
	[[ "$(read_value "${DEVICE}/enabled")" == '1' ]] || die 'could not enable persistent VKMS device'
	[[ "$(read_value "${CONNECTOR}/dynamic")" == '1' ]] || die 'connector is not dynamic'
	[[ "$(read_value "${CONNECTOR}/enabled")" == '1' ]] || die 'connector is not registered'
	[[ "$(read_value "${CONNECTOR}/status")" == '2' ]] || die 'connector is unexpectedly connected'
	[[ "$(read_value "${CONNECTOR2}/status")" == '2' ]] || die 'second connector is unexpectedly connected'
	drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
	log "persistent VKMS device and disconnected connector enabled; DRM card ${drm_card} is ready for the display manager"
}

main "$@"
