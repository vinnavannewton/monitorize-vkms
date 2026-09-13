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

## Install

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms
sudo ./install.sh
sudo reboot
```

The installer detects Fedora/RHEL-like, Debian/Ubuntu, Arch, and openSUSE
systems; installs the required DKMS build dependencies when needed; and stages
the custom `vkms.ko` for the **next boot**. It never unloads or replaces the
currently running VKMS module.

After reboot, open Monitorize, select the VKMS backend, and launch a virtual
display.

## Uninstall

```sh
sudo ./uninstall.sh
sudo reboot
```

This removes only the `monitorize-vkms` DKMS package and restores normal
resolution to the untouched distro VKMS module after reboot.

## Important notes

- This is a source distribution. `install.sh` uses DKMS; distribution packages
  are not provided.
- DKMS rebuilds the module for future kernels. If a future build fails, inspect
  `/var/lib/dkms/monitorize-vkms/` for its build log.
- Secure Boot is detected. An unsigned DKMS result is rejected; the installer
  does not disable Secure Boot or enroll signing keys automatically.
- Never replace VKMS while a graphical session is using its DRM device.

## Documentation

- [Development and manual testing](docs/DEVELOPMENT.md) (manual builds,
  temporary module tests, and troubleshooting)
- [Upstream tracking](docs/UPSTREAM.md)
- [Source provenance](src/vkms/ORIGIN.md)
