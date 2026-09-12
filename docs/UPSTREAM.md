# Upstream VKMS Tracking

monitorize-vkms is based on the Linux kernel VKMS driver.

The goal of this repository is NOT to create a custom DRM driver.

It temporarily carries upstream-in-development VKMS functionality
required by Monitorize until equivalent functionality is available
in normal Linux distribution kernels.

## Current carried patch series

Series:
[PATCH v5 00/38] VKMS: Introduce multiple configFS attributes

Author:
Louis Chauvet <louis.chauvet@bootlin.com>

Message-ID:
20260627-vkms-all-config-v5-0-854aa0840926@bootlin.com

Base:
drm-misc-next

Important functionality for Monitorize:
- Per-connector EDID configuration
- Configfs EDID binary attribute
- Configfs EDID enable/disable attribute

Relevant patches:
- PATCH v5 32/38: drm/vkms: Introduce config for connector EDID
- PATCH v5 33/38: drm/vkms: Introduce configfs for connector EDID

Policy:
- Preserve original upstream authorship and Signed-off-by metadata.
- Keep Monitorize-specific behavior out of the VKMS kernel driver.
- Carry the smallest practical upstream patchset.
- Remove downstream patches when equivalent support lands upstream.
