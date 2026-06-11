#!/bin/bash
# rpmbuild-only against the persisted, already-bundled /build/pkgroot.
set -euxo pipefail
exec > /out/rpmonly.log 2>&1
dnf -y install rpm-build findutils >/dev/null
test -d /build/pkgroot/usr/lib/copperai || { echo "FATAL: staged tree missing"; exit 1; }

mkdir -p /root/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,BUILDROOT}
# Quote every path: KiCad footprint files contain spaces and parens.
( cd /build/pkgroot && find usr \( -type f -o -type l \) -printf '"/%p"\n' ) > /root/files.list
echo '%dir "/usr/lib/copperai"' >> /root/files.list
echo "file list entries: $(wc -l < /root/files.list)"

cat > /root/rpmbuild/SPECS/copperai.spec <<SPEC
%global __os_install_post %{nil}
%global debug_package %{nil}
Name: copperai
Version: 1.0.0
Release: 1%{?dist}
Summary: CopperAI KiCad (Fedora, self-contained)
License: GPLv3+
BuildArch: x86_64
Requires: mesa-libGL
Requires: mesa-dri-drivers
AutoReqProv: no
%description
CopperAI KiCad self-contained Fedora build. Bundles its own shared libraries
under /usr/lib/copperai (RPATH), so it installs without pulling KiCad deps.
%install
mkdir -p %{buildroot}
cp -a /build/pkgroot/usr %{buildroot}/usr
%files -f /root/files.list
SPEC

rpmbuild -bb --define "_topdir /root/rpmbuild" --define "_build_id_links none" /root/rpmbuild/SPECS/copperai.spec || echo "rpmbuild returned $? (continuing if RPM was written)"
ls -la /root/rpmbuild/RPMS/x86_64/ || true
cp /root/rpmbuild/RPMS/x86_64/*.rpm /out/
ls -la /out/*.rpm
echo "PACKAGE-COMPLETE"
