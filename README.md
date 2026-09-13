# monitorize-vkms

Optional out-of-tree VKMS support for [Monitorize](https://github.com/vinnavannewton/monitorize).
It adds newer VKMS configfs features, including per-connector EDID support for
custom resolutions and refresh rates.

You need it only if your current VKMS driver lacks the `edid` and
`edid_enabled` connector attributes. Monitorize checks that capability; it
does not depend on this module by name.

## Tested

- Fedora 44, kernel `7.1.10-200.fc44.x86_64`
- KDE Plasma / KWin Wayland
- Generated EDID exposing `1920x1080 @ 75 Hz`

Other distributions and desktop environments have not been validated yet.

## Build from source

On Fedora, install the build requirements:

```sh
sudo dnf install gcc make git kernel-devel-$(uname -r)
```

Clone and build:

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms/src/vkms
make
```

Check the result:

```sh
modinfo -F name ./vkms.ko
modinfo -F vermagic ./vkms.ko
```

Expected module name: `vkms`. Its `vermagic` must match the running kernel.

> Building creates `vkms.ko`; it does not install anything, overwrite the
> distro module, or persist after reboot.

## Important notes

- This is currently a source/development build. DKMS and package installers are
  planned, not implemented.
- The module is unsigned. Secure Boot or module-signature enforcement can
  reject it.
- Rebuild after every kernel update.
- Never replace VKMS while a graphical session is using its DRM device.

## Documentation

- [Development and manual testing](docs/DEVELOPMENT.md)
- [Upstream tracking](docs/UPSTREAM.md)
- [Source provenance](src/vkms/ORIGIN.md)
