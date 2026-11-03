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

| Desktop Environments          | Compatibility |
| ----------------------------- |:-------------:|
| KDE Plasma                    | ✅             |
| GNOME                         | ✅             |
| Cinnamon X11                  | ✅             |
| Hyprland (Aquamarine 0.15.1+) | ✅             |
| Niri                          | ✅             |
| COSMIC                        | ❌             |

---

## Standalone CLI Usage

After installing and rebooting once, you can manage virtual displays directly from your terminal:

```sh
# Create named displays (quote names containing spaces)
monitorize-vkms create tablet 2340x1080@60
monitorize-vkms create "Monitor 1" 1920x1080@60
monitorize-vkms create "Virtual display 3" 2560x1440@60

# Check active virtual displays
monitorize-vkms list

# Inspect driver, configfs, and DRM status
monitorize-vkms status

# Verify installation without changing displays or services
monitorize-vkms verify

# Diagnose configuration and environment health
monitorize-vkms doctor

# Remove one named display, or omit its name to remove your named displays
monitorize-vkms remove tablet
monitorize-vkms remove "Monitor 1"
monitorize-vkms remove

# Update source and reinstall, reinstall without pulling, or uninstall
monitorize-vkms update
monitorize-vkms reinstall
monitorize-vkms uninstall
```

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
sudo pacman -S --needed git dkms base-devel linux-headers python python-dbus python-gobject polkit mokutil wlr-randr
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
sudo dnf install -y git dkms gcc make "kernel-devel-$(uname -r)" python3 python3-dbus python3-gobject polkit mokutil wlr-randr
```

### Ubuntu / Debian

Update and reboot first so the kernel development files match the running kernel:

```bash
sudo apt update
sudo apt upgrade -y
sudo reboot
```

```bash
sudo apt install -y git dkms build-essential "linux-headers-$(uname -r)" python3 python3-dbus python3-gi pkexec mokutil wlr-randr
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
sudo zypper install git dkms gcc make kernel-devel kernel-default-devel python3 pkexec mokutil wlr-randr
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

For an older installation without a saved checkout location, or if you moved it:

```sh
monitorize-vkms update --source /path/to/monitorize-vkms
```

To rerun installation from the existing source without fetching updates:

```sh
monitorize-vkms reinstall
```

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
monitorize-vkms uninstall
# Or, from the source checkout:
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

Hyprland VKMS needs **Aquamarine 0.15.1 or newer** for the shared-EGL teardown fix

## Automatic kernel preparation

Version 1.0.1 installs `monitorize-vkms-kernel-build.timer`. It scans installed
kernels every 15 minutes and prepares missing modules through DKMS without
blocking OS package transactions. Matching development files must be installed.
DKMS AUTOINSTALL stays disabled because this timer owns automatic preparation.
The worker never activates or unloads a display driver. Reboot normally after
preparation, then run `monitorize-vkms verify`. Per-kernel results appear in
`verify --json` and Monitorize Settings diagnostics.

With Secure Boot disabled, no MOK enrollment is requested. With Secure Boot
enabled, enroll the certificate matching the DKMS signing key (recommended), or
disable Secure Boot in BIOS/UEFI and retry. For the default DKMS certificate:
`sudo mokutil --import /var/lib/dkms/mok.pub`; set a temporary password, reboot,
and confirm Enroll MOK in the machine/VM console. Enrollment is normally once
per signing key, not once per kernel. Custom certificate locations currently
require manual trust verification; the worker reports needs attention.

Failed builds are recorded under `/var/lib/monitorize-vkms/kernel-builds`.
An unchanged failed source/header combination is not rebuilt continuously.
After investigating, remove only that kernel's JSON record as root to retry.
Missing headers and signing-trust failures are retried automatically. Existing
installed artifacts are preserved; this worker does not replace an older
installed module version. Use the normal reinstall/update path for replacement.
Automatic kernel-update acceptance across supported distros remains pending.
