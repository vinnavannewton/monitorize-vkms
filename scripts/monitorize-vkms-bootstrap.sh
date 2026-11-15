#!/usr/bin/env bash
# Register eight independent, initially disconnected Monitorize VKMS outputs.

set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly MODULE='monitorize_vkms'
readonly LOCK_PATH='/run/lock/monitorize-vkms.lock'
readonly ROOT='/sys/kernel/config/monitorize-vkms'
readonly DEVICE="${ROOT}/monitorize"
readonly -a OUTPUTS=(0 1 2 3 4 5 6 7)

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

setup_pipeline() {
	local index="$1" plane crtc encoder connector
	plane="${DEVICE}/planes/plane${index}"
	crtc="${DEVICE}/crtcs/crtc${index}"
	encoder="${DEVICE}/encoders/encoder${index}"
	connector="${DEVICE}/connectors/connector${index}"

	ensure_dir "$plane"
	ensure_dir "$crtc"
	ensure_dir "$encoder"
	ensure_dir "$connector"
	printf '1' > "${plane}/type"
	ensure_link "${plane}/possible_crtcs/crtc${index}" "$crtc"
	ensure_link "${encoder}/possible_crtcs/crtc${index}" "$crtc"
	ensure_link "${connector}/possible_encoders/encoder${index}" "$encoder"
	printf '1' > "${connector}/dynamic"
	printf '2' > "${connector}/status"
	printf '1' > "${connector}/edid_enabled"
	printf '1' > "${connector}/enabled"
}

require_persistent_topology() {
	local index connector
	for index in "${OUTPUTS[@]}"; do
		connector="${DEVICE}/connectors/connector${index}"
		[[ -d "${DEVICE}/planes/plane${index}" &&
		   -d "${DEVICE}/crtcs/crtc${index}" &&
		   -d "${DEVICE}/encoders/encoder${index}" &&
		   -d "$connector" &&
		   -L "${DEVICE}/planes/plane${index}/possible_crtcs/crtc${index}" &&
		   -L "${DEVICE}/encoders/encoder${index}/possible_crtcs/crtc${index}" &&
		   -L "${connector}/possible_encoders/encoder${index}" ]] ||
			die 'persistent VKMS topology is incomplete; reboot after installing this version'
		[[ "$(read_value "${connector}/dynamic")" == '1' &&
		   "$(read_value "${connector}/enabled")" == '1' ]] ||
			die 'persistent connector is not registered and dynamic'
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
	local drm_card index
	[[ $EUID -eq 0 ]] || die 'must run as root'
	acquire_global_lock
	log "loading ${MODULE} with create_default_dev=0"
	modprobe "$MODULE" create_default_dev=0
	[[ -d "/sys/module/${MODULE}" ]] || die "${MODULE} did not load"
	[[ -d "$ROOT" ]] || die "${ROOT} is unavailable; configfs did not initialize"

	if [[ -d "$DEVICE" ]] && [[ "$(read_value "${DEVICE}/enabled")" == '1' ]]; then
		require_persistent_topology
		drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
		log "persistent topology already initialized; DRM card ${drm_card}"
		return
	fi

	[[ -d "$DEVICE" ]] || mkdir "$DEVICE"
	require_exact_children "${DEVICE}/planes" "${OUTPUTS[@]/#/plane}"
	require_exact_children "${DEVICE}/crtcs" "${OUTPUTS[@]/#/crtc}"
	require_exact_children "${DEVICE}/encoders" "${OUTPUTS[@]/#/encoder}"
	require_exact_children "${DEVICE}/connectors" "${OUTPUTS[@]/#/connector}"
	for index in "${OUTPUTS[@]}"; do
		setup_pipeline "$index"
	done
	printf '1' > "${DEVICE}/enabled"
	[[ "$(read_value "${DEVICE}/enabled")" == '1' ]] || die 'could not enable persistent VKMS device'
	require_persistent_topology
	drm_card="$(find_monitorize_drm_card)" || die 'persistent VKMS device has no DRM card'
	log "persistent VKMS device with eight disconnected connectors enabled; DRM card ${drm_card} is ready for the display manager"
}

main "$@"
