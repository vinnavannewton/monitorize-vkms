# monitorize-vkms

`monitorize-vkms` is an optional out-of-tree VKMS module for
[Monitorize](https://github.com/vinnavannewton/monitorize). It carries newer
VKMS configfs functionality, including per-connector EDID support, so
Monitorize can offer custom resolutions and refresh rates through generated
EDID data.

You need this only when the VKMS driver currently loaded by your system does
not expose the EDID configfs capability that Monitorize needs. It is a
development/source build today, not a permanent replacement for your distro
kernel module.

## Do I need monitorize-vkms?

Usually, no. Monitorize can use ordinary VKMS modes without this repository.

Use this module only when all of the following are true:

- You want a custom width, height, or refresh rate.
- The loaded VKMS implementation does not provide a connector `edid` attribute.
- The loaded VKMS implementation does not provide a connector `edid_enabled`
  attribute.

The relevant question is capability, not the package name or kernel version.
Future upstream or distro VKMS versions may already expose these attributes;
when they do, this module is unnecessary. Monitorize is intended to detect the
capability rather than assume that `monitorize-vkms` is installed.

## Tested environment

The following development path has been validated:

- Fedora 44
- Kernel `7.1.10-200.fc44.x86_64`
- KDE Plasma with KWin Wayland
- A generated 128-byte EDID that exposed `1920x1080 @ 75 Hz` through VKMS

The module builds as an external `vkms.ko`, exposes VKMS configfs, supports
connector `edid` and `edid_enabled`, and has created a virtual DRM output that
KDE exposed as `Virtual-1`.

This is not a claim of support for other distributions, desktop environments,
Secure Boot configurations, or production deployment. Broader support is a
goal, not a tested promise.

## Prerequisites

You need a C toolchain, `make`, Git, and a build tree matching the **running**
kernel. On the tested Fedora system, install the prerequisites with:

```sh
sudo dnf install \
    gcc \
    make \
    git \
    kernel-devel-$(uname -r)
```

Verify that the matching kernel build tree is available:

```sh
test -d /lib/modules/$(uname -r)/build && echo "Kernel build tree found"
```

If this prints nothing, install the matching `kernel-devel` package or boot
into a kernel for which its development files are installed. Do not build a
module for one kernel and load it into another.

## Clone

```sh
git clone https://github.com/vinnavannewton/monitorize-vkms.git
cd monitorize-vkms
```

## Build

Build from the module directory:

```sh
cd src/vkms
make
```

The build probes the target kernel's DRM atomic API through its external-module
Kbuild environment and generates the required local compatibility selection.
On success, the module is `vkms.ko` in the current directory.

Verify the result:

```sh
modinfo ./vkms.ko
modinfo -F name ./vkms.ko
modinfo -F vermagic ./vkms.ko
```

The module name must be `vkms`; its `vermagic` should match the running kernel.

> **Build is not installation.** Building `vkms.ko` does not copy it into
> `/lib/modules`, does not replace the distro module, and does not persist
> across a reboot.

## Development test

This is a temporary, current-boot-only test path for developers. It replaces
the **loaded** VKMS module, not the distro module stored on disk. Two modules
named `vkms` cannot be loaded simultaneously.

Do not unload VKMS from an active graphical session when a compositor or
application uses its DRM device. For a manual test, log out to a TTY first,
verify that no process holds the VKMS DRM card, and verify that no active
Monitorize configfs instance remains.

From `src/vkms`, after those checks have passed:

```sh
lsmod | grep '^vkms'
sudo modprobe -r vkms
sudo insmod ./vkms.ko create_default_dev=0
```

Check that the custom module is loaded and that it did not create the legacy
default virtual device automatically:

```sh
lsmod | grep '^vkms'
cat /sys/module/vkms/parameters/create_default_dev
ls -la /sys/kernel/config/vkms
```

The expected value for `create_default_dev` is `N`. Setting
`create_default_dev=0` registers VKMS/configfs support without automatically
creating the legacy default virtual display.

Do not create a display, topology, or EDID merely to test loading the module.
Normal Monitorize use is intended to manage configfs through its backend and
privileged helper.

## Verify custom-EDID support

The important feature is a connector exposing both `edid` and
`edid_enabled`, not whether a particular module file is present. Monitorize
performs this capability check itself.

For inspection of an **already existing** VKMS connector, without creating or
enabling anything manually, list the relevant attributes:

```sh
find /sys/kernel/config/vkms -type f \( -name edid -o -name edid_enabled \) -print
```

An empty result can simply mean that no VKMS connector currently exists. Do not
manually create a test topology as part of normal installation; that is an
advanced developer operation and must be cleaned up through controlled configfs
teardown.

## Restore the distro VKMS module

Only when the custom module is unused and no compositor or application holds
its DRM device, remove it normally:

```sh
sudo modprobe -r vkms
```

Confirm that normal module resolution still points to the distro-provided
module before restoring it:

```sh
modinfo -n vkms
```

It should resolve under `/lib/modules/...`, then restore the distro module:

```sh
sudo modprobe vkms create_default_dev=0
```

Because the custom module is loaded directly with `insmod` and is not copied
under `/lib/modules`, a normal reboot also returns to the distro-provided
module environment.

## Secure Boot

Development-built `vkms.ko` is unsigned. Systems enforcing Secure Boot or
kernel module signature validation can reject it. Signed-module handling and
DKMS packaging are planned; this repository does not yet include signing or a
MOK-enrolment workflow.

## Kernel updates

This is an out-of-tree module and must be rebuilt against every new kernel
build tree. Until persistent packaging exists, rebuild it manually after a
kernel update. Automatic rebuilds are not implemented.

## Configfs

Monitorize uses VKMS configuration below:

```text
/sys/kernel/config/vkms/
```

Its backend/helper is intended to manage planes, CRTCs, encoders, connectors,
and EDID. Those manual configfs operations are developer work, not part of the
normal installation flow.

## Cleanup

No persistent installation is performed today. To remove local build artifacts
from `src/vkms`:

```sh
make clean
```

For a loaded custom module, follow the controlled restore procedure above;
remove it only when unused, then reload the distro module. This is cleanup, not
a full uninstall.

## Persistent installation

DKMS packaging is planned but not implemented. This repository currently has
no `dkms.conf`, installer, uninstaller, RPM package, DEB package, or AUR
package. Do not run `dkms add` or `dkms install` for this repository.

## How Monitorize uses it

```text
Monitorize
  -> checks VKMS EDID capability
  -> generates EDID for the requested width, height, and refresh rate
  -> writes it through VKMS configfs
  -> compositor observes the resulting DRM mode
```

Monitorize chooses this path by capability detection; it does not rely on this
repository or package being present by name.

## Safety

- Never force-remove a module with `rmmod -f` or `modprobe -r --force`.
- Never use `rm -rf /sys/kernel/config/vkms`.
- Never overwrite or delete the distro's `vkms.ko` under `/lib/modules`.
- Never replace VKMS while an active graphical session is using its DRM device.
- Record errors and stop if normal module removal fails.

## Upstream and provenance

The module is derived from Linux VKMS plus the upstream-development VKMS
configfs series and a small, explicitly separated out-of-tree compatibility
layer. It is not a separate DRM driver.

For the carried upstream series and policy, see [docs/UPSTREAM.md](docs/UPSTREAM.md).
For the source base, applied-series identity, and compatibility policy, see
[src/vkms/ORIGIN.md](src/vkms/ORIGIN.md).
