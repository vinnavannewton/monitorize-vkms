# Development and manual testing

This page is for development testing only. `monitorize-vkms` is not installed
persistently: it has no DKMS metadata, package definition, or installer.

## Build

From the repository root:

```sh
cd src/vkms
make
```

The build uses `/lib/modules/$(uname -r)/build` by default and probes the
target DRM API before compiling. The result is `src/vkms/vkms.ko`.

## Temporary module test

The custom module and the distro module are both named `vkms`; only one can be
loaded at a time. Do this only from a TTY after logging out of the desktop and
confirming that no process has the VKMS DRM card open.

From `src/vkms`:

```sh
sudo modprobe -r vkms
sudo insmod ./vkms.ko create_default_dev=0
```

`create_default_dev=0` registers VKMS configfs without creating the legacy
default virtual display. Verify the load with:

```sh
lsmod | grep '^vkms'
cat /sys/module/vkms/parameters/create_default_dev
ls -la /sys/kernel/config/vkms
```

The expected parameter value is `N`.

## EDID capability

Monitorize detects `edid` and `edid_enabled` on an existing VKMS connector and
uses configfs to manage the display. Normal users should not create configfs
topology manually just to test this feature.

To inspect existing connectors without changing state:

```sh
find /sys/kernel/config/vkms -type f \( -name edid -o -name edid_enabled \) -print
```

## Restore the distro module

Only after the custom module is unused and no compositor has its DRM device
open:

```sh
sudo modprobe -r vkms
modinfo -n vkms
sudo modprobe vkms create_default_dev=0
```

`modinfo -n vkms` should resolve below `/lib/modules/...`. Since the custom
module was loaded directly and not copied there, a normal reboot also restores
the distro module environment.

## Safety

- Do not use `rmmod -f` or `modprobe -r --force`.
- Do not use recursive deletion on `/sys/kernel/config/vkms`.
- Do not overwrite or delete the distro `vkms.ko`.
- Stop and investigate if normal module removal fails.

## Kernel updates and Secure Boot

Rebuild after every kernel update. The development-built module is unsigned, so
systems enforcing Secure Boot or module-signature validation may reject it.
Persistent packaging and signed-module handling are not implemented yet.
