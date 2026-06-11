#!/bin/bash
# CopperAI-KiCad Fedora x86_64 build + self-contained bundle. Runs inside fedora:41.
set -euxo pipefail
exec > /out/build.log 2>&1

echo "=== PHASE: deps ==="
dnf -y install dnf5-plugins 'dnf-command(builddep)' git cmake ninja-build gcc-c++ \
    rpm-build patchelf file findutils which || true
# builddep covers most; the explicit list backstops anything builddep misses
# (this is the Fedora analogue of the libs apt-get couldn't find).
dnf -y builddep kicad || true
# Install each package independently — a single name that doesn't exist on this
# Fedora must NOT sink the whole list (that silently dropped protobuf-devel before).
for p in \
    wxGTK-devel webkit2gtk4.1-devel gtk3-devel glew-devel glm-devel \
    cairo-devel boost-devel swig python3-devel \
    opencascade-devel protobuf-devel protobuf-compiler nng-devel \
    libcurl-devel openssl-devel zlib-devel libzstd-devel libsecret-devel \
    unixODBC-devel gettext libomp-devel mesa-libGL-devel; do
  dnf -y install "$p" || echo "WARN: optional dep not installed: $p"
done
# Hard gate the must-haves: fail loudly here rather than deep in cmake.
rpm -q protobuf-devel protobuf-compiler wxGTK-devel webkit2gtk4.1-devel opencascade-devel

echo "=== PHASE: clone ==="
rm -rf /build/kicad && git clone -b main /src.bundle /build/kicad
cd /build/kicad

echo "=== PHASE: cmake ==="
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr \
  -DKICAD_SCRIPTING_WXPYTHON=OFF \
  -DKICAD_BUILD_QA_TESTS=OFF \
  -DKICAD_USE_CMAKE_FINDPROTOBUF=ON \
  -DKICAD_PRODUCTION_AGENT_CHAT_DEFAULT=ON 2>&1 | tail -60

echo "=== PHASE: compile ==="
ninja -C build -j"$(nproc)"

echo "=== PHASE: install ==="
DESTDIR=/pkgroot ninja -C build install

echo "=== PHASE: bundle libs (self-contained) ==="
PRIV=/pkgroot/usr/lib/copperai
mkdir -p "$PRIV"
# System libs that must stay external (kernel/loader/GPU/glibc core) — bundling
# these breaks more than it fixes.
KEEP='ld-linux|/libc\.so|/libm\.so|/libdl\.so|/libpthread\.so|/librt\.so|/libresolv\.so|libGLX|libGL\.so|libEGL|libGLdispatch|libdrm|libgbm|libwayland|/libgcc_s'
# Collect all ELF files — using file(1) rather than extension/permission so .kiface
# plugin modules (which carry neither *.so nor exec bit) are included.
mapfile -t ALL_ELF < <(find /pkgroot/usr/bin /pkgroot/usr/lib* -type f 2>/dev/null \
  | while read -r f; do file "$f" | grep -q ELF && echo "$f"; done)
copy_dep() { local lib="$1"; [ -f "$lib" ] || return; local base; base=$(basename "$lib"); [ -e "$PRIV/$base" ] && return; cp -L "$lib" "$PRIV/$base"; }
for b in "${ALL_ELF[@]}"; do
  ldd "$b" 2>/dev/null | awk '/=> \//{print $3}' | while read -r dep; do
    echo "$dep" | grep -qE "$KEEP" && continue
    echo "$dep" | grep -q '/pkgroot/' && continue   # already ours
    copy_dep "$dep"
  done
done
# second pass: resolve deps OF the bundled libs
for i in 1 2 3; do
  for lib in "$PRIV"/*; do
    ldd "$lib" 2>/dev/null | awk '/=> \//{print $3}' | while read -r dep; do
      echo "$dep" | grep -qE "$KEEP" && continue
      copy_dep "$dep"
    done
  done
done
echo "bundled $(ls "$PRIV" | wc -l) libraries"

# Patchelf ALL ELF files to absolute /usr/lib/copperai RPATH.
# Absolute path is required for .kiface plugin modules loaded at runtime — they
# cannot rely on $ORIGIN since dlopen resolves relative to the library, not cwd.
cnt=0
for b in "${ALL_ELF[@]}" "$PRIV"/*; do
  [ -f "$b" ] || continue
  patchelf --set-rpath '/usr/lib/copperai' "$b" 2>/dev/null && cnt=$((cnt+1)) || true
done
echo "patchelf'd $cnt ELF files"
# Sanity: .kiface modules must carry the RPATH
for k in $(find /pkgroot -name '*.kiface' | head -3); do echo "$k -> $(patchelf --print-rpath "$k")"; done

echo "=== PHASE: package ==="
# Use native rpmbuild (not fpm) — fpm calls lchmod on .so symlinks which Linux rejects.
mkdir -p /root/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,BUILDROOT}
( cd /pkgroot && find usr \( -type f -o -type l \) -printf '"/%p"\n' ) > /root/files.list
echo '%dir "/usr/lib/copperai"' >> /root/files.list
cat > /root/rpmbuild/SPECS/copperai.spec <<'SPEC'
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
Requires: libwayland-client
Requires: libwayland-cursor
Requires: libwayland-egl
AutoReqProv: no
%description
CopperAI KiCad self-contained Fedora build.
%install
mkdir -p %{buildroot}
cp -a /pkgroot/usr %{buildroot}/usr
%files -f /root/files.list
SPEC
rpmbuild -bb --define "_topdir /root/rpmbuild" --define "_build_id_links none" \
  /root/rpmbuild/SPECS/copperai.spec || echo "rpmbuild returned $?"
cp /root/rpmbuild/RPMS/x86_64/*.rpm /out/
ls -la /out/*.rpm
echo "BUILD-COMPLETE"
