Name: monitorize-vkms
Version: 1.0
Release: 1%{?dist}
Summary: Monitorize virtual display host with protected remote-login lifecycle
License: GPL-2.0-only
URL: https://github.com/vinnavannewton/monitorize-vkms
Source0: %{name}-%{version}.tar.gz
BuildArch: noarch
BuildRequires: python3 >= 3.11
BuildRequires: python3-dbus, python3-gobject
Requires: python3 >= 3.11
Requires: python3-dbus, python3-gobject, dkms, polkit, systemd, util-linux

%description
DKMS sources and userspace services for ordinary and protected boot displays.

%prep
%autosetup

%build

%install
python3 scripts/stage-host-assets.py --destdir %{buildroot} --dkms

%check
python3 -m unittest discover -s tests

%pretrans -p /bin/sh
if [ -f /usr/lib/monitorize-vkms/monitorize_vkms/package_maintenance.py ]; then
    /usr/bin/python3 -I /usr/lib/monitorize-vkms/monitorize_vkms/package_maintenance.py pre-remove || exit 1
fi

%preun
if [ "$1" = 0 ]; then
    /usr/bin/python3 -I /usr/lib/monitorize-vkms/monitorize_vkms/package_maintenance.py pre-remove || exit 1
fi

%post
/usr/bin/python3 -I /usr/lib/monitorize-vkms/monitorize_vkms/package_maintenance.py post-install

%postun
if [ "$1" = 0 ]; then
    dkms remove -m monitorize-vkms -v %{version} --all
fi
systemctl daemon-reload

%files
%{_bindir}/monitorize-vkms
%{_prefix}/lib/monitorize-vkms/
%{_prefix}/libexec/monitorize-vkms/
%{_prefix}/lib/systemd/system/monitorize-vkms*.service
%{_prefix}/lib/modprobe.d/monitorize-vkms.conf
%{_datadir}/monitorize-vkms/
%{_datadir}/dbus-1/system-services/io.github.vinnavannewton.MonitorizeVkms1.service
%{_datadir}/dbus-1/system.d/io.github.vinnavannewton.MonitorizeVkms1.conf
%{_datadir}/dbus-1/interfaces/io.github.vinnavannewton.MonitorizeVkms1.xml
%{_datadir}/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy
%{_prefix}/src/monitorize-vkms-%{version}/
%license LICENSE
