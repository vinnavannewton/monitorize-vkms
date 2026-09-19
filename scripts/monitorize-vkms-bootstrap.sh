#!/usr/bin/env bash
# Create the one persistent, connector-registered Monitorize VKMS topology.

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly MODULE='monitorize_vkms'
readonly ROOT='/sys/kernel/config/vkms'
readonly DEVICE="${ROOT}/monitorize"
readonly PLANE="${DEVICE}/planes/plane0"
readonly CRTC="${DEVICE}/crtcs/crtc0"
readonly ENCODER="${DEVICE}/encoders/encoder0"
readonly CONNECTOR="${DEVICE}/connectors/connector0"

log() { printf '[Monitorize VKMS bootstrap] %s\n' "$*"; }
die() { printf '[Monitorize VKMS bootstrap] Error: %s\n' "$*" >&2; exit 1; }
read_value() { tr -d '\n' < "$1"; }

require_exact_children() {
	local directory="$1" expected="$2" child base
	for child in "${directory}"/*; do
		[[ -e "$child" ]] || continue
		[[ -d "$child" ]] || continue
		base="${child##*/}"
		[[ "$base" == "$expected" ]] || die "unexpected topology entry ${child}"
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
	for path in "$PLANE" "$CRTC" "$ENCODER" "$CONNECTOR"; do
		[[ -d "$path" ]] || die "persistent topology is incomplete: ${path}"
	done
	for path in \
		"${PLANE}/possible_crtcs/crtc0" \
		"${ENCODER}/possible_crtcs/crtc0" \
		"${CONNECTOR}/possible_encoders/encoder0"; do
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
	log "loading ${MODULE} with create_default_dev=0"
	modprobe "$MODULE" create_default_dev=0
	[[ -d "/sys/module/${MODULE}" ]] || die "${MODULE} did not load"
	[[ -d "$ROOT" ]] || die "${ROOT} is unavailable; configfs did not initialize"

	if [[ -d "$DEVICE" ]] && [[ "$(read_value "${DEVICE}/enabled")" == '1' ]]; then
		require_persistent_topology
		[[ "$(read_value "${CONNECTOR}/dynamic")" == '1' ]] || die 'existing connector is not dynamic'
		[[ "$(read_value "${CONNECTOR}/enabled")" == '1' ]] || die 'existing connector is not registered; reboot after reinstalling monitorize-vkms'
		drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
		log "persistent topology already initialized; DRM card ${drm_card}"
		return
	fi

	[[ -d "$DEVICE" ]] || mkdir "$DEVICE"
	require_exact_children "${DEVICE}/planes" plane0
	require_exact_children "${DEVICE}/crtcs" crtc0
	require_exact_children "${DEVICE}/encoders" encoder0
	require_exact_children "${DEVICE}/connectors" connector0
	ensure_dir "$PLANE"
	ensure_dir "$CRTC"
	ensure_dir "$ENCODER"
	ensure_dir "$CONNECTOR"

	log 'creating/reusing persistent topology'
	printf '1' > "${PLANE}/type"
	ensure_link "${PLANE}/possible_crtcs/crtc0" "$CRTC"
	ensure_link "${ENCODER}/possible_crtcs/crtc0" "$CRTC"
	ensure_link "${CONNECTOR}/possible_encoders/encoder0" "$ENCODER"
	printf '1' > "${CONNECTOR}/dynamic"
	printf '2' > "${CONNECTOR}/status"
	# Attach the DRM EDID property when the connector is first registered. The
	# helper can then replace or disable the EDID while the connector remains
	# registered and disconnected.
	printf '1' > "${CONNECTOR}/edid_enabled"
	printf '1' > "${CONNECTOR}/enabled"
	log 'connector registered in disconnected state for compositor discovery'
	printf '1' > "${DEVICE}/enabled"
	[[ "$(read_value "${DEVICE}/enabled")" == '1' ]] || die 'could not enable persistent VKMS device'
	[[ "$(read_value "${CONNECTOR}/dynamic")" == '1' ]] || die 'connector is not dynamic'
	[[ "$(read_value "${CONNECTOR}/enabled")" == '1' ]] || die 'connector is not registered'
	[[ "$(read_value "${CONNECTOR}/status")" == '2' ]] || die 'connector is unexpectedly connected'
	drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
	log "persistent VKMS device and disconnected connector enabled; DRM card ${drm_card} is ready for the display manager"
}

main "$@"
