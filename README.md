# monitorize-vkms

Standalone virtual display tool and out-of-tree VKMS kernel module for Linux.

`monitorize-vkms` enables creating real DRM/KMS virtual monitors with custom resolutions and refresh rates, complete with dynamic EDID generation and desktop layout integration for validated backends.

It can be used as a **standalone CLI tool** or as the backend for [project-monitorize](https://github.com/vinnavannewton/project-monitorize).



Monitorize VKMS is derived from Linux VKMS at commit
`6648301c5bb2ef23f0fb15bcb01d21ff66f36799` (Linux 7.1-rc5),
with VKMS changes from Louis Chauvet’s v5 “Introduce multiple
configFS attributes” patch series and Monitorize-specific adaptations.

See src/vkms/ORIGIN.md for provenance.

---

## Supported desktops

| Desktop Environments | Compatibility |
| -------------------- |:-------------:|
| KDE Plasma           | ✅             |
| GNOME                | ✅             |
| Cinnamon X11         | ✅             |
| Hyprland             | ✅ |
| Niri                 | ✅             |

Hyprland creation and removal are enabled for testing with Aquamarine 0.15.1 or newer
(version is not checked automatically). Install `wlr-randr` with `--json`
support. The current adapter confirms the output is enabled; verify the actual
resolution and refresh rate in Hyprland. Removal confirms the named output is
disabled in the compositor before disconnecting it in configfs, and refuses to
disable the last active output. Full live lifecycle validation remains pending.

---

## Standalone CLI Usage

After installing and rebooting once, you can manage virtual displays directly from your terminal:

```sh
# Create upto two virtual displays with custom resolution and refresh rate
monitorize-vkms create 2340x1080@60

# Check active virtual displays
monitorize-vkms list

# Inspect driver, configfs, and DRM status
monitorize-vkms status

# Diagnose configuration and environment health
monitorize-vkms doctor

# Remove one display, or omit its ID to remove both owned displays
monitorize-vkms remove mon1
monitorize-vkms remove mon2
monitorize-vkms remove
```

`create` fills `mon1`, then `mon2`; a third create fails.

---

## Prerequisites

Monitorize VKMS builds an out-of-tree kernel module with DKMS. Install build
files matching your currently running kernel before running the installer.
Kernel API support is determined by compiling against the installed kernel headers, not by the kernel version string alone.
The commands below are for the standard kernel in each distribution.

### Arch Linux

Update and reboot first so the kernel development files match the running
kernel:

```bash
sudo pacman -Syu
sudo reboot
```

After reboot, for the standard `linux` kernel:

```bash
sudo pacman -S --needed git dkms base-devel linux-headers python python-dbus python-gobject polkit mokutil
```

Use the header package for your kernel instead of `linux-headers` when needed:

| Kernel package   | Header package           |
| ---------------- | ------------------------ |
| `linux`          | `linux-headers`          |
| `linux-lts`      | `linux-lts-headers`      |
| `linux-zen`      | `linux-zen-headers`      |
| `linux-hardened` | `linux-hardened-headers` |

Custom and AUR kernels need their corresponding header package.

### Fedora

Update and reboot first so the kernel development files match the running
kernel:

```bash
sudo dnf update -y
sudo reboot
```

```bash
sudo dnf install -y git dkms gcc make "kernel-devel-$(uname -r)" python3 python3-dbus python3-gobject polkit mokutil
```

### Ubuntu / Debian

Update and reboot first so the kernel development files match the running kernel:

```bash
sudo apt update
sudo apt upgrade -y
sudo reboot
```

```bash
sudo apt install -y git dkms build-essential "linux-headers-$(uname -r)" python3 python3-dbus python3-gi pkexec mokutil
```

### openSUSE Tumbleweed

Update and reboot first so the kernel development files match the running
kernel:

```bash
sudo zypper dup
sudo reboot
```

After reboot, for the standard `kernel-default` kernel:

```bash
sudo zypper install git dkms gcc make kernel-devel kernel-default-devel python3 pkexec mokutil
```

Other kernel flavors need their matching `kernel-<flavor>-devel` package.

### Verify kernel headers

This is the build file the installer checks for the running kernel:

```bash
test -f "/lib/modules/$(uname -r)/build/Makefile" \
  && echo "Kernel headers OK for $(uname -r)" \
  || echo "ERROR: Matching kernel headers are missing for $(uname -r)"
```

---

## Installation

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms
sudo ./install.sh
```

If errors stop there!, else continue to reboot.

```sh
sudo reboot
```

After installing verify:

```sh
cd scripts
./verify-install.sh
```

If everything is a pass, then your done!

---

## Updating

Run as your normal user:

```sh
monitorize-vkms update
```

This fetches the official `main` branch and fast-forwards your original source
checkout, then runs `install.sh` through `sudo`. Keep that checkout available.
If the source is already up to date, it skips installation and does not rebuild.
Local changes, untracked files, local commits, and other branches stop the update
without discarding your work. Git runs as your user; only installation uses sudo.
After a successful installation, reboot to activate the installed module.

For an older installation without a saved checkout location, or if you moved it:

```sh
monitorize-vkms update --source /path/to/monitorize-vkms
```

Older CLI versions without `update` need one manual `git pull --ff-only` and
`sudo ./install.sh` from the checkout first.

The command does not upgrade your operating system or reboot automatically.
A full OS upgrade is not required for every Monitorize VKMS update. If you are
upgrading the kernel too, update your system using your distribution's normal
procedure, reboot into that kernel, ensure its matching build headers are
installed (see prerequisites above), then run `monitorize-vkms reinstall` and reboot
again. The installer builds for the **running kernel**; DKMS automatic installation
for future kernels is deliberately disabled.

To rerun installation from the existing source without fetching updates:

```sh
monitorize-vkms reinstall
```

Use this after a kernel upgrade or to retry a failed installation. It also accepts
`--source /path/to/monitorize-vkms`. The installer retains its existing behavior:
it builds for a new kernel, but verifies/reuses an already matching installation.

The existing installer still checks prerequisites, Secure Boot, and source
compatibility before system changes. Changed module build inputs must be released
with a new `VERSION`/DKMS package version; the updater does not bypass this check.
If installation fails after fetching, the checkout stays updated; resolve the
reported error and run `monitorize-vkms reinstall`.

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

- **Isolated Driver**: Module name is `monitorize_vkms`. Distro `vkms` remains at its packaged path.
- **Fail-Closed Secure Boot**: When Secure Boot is enabled, installation requires a signed module and a verified enrolled DKMS MOK certificate.
- **Stock VKMS Coexistence**: Uses a separate `monitorize-vkms` configfs namespace and exports no kernel symbols, allowing coexistence with built-in or loaded distro VKMS.
- **Failure Rollback**: Installation failures trigger rollback of installer-managed changes and restoration of saved state. If rollback is incomplete, the installer reports it and preserves recovery snapshots.

---
