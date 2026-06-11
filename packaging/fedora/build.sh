#!/bin/bash
# CopperAI-KiCad Fedora x86_64 build + self-contained bundle. Runs inside fedora:41.
set -euxo pipefail
exec > /out/build.log 2>&1

echo "=== PHASE: deps ==="
dnf -y install dnf5-plugins 'dnf-command(builddep)' git cmake ninja-build gcc-c++ \
    rpm-build ruby ruby-devel rubygems make patchelf file findutils which || true
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
mapfile -t BINS < <(find /pkgroot/usr/bin /pkgroot/usr/lib* -type f \( -name '*.so*' -o -perm -u+x \) 2>/dev/null | while read -r f; do file "$f" | grep -q ELF && echo "$f"; done)
copy_dep() { local lib="$1"; [ -f "$lib" ] || return; local base; base=$(basename "$lib"); [ -e "$PRIV/$base" ] && return; cp -L "$lib" "$PRIV/$base"; }
for b in "${BINS[@]}"; do
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
# point every binary and bundled lib at the private dir first
for b in "${BINS[@]}" "$PRIV"/*; do patchelf --set-rpath '$ORIGIN/../lib/copperai:$ORIGIN' "$b" 2>/dev/null || true; done
for b in /pkgroot/usr/bin/*; do file "$b" | grep -q ELF && patchelf --set-rpath '$ORIGIN/../lib/copperai' "$b" 2>/dev/null || true; done

echo "=== PHASE: package ==="
gem install --no-document fpm
fpm -s dir -t rpm -n copperai -v 1.0.0 --iteration 1 \
  --rpm-auto-add-directories \
  --description "CopperAI KiCad (Fedora, self-contained)" \
  --url "https://copperai.workers.dev" \
  --depends mesa-libGL --depends mesa-dri-drivers \
  -C /pkgroot usr
cp ./*.rpm /out/
ls -la /out/*.rpm
echo "BUILD-COMPLETE"
