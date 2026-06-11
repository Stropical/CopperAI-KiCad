#!/bin/bash
# Final packaging: full build deps (so lib bundling resolves), reuse compiled
# objects via cmake --install (no recompile), bundle, then native rpmbuild.
set -euxo pipefail
exec > /out/package.log 2>&1

echo "=== deps (build libs needed for bundling + rpmbuild) ==="
dnf -y install dnf5-plugins 'dnf-command(builddep)' cmake ninja-build gcc-c++ \
    rpm-build patchelf file findutils which binutils >/dev/null
dnf -y builddep kicad >/dev/null || true
for p in wxGTK-devel webkit2gtk4.1-devel gtk3-devel glew-devel glm-devel cairo-devel \
    boost-devel opencascade-devel protobuf-devel protobuf-compiler nng-devel \
    libcurl-devel openssl-devel libzstd-devel libsecret-devel unixODBC-devel \
    libomp-devel mesa-libGL-devel; do dnf -y install "$p" >/dev/null || true; done

test -d /build/kicad/build || { echo "FATAL: build dir missing"; exit 1; }
cd /build/kicad

echo "=== install (no recompile) ==="
rm -rf /build/pkgroot
DESTDIR=/build/pkgroot cmake --install build >/dev/null

echo "=== bundle libs (self-contained) ==="
PRIV=/build/pkgroot/usr/lib/copperai
mkdir -p "$PRIV"
KEEP='ld-linux|/libc\.so|/libm\.so|/libdl\.so|/libpthread\.so|/librt\.so|/libresolv\.so|libGLX|libGL\.so|libEGL|libGLdispatch|libdrm|libgbm|libwayland|/libgcc_s'
mapfile -t BINS < <(find /build/pkgroot/usr/bin /build/pkgroot/usr/lib* -type f \( -name '*.so*' -o -perm -u+x \) 2>/dev/null | while read -r f; do file "$f" | grep -q ELF && echo "$f"; done)
copy_dep() { local lib="$1"; [ -f "$lib" ] || return; local base; base=$(basename "$lib"); [ -e "$PRIV/$base" ] && return; cp -L "$lib" "$PRIV/$base"; }
for b in "${BINS[@]}"; do ldd "$b" 2>/dev/null | awk '/=> \//{print $3}' | while read -r d; do echo "$d"|grep -qE "$KEEP" && continue; echo "$d"|grep -q '/build/pkgroot/' && continue; copy_dep "$d"; done; done
for i in 1 2 3; do for lib in "$PRIV"/*; do ldd "$lib" 2>/dev/null | awk '/=> \//{print $3}' | while read -r d; do echo "$d"|grep -qE "$KEEP" && continue; copy_dep "$d"; done; done; done
echo "bundled $(ls "$PRIV" | wc -l) libraries"
for b in "${BINS[@]}" "$PRIV"/*; do patchelf --set-rpath '$ORIGIN/../lib/copperai:$ORIGIN' "$b" 2>/dev/null || true; done
for b in /build/pkgroot/usr/bin/*; do file "$b" | grep -q ELF && patchelf --set-rpath '$ORIGIN/../lib/copperai' "$b" 2>/dev/null || true; done
find /build/pkgroot -xtype l -delete || true

echo "=== rpmbuild (native, handles .so symlinks) ==="
mkdir -p /root/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,BUILDROOT}
( cd /build/pkgroot && find usr \( -type f -o -type l \) -printf '/%p\n' ) > /root/files.list
echo "%dir /usr/lib/copperai" >> /root/files.list
cat > /root/rpmbuild/SPECS/copperai.spec <<SPEC
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
%clean
rm -rf %{buildroot}
SPEC
rpmbuild -bb --define "_topdir /root/rpmbuild" /root/rpmbuild/SPECS/copperai.spec
cp /root/rpmbuild/RPMS/x86_64/*.rpm /out/
ls -la /out/*.rpm
echo "PACKAGE-COMPLETE"
