# monitorize-vkms

Standalone virtual display tool and out-of-tree VKMS kernel module for Linux.

`monitorize-vkms` enables creating real DRM/KMS virtual monitors with custom resolutions and refresh rates, complete with dynamic EDID generation and automatic compositor layout integration (GNOME  & KDE Plasma).

It can be used as a **standalone CLI tool** or as the backend for [Monitorize](https://github.com/vinnavannewton/monitorize).

---

## Standalone CLI Usage

After installing and rebooting once, you can manage virtual displays directly from your terminal:

```sh
# Create a virtual display with custom resolution and refresh rate
monitorize-vkms create 2340x1080@60

# Check active virtual displays
monitorize-vkms list

# Inspect driver, configfs, and DRM status
monitorize-vkms status

# Diagnose configuration and environment health
monitorize-vkms doctor

# Remove the virtual display
monitorize-vkms remove
```

---

## Prerequisites

Install DKMS, GCC, Make, and the matching kernel headers for your running kernel:

```sh
# Verify kernel headers are present:
test -f "/lib/modules/$(uname -r)/build/Makefile"
```

---

## Installation

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms
sudo ./install.sh
sudo reboot
```

The installer:

1. Compiles a disposable compatibility preflight before modifying anything.
2. Stages and builds `monitorize_vkms.ko` via DKMS without touching stock `vkms.ko`.
3. Installs the persistent bootstrap service (`monitorize-vkms-bootstrap.service`).
4. Installs the standalone CLI (`/usr/bin/monitorize-vkms`), helper (`/usr/libexec/monitorize-vkms/monitorize-vkms-helper`), and Polkit policy (`/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy`).

Reboot once after installation to create the persistent DRM card.

---

## Verification & Troubleshooting

Check health using the built-in doctor command:

```sh
monitorize-vkms doctor
```

Or run the low-level verification script:

```sh
./scripts/verify-install.sh
```

---

## Uninstallation

```sh
sudo ./uninstall.sh
sudo reboot
```

This removes the DKMS module, bootstrap service, CLI, helper, and Polkit policy, and restores any previous modprobe configuration.

---

## Safety Invariants

- **Isolated Driver**: Module name is `monitorize_vkms`. Distro `vkms` remains untouched at its packaged path.
- **Fail-Closed Secure Boot**: Refuses unsigned modules if Secure Boot is enabled unless a valid DKMS MOK key is enrolled.
- **No Built-in Conflict**: Rejects kernels built with `CONFIG_DRM_VKMS=y`.
- **Atomic Rollback**: Any failure during installation immediately cleans up all staged files and DKMS registrations.

---

## Documentation

Wayland compositors other than GNOME/KDE require `wlr-randr` with `--json`
support and the `zwlr_output_manager_v1` protocol. Support is probed through
the actual protocol connection, including for unrecognized Wayland desktops.
Removal disables the exact Monitorize output and confirms its disabled state
before disconnecting it in configfs. Failed, unsupported, ambiguous, or timed-out
requests leave the kernel connector connected and return an error. Removing the
last enabled compositor output is refused. Creation re-enables a previously
disabled output. The DRM card and topology remain persistent.

This ordering requires live validation on each compositor. Protocol confirmation
does not guarantee completion of internal renderer cleanup. Hyprland 0.56.2 with
Aquamarine 0.15.0 has crashed on output disable itself, before kernel disconnect.
Live removal is therefore blocked in Hyprland until a compositor fix is validated:
the command returns an error and leaves the display connected. Log out of Hyprland
before removing it from a separate text console. This is crash containment, not a
working live-removal fix. No compositor restart or alternate virtual display is
used as a fallback. Hyprland uses Aquamarine, so this failure is not proof of the
same bug in wlroots compositors such as Sway.

- [Development and manual testing](docs/DEVELOPMENT.md)
- [Upstream tracking](docs/UPSTREAM.md)
- [Source provenance](src/vkms/ORIGIN.md)
