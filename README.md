# monitorize-vkms

Standalone virtual display tool and out-of-tree VKMS kernel module for Linux.

Remote-login host support is implemented in the 0.3.0 development tree. It reserves
mon1 at 1920x1080@60 without connecting it at boot, and provides protected on-demand
greeter/desktop lifecycle operations to Monitorize. Ordinary display creation remains
available without enrollment. GNOME/GDM live boot and headless acceptance is pending;
protocol availability is not a claim that every display manager has been validated.

Native host package recipes are in `packaging/arch`, `packaging/deb/debian`, and
`packaging/rpm`. The common staging command is
`python3 scripts/stage-host-assets.py --destdir /tmp/monitorize-host-package --dkms`.
Arch: run `makepkg` from `packaging/arch` with the required dependencies installed.
DEB: copy `packaging/deb/debian` to the release tree's `debian` directory and run
`dpkg-buildpackage` in a matching Debian/Ubuntu build environment. RPM: create a
`monitorize-vkms-0.3.0.tar.gz` release source archive and build with the supplied spec.
Install kernel headers matching the target kernel before installing a host package.
Packages build/install the module through DKMS; they do not unload the running GPU.
Reboot to initialize the persistent topology when first installing the host.

The host package owns Python/helpers, bootstrap, policies, DKMS sources and
`/usr/share/monitorize-vkms/capabilities.json`. Monitorize's app package owns remote
login units and enrollment. Portable app binaries need these native system packages.
Installing packages does not enroll remote login. Before replacing/removing an
enrolled host, disable remote login in Settings and release its reservation; pairing
is retained. The Arch transaction guard rejects replacement while ownership is active.

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

### One-time unattended authorization (local implementation)

The host service supports administrator-approved enrollment for a Unix user.
Monitorize's setup offers **Enable unattended VKMS displays**, and Settings offers
Enable/Disable. After enrollment, that user's service requests can create bounded
`mon1`/`mon2` displays without recurring password prompts. Other processes running
as the same user have the same restricted access; this does not identify one
particular Python application. No password is stored.

For an existing installation, stop Monitorize sessions, update with this checkout's
`sudo ./install.sh`, then restart `monitorize-vkms.service` before testing the new
API. The installer may perform its normal DKMS verification/build and reboot
requirements; these authorization changes do not alter kernel/module behavior.
No host update is applied merely by editing or running the Monitorize GUI source.

The dedicated authorization command can also be used explicitly:

```bash
pkexec /usr/libexec/monitorize-vkms/monitorize-vkms-authorization enable
pkexec /usr/libexec/monitorize-vkms/monitorize-vkms-authorization disable
```

Authorization records are root-owned under `/var/lib/monitorize-vkms/authorization`.
They bind to the account name, UID, home and home-directory device/inode; changed
identity fails closed and requires re-enrollment. Account removal should revoke
access before reusing its UID/home. Revocation denies new silent creates but keeps
a disabled record for generation-owned cleanup. Upgrades preserve enrollment;
uninstall removes it. Enrollment is not a blanket sudo/pkexec exemption. Direct
legacy CLI/pkexec creation remains an explicit interactive administrative path.

`GetAuthorizationState` reports only the caller's authorization version/status.
`CreateDisplayUnattended` never runs interactive Polkit; ordinary `CreateDisplay`
retains interactive fallback for users who are not enrolled. Enrolled inactive
local graphical sessions are permitted, but ambiguity, another user's ownership,
remote-only sessions and existing compositor-removal guards still fail closed.
This does not enable login-screen capture or bypass logout/compositor safety.

Source validation covers grant integrity, revocation, bus identity, late-generation
cleanup and inactive-session selection. Actual root installation, repeated display
creation after the old authentication cache expires, login/lock/reconnect and
revocation recovery still require host testing.
