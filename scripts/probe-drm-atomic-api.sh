#!/bin/sh
# SPDX-License-Identifier: GPL-2.0+
#
# Probe the target kernel's external-module build environment for the DRM
# atomic aggregate rename, optional CRTC background-color API, and colorop
# callback API. The type probes intentionally dereference or instantiate their
# targets: a mere forward declaration must not count as support.

set -eu

kdir=
output=

while test "$#" -gt 0; do
	case "$1" in
	--kdir)
		test "$#" -ge 2 || { echo "missing value for --kdir" >&2; exit 2; }
		kdir=$2
		shift 2
		;;
	--output)
		test "$#" -ge 2 || { echo "missing value for --output" >&2; exit 2; }
		output=$2
		shift 2
		;;
	*)
		echo "unknown argument: $1" >&2
		exit 2
		;;
	esac
done

test -n "$kdir" || { echo "--kdir is required" >&2; exit 2; }
test -n "$output" || { echo "--output is required" >&2; exit 2; }
test -d "$kdir" || { echo "kernel build tree is missing: $kdir" >&2; exit 2; }
test -f "$kdir/Makefile" || { echo "kernel build Makefile is missing: $kdir/Makefile" >&2; exit 2; }
test -d "$(dirname "$output")" || { echo "feature-header directory is missing: $(dirname "$output")" >&2; exit 2; }

probe_dir=$(mktemp -d "${TMPDIR:-/tmp}/monitorize-vkms-atomic-probe.XXXXXX")
trap 'rm -rf "$probe_dir"' EXIT HUP INT TERM
probe_log="$probe_dir/build.log"
feature_tmp="$probe_dir/vkms_oot_features.h"

printf '%s\n' 'obj-m := probe.o' > "$probe_dir/Makefile"
printf '%s\n' \
	'#include <linux/module.h>' \
	'#include <drm/drm_atomic.h>' \
	'' \
	'static void __maybe_unused vkms_oot_probe_type(struct drm_atomic_commit *commit)' \
	'{' \
	'	(void)commit->dev;' \
	'}' \
	'' \
	'MODULE_LICENSE("GPL");' > "$probe_dir/probe.c"

if make -C "$kdir" M="$probe_dir" modules > "$probe_log" 2>&1; then
	has_drm_atomic_commit=1
elif grep -Eq 'probe\.c:.*(invalid use of undefined type|undefined type).*drm_atomic_commit' "$probe_log"; then
	has_drm_atomic_commit=0
else
	echo "DRM atomic API probe failed for reasons other than an absent drm_atomic_commit type:" >&2
	cat "$probe_log" >&2
	exit 1
fi

# Check declarations, state layout, color helpers and the exported symbol.
# Use a referenced init function so modpost also checks external-module linkage.
background_dir="$probe_dir/background"
mkdir "$background_dir"
printf '%s\n' 'obj-m := probe.o' 'ccflags-y := -Werror=implicit-function-declaration' > "$background_dir/Makefile"
cat > "$background_dir/probe.c" <<'EOF'
#include <linux/module.h>
#include <drm/drm_crtc.h>
#include <drm/drm_blend.h>

static int __init probe_init(void)
{
	struct drm_crtc_state state = { 0 };

	drm_crtc_attach_background_color_property(NULL);
	return DRM_ARGB64_GETR(state.background_color) +
	       DRM_ARGB64_GETG(state.background_color) +
	       DRM_ARGB64_GETB(state.background_color);
}
module_init(probe_init);
MODULE_LICENSE("GPL");
EOF

if make -C "$kdir" M="$background_dir" modules > "$probe_log" 2>&1; then
	has_drm_background_color=1
elif grep -Eq '(implicit declaration|no member named|undeclared).*(drm_crtc_attach_background_color_property|background_color|DRM_ARGB64_GET[RGB])|drm_crtc_attach_background_color_property.*undefined!' "$probe_log"; then
	has_drm_background_color=0
else
	echo "DRM background-color API probe failed for reasons other than an absent API:" >&2
	cat "$probe_log" >&2
	exit 1
fi

# Linux 7.1 added a drm_colorop_funcs argument to the colorop initialization
# helpers. Build both API shapes instead of keying this on the kernel version:
# distributions may backport the change independently of the release number.
colorop_funcs_dir="$probe_dir/colorop-funcs"
colorop_funcs_log="$colorop_funcs_dir/build.log"
mkdir "$colorop_funcs_dir"
printf '%s\n' 'obj-m := probe.o' > "$colorop_funcs_dir/Makefile"
cat > "$colorop_funcs_dir/probe.c" <<'EOF'
#include <linux/module.h>
#include <drm/drm_colorop.h>

static const struct drm_colorop_funcs probe_colorop_funcs = {
	.destroy = drm_colorop_destroy,
};

static int __init probe_init(void)
{
	return drm_plane_colorop_curve_1d_init(NULL, NULL, NULL, &probe_colorop_funcs,
					      BIT(DRM_COLOROP_1D_CURVE_SRGB_EOTF),
					      DRM_COLOROP_FLAG_ALLOW_BYPASS);
}
module_init(probe_init);
MODULE_LICENSE("GPL");
EOF

if make -C "$kdir" M="$colorop_funcs_dir" modules > "$colorop_funcs_log" 2>&1; then
	has_drm_colorop_funcs=1
else
	colorop_legacy_dir="$probe_dir/colorop-legacy"
	colorop_legacy_log="$colorop_legacy_dir/build.log"
	mkdir "$colorop_legacy_dir"
	printf '%s\n' 'obj-m := probe.o' > "$colorop_legacy_dir/Makefile"
	cat > "$colorop_legacy_dir/probe.c" <<'EOF'
#include <linux/module.h>
#include <drm/drm_colorop.h>

static int __init probe_init(void)
{
	return drm_plane_colorop_curve_1d_init(NULL, NULL, NULL,
					      BIT(DRM_COLOROP_1D_CURVE_SRGB_EOTF),
					      DRM_COLOROP_FLAG_ALLOW_BYPASS);
}
module_init(probe_init);
MODULE_LICENSE("GPL");
EOF

	if make -C "$kdir" M="$colorop_legacy_dir" modules > "$colorop_legacy_log" 2>&1; then
		has_drm_colorop_funcs=0
	else
		echo "DRM colorop API probe failed for both supported signatures:" >&2
		echo "--- callback signature ---" >&2
		cat "$colorop_funcs_log" >&2
		echo "--- legacy signature ---" >&2
		cat "$colorop_legacy_log" >&2
		exit 1
	fi
fi

printf '%s\n' \
	'/* SPDX-License-Identifier: GPL-2.0+ */' \
	'/* Generated by the monitorize-vkms build; do not edit manually. */' \
	'#ifndef _VKMS_OOT_FEATURES_H_' \
	'#define _VKMS_OOT_FEATURES_H_' \
	'' \
	"#define VKMS_OOT_HAS_DRM_ATOMIC_COMMIT $has_drm_atomic_commit" \
	"#define VKMS_OOT_HAS_DRM_BACKGROUND_COLOR $has_drm_background_color" \
	"#define VKMS_OOT_HAS_DRM_COLOROP_FUNCS $has_drm_colorop_funcs" \
	'' \
	'#endif /* _VKMS_OOT_FEATURES_H_ */' > "$feature_tmp"

if test ! -f "$output" || ! cmp -s "$feature_tmp" "$output"; then
	mv "$feature_tmp" "$output"
fi
