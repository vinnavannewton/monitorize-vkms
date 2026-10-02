# monitorize-vkms

Standalone virtual display tool and out-of-tree VKMS kernel module for Linux.

`monitorize-vkms` enables creating real DRM/KMS virtual monitors with custom resolutions and refresh rates, complete with dynamic EDID generation and automatic desktop layout integration (GNOME, KDE Plasma, and Cinnamon/X11).

It can be used as a **standalone CLI tool** or as the backend for [Monitorize](https://github.com/vinnavannewton/monitorize).

---

## Supported desktops

| Desktop Environments | Compatibility |
| -------------------- |:-------------:|
| KDE Plasma           | ✅             |
| GNOME                | ✅             |
| Hyprland             | ❌             |
| Niri                 | ✅             |
| Cinnamon X11         | ✅             |
| Cosmic               | ⚠️            |

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

Monitorize VKMS builds an out-of-tree kernel module with DKMS. Install build
files matching your currently running kernel before running the installer.
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
sudo pacman -S --needed git dkms base-devel linux-headers python polkit mokutil
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
sudo dnf install -y git dkms gcc make "kernel-devel-$(uname -r)" python3 polkit mokutil
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y git dkms build-essential "linux-headers-$(uname -r)" python3 pkexec mokutil
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
- **Stock VKMS Coexistence**: Uses a separate `monitorize-vkms` configfs
  namespace and exports no VKMS symbols, so the distro implementation may be
  built-in or loaded at the same time.
- **Atomic Rollback**: Any failure during installation immediately cleans up all staged files and DKMS registrations.

---
