# Upstream Source Provenance

Source:
Linux kernel VKMS driver

Base commit:
`6648301c5bb2ef23f0fb15bcb01d21ff66f36799`

Applied series:
`[PATCH v5 00/38] VKMS: Introduce multiple configFS attributes`

Message-ID:
`20260627-vkms-all-config-v5-0-854aa0840926@bootlin.com`

Number of applied patches:
38

Important functionality carried for Monitorize:

- VKMS configfs support
- connector configuration
- per-connector EDID storage
- connector EDID configfs interface

Policy:
These sources should remain as close as possible to upstream VKMS.
Monitorize-specific behavior belongs in userspace or in a small explicitly
separated out-of-tree compatibility layer.

Generic DRM patches from the series are **not** copied into this repository.

## Out-of-tree compatibility

Copied VKMS sources remain upstream-derived. `vkms_oot_compat.h` is a local
out-of-tree compatibility layer, included only by `vkms_config.c`. It replaces
diagnostic DRM naming helpers unavailable to an external module; no functional
DRM behavior is replaced. Generic DRM patch 6 is intentionally not required
because Monitorize uses the zero-degree default rotation.

The external-module build probes the target kernel for the optional CRTC
background-color API, including its state field, RGB helpers and exported
attachment function. Kernels without that API do not expose BACKGROUND_COLOR;
composition uses opaque black, matching the DRM default. Kernels with the
complete API retain upstream configurable-background behavior. The generated
`vkms_oot_features.h` also selects the DRM atomic aggregate type.
