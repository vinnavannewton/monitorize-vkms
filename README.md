# monitorize-vkms

Standalone virtual display tool and out-of-tree VKMS kernel module for Linux.

`monitorize-vkms` enables creating real DRM/KMS virtual monitors with custom resolutions and refresh rates (such as `2340x1080@60`, `2340x1600@60`, or `1920x1080@144`), complete with dynamic EDID generation and automatic compositor layout integration (GNOME Mutter & KDE Plasma).

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

### Foreground Mode (Streaming & Script Integration)

Run with `-f` or `--foreground` to keep the display active while the process runs, automatically destroying it when closed (or on `SIGINT` / `SIGTERM` / stdin EOF):

```sh
monitorize-vkms create 1920x1080@60 --foreground
```

### JSON Output

All commands support `--json` for machine-readable automation:

```sh
monitorize-vkms status --json
monitorize-vkms list --json
monitorize-vkms doctor --json
monitorize-vkms create 2340x1080@60 --json
```

---

## Architecture & Security

`monitorize-vkms` uses a **persistent topology, privilege-separated** architecture:

1. **Persistent DRM Device**: The `monitorize-vkms-bootstrap.service` systemd unit creates a persistent DRM card (`cardX` linked to `/sys/devices/faux/monitorize`) before the display manager starts. This ensures the display manager and compositors recognize the card safely at startup.
2. **Dynamic Connector**: The virtual monitor connector (`connector0`) is initialized as dynamic and disconnected (`enabled=0`). The DRM card remains alive permanently; only the connector state transitions dynamically.
3. **Custom EDID Generation**: When a resolution is requested, `monitorize-vkms` generates a valid 128-byte VESA EDID 1.4 block with standard CVT timing.
4. **Privilege Separation**: The CLI runs as an unprivileged user. Writes to `/sys/kernel/config/vkms` are mediated by a dedicated privileged helper (`/usr/libexec/monitorize-vkms/monitorize-vkms-helper`) authorized via Polkit (`io.github.vinnavannewton.monitorize-vkms`).
5. **Compositor Integration**:
   - **GNOME Wayland**: Automatically configured via Mutter's D-Bus `org.gnome.Mutter.DisplayConfig` interface.
   - **KDE Plasma Wayland**: Automatically configured via `kscreen-doctor`.

---

## Compatibility Status

- **Tested Distro**: Fedora 44 (`x86_64`)
- **Kernel Support**: Linux 6.1+ through current mainline / Fedora kernels
- **Desktops**: GNOME Shell / Mutter, KDE Plasma / KWin, Hyprland, Sway
- **Safety**: Out-of-tree module named `monitorize_vkms.ko`; distro `vkms.ko` is never overwritten or relocated.

The installer recognizes Fedora/RHEL-like, Debian/Ubuntu, Arch, and openSUSE systems. NixOS is intentionally unsupported.

---

## Prerequisites

Install DKMS, GCC, Make, and the matching kernel headers for your running kernel:

```sh
# Verify kernel headers are present:
test -f "/lib/modules/$(uname -r)/build/Makefile"
```

The installer deliberately does not invoke package managers (DNF, APT, Pacman, Zypper) to prevent unintended kernel upgrades.

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

To run unit tests without hardware:

```sh
python3 -m unittest discover -s tests -v
bash tests/test-safety.sh
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

- [Development and manual testing](docs/DEVELOPMENT.md)
- [Upstream tracking](docs/UPSTREAM.md)
- [Source provenance](src/vkms/ORIGIN.md)
