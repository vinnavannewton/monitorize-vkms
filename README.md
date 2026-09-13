# monitorize-vkms

Optional out-of-tree VKMS support for [Monitorize](https://github.com/vinnavannewton/monitorize).
It adds newer VKMS configfs features, including per-connector EDID support for
custom resolutions and refresh rates.

You need it only if your current VKMS driver lacks the `edid` and
`edid_enabled` connector attributes. Monitorize checks that capability; it
does not depend on this module by name.

## Compatibility status

- Fedora 44, kernel `7.1.10-200.fc44.x86_64`
- KDE Plasma / KWin Wayland
- Generated EDID exposing `1920x1080 @ 75 Hz`

The installer recognizes Fedora/RHEL-like, Debian/Ubuntu, Arch, and openSUSE
systems, but those families are not all runtime-validated yet. Unsupported
kernel APIs fail during a disposable compile preflight **before DKMS or any
system file is changed**. NixOS is intentionally unsupported.

The module is desktop-independent. Monitorize currently integrates its VKMS
outputs with KDE, GNOME, Hyprland, and Sway.

## Prerequisites

Install DKMS, GCC, Make, and the headers/build tree that exactly match the
currently running kernel. This must already exist:

```sh
test -f "/lib/modules/$(uname -r)/build/Makefile"
```

The installer deliberately does not run DNF, APT, Pacman, or Zypper. This
prevents a module installation from introducing a different or incomplete
kernel. On rolling distributions, complete the normal full system update and
reboot before installing matching headers.

## Install

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms
sudo ./install.sh
sudo reboot
```

The installer first compiles the complete source in a disposable directory.
Only a successful preflight is staged into DKMS. It installs the custom driver
as `monitorize_vkms.ko`, leaving the distro's `vkms.ko` at its packaged path,
and never unloads a running graphics module.

Reboot once after installation. Then open Monitorize, select the VKMS backend,
and launch a virtual display. Monitorize loads
`monitorize_vkms create_default_dev=0`. Check the live result with:

```sh
./scripts/verify-install.sh
```

## Uninstall

```sh
sudo ./uninstall.sh
sudo reboot
```

This removes the isolated `monitorize_vkms.ko` package and restores any
pre-existing modprobe configuration. It also understands the legacy `0.1.0`
same-name installation and asks DKMS to restore the distro module. No running
graphics module is forcibly unloaded.

## Important notes

- This is a source distribution. `install.sh` uses DKMS; distribution packages
  are not provided.
- DKMS autoinstall and weak-module propagation are disabled. Rerun the
  installer after booting each new kernel so its full compatibility and trust
  checks run before installation.
- Secure Boot detection is fail-closed. A signed module is accepted only when
  `mokutil` confirms a known DKMS certificate is enrolled. The installer does
  not disable Secure Boot or enroll keys automatically.
- Kernels with built-in `CONFIG_DRM_VKMS=y` are rejected because two VKMS
  implementations cannot safely register the same configfs subsystem.
- Installation failures remove the newly added DKMS version. The legacy
  same-name release is first removed to restore stock `vkms.ko`; later isolated
  releases are retired only after the replacement has built and installed.
- Never replace VKMS while a graphical session is using its DRM device.

## Documentation

- [Development and manual testing](docs/DEVELOPMENT.md) (manual builds,
  temporary module tests, and troubleshooting)
- [Upstream tracking](docs/UPSTREAM.md)
- [Source provenance](src/vkms/ORIGIN.md)
