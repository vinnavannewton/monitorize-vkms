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
| Hyprland             | ✅             |
| Niri                 | ✅             |
| COSMIC               | ❌             |

The XRandR backend also handles native Xorg sessions on XFCE, MATE, LXQt,
Openbox and other X11 desktops when Xorg exposes the VKMS output. Install
`xrandr` for this path. These additional desktops share the Cinnamon X11
connector-ID mapping and ownership checks; per-desktop/GPU live validation is
still required. Xwayland is not a physical-output control backend.

The Monitorize app's managed Hyprland/X11 listeners require the updated host
capability declaration. Updating this source checkout alone does not update the
installed privileged helper or restart its service.

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

Each name must be unique for your user. There is no fixed CLI display-count cap;
actual limits depend on kernel resources and compositor support for additional
DRM devices. The app's existing two managed outputs remain separate.
`list` includes your named outputs and app-managed outputs, with ownership/state.
Bare `remove` processes your named displays sequentially and reports individual
failures. It does not remove another user's displays or the app/remote-login
outputs. Removal requires the session that created the display.

Names support printable text up to 128 UTF-8 bytes. Use double quotes for spaces;
single-word names work without quotes. The full name is kept for CLI lookup;
the desktop EDID label is limited to 13 ASCII characters by the EDID format.
Named outputs use independently registered DRM devices. Removed devices remain
disconnected and are reused for later names until reboot, avoiding live card
teardown while the compositor may still hold references.

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

Update also reruns installation when the source checkout is already current, so
previously pulled changes reach the installed files. Monitorize Settings can run
this source-update workflow with graphical authorization and show installation
health. A service restart does not replace an already-loaded kernel module; follow
the reported system-restart requirement after a driver update.

For an older installation without a saved checkout location, or if you moved it:

```sh
monitorize-vkms update --source /path/to/monitorize-vkms
```

To rerun installation from the existing source without fetching updates:

```sh
monitorize-vkms reinstall
```

### Bumping the source version

From this checkout, run:

```sh
./scripts/bump-version.py 1.0.1
```

Accepts `MAJOR.MINOR` or `MAJOR.MINOR.PATCH`. Synchronizes `VERSION`, DKMS,
RPM, Arch, and Debian metadata, resets package revisions, and preserves Debian
changelog history. Existing version mismatches stop the command before writing.
This edits source files only; it does not install, build, commit, push, or tag.

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

`monitorize-vkms uninstall` uses the recorded source checkout (or `--source PATH`)
and asks for sudo to run its uninstaller. It does not fetch or build. Remove named
displays and disable remote login first. Reboot afterward to finish module removal.


Hyprland VKMS needs **Aquamarine 0.15.1 or newer** for the shared-EGL teardown fix
([upstream PR #373](https://github.com/hyprwm/aquamarine/pull/373),
[release v0.15.1](https://github.com/hyprwm/aquamarine/releases/tag/v0.15.1)).
Check the installed Aquamarine package as well as Hyprland itself. Source
installation checks for `wlr-randr` before making changes; native packages require
it. On older distributions, the output adapter also supports the tool's text
output when `--json` is explicitly unsupported, retaining output identity and
last-active-output checks.
