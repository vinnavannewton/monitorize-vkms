// SPDX-License-Identifier: GPL-2.0+
/*
 * Out-of-tree compatibility helpers for DRM symbols that are available in
 * the VKMS upstream development series but unavailable or unexported in
 * stock distro kernels.
 *
 * These helpers are diagnostic only and should be removed when the required
 * DRM helpers are available to external modules.
 */

#ifndef _VKMS_OOT_COMPAT_H_
#define _VKMS_OOT_COMPAT_H_

#include <drm/drm_atomic.h>
#include <drm/drm_color_mgmt.h>
#include <drm/drm_connector.h>
#include <drm/drm_plane.h>

#include "vkms_oot_features.h"

#if VKMS_OOT_HAS_DRM_ATOMIC_COMMIT
typedef struct drm_atomic_commit vkms_oot_atomic_state_t;
#else
typedef struct drm_atomic_state vkms_oot_atomic_state_t;
#endif

/*
 * Keep the implementations private and alias only this translation unit's
 * call sites. Function declarations cannot be feature-tested by the C
 * preprocessor; private names avoid redeclaration conflicts with future DRM
 * headers while retaining the upstream call signatures.
 */
static inline const char *vkms_oot_get_plane_type_name(enum drm_plane_type type)
{
	(void)type;
	return "?";
}

static inline const char *vkms_oot_get_rotation_name(unsigned int rotation)
{
	(void)rotation;
	return "?";
}

static inline const char *
vkms_oot_get_color_encoding_name(enum drm_color_encoding encoding)
{
	(void)encoding;
	return "?";
}

static inline const char *vkms_oot_get_color_range_name(enum drm_color_range range)
{
	(void)range;
	return "?";
}

static inline const char *vkms_oot_get_colorspace_name(enum drm_colorspace colorspace)
{
	(void)colorspace;
	return "?";
}

#ifndef drm_get_plane_type_name
#define drm_get_plane_type_name vkms_oot_get_plane_type_name
#endif

#ifndef drm_get_rotation_name
#define drm_get_rotation_name vkms_oot_get_rotation_name
#endif

#ifndef drm_get_color_encoding_name
#define drm_get_color_encoding_name vkms_oot_get_color_encoding_name
#endif

#ifndef drm_get_color_range_name
#define drm_get_color_range_name vkms_oot_get_color_range_name
#endif

#ifndef drm_get_colorspace_name
#define drm_get_colorspace_name vkms_oot_get_colorspace_name
#endif

/*
 * vkms_config_show() needs one %s conversion and one const char * argument.
 * The compatibility form intentionally does not evaluate @rot, because this
 * diagnostic output has no functional effect on VKMS rotation state.
 */
#ifndef DRM_ROTATION_FMT
#define DRM_ROTATION_FMT "%s"
#endif

#ifndef DRM_ROTATION_FMT_ARGS
#define DRM_ROTATION_FMT_ARGS(rot) "?"
#endif

#endif /* _VKMS_OOT_COMPAT_H_ */
