# CopperAI-KiCad — Fedora packaging

Produces a **self-contained** Fedora x86_64 RPM: the binaries bundle their own
shared libraries under `/usr/lib/copperai` (via RPATH), so the package installs
on a clean Fedora with no KiCad dependency chase — only `mesa-libGL` /
`mesa-dri-drivers` (the GPU stack, which must come from the host) are required.

## How it's built

Everything runs inside a disposable `fedora:41` container (podman), so the host
only needs podman. On an LXC host, set `keyring=false` under `[containers]` in
`/etc/containers/containers.conf` and run with `--cgroup-manager=cgroupfs`.

```sh
# one-shot: deps -> cmake -> compile -> install -> bundle libs -> rpm
podman run --rm --cgroup-manager=cgroupfs \
  -v ./copper-kicad.bundle:/src.bundle:ro \
  -v ./packaging/fedora/build.sh:/build.sh:ro \
  -v ./out:/out -v ./scratch:/build \
  fedora:41 bash /build.sh
```

`build.sh` is the full pipeline. `package.sh` / `rpmbuild-only.sh` repackage an
already-compiled tree in `scratch/` without recompiling (useful when iterating
on packaging).

## Key build notes (learned the hard way)

- **Protobuf**: Fedora's `protobuf-devel` ships no CMake *config* file, so build
  with `-DKICAD_USE_CMAKE_FINDPROTOBUF=ON` (CMake MODULE-mode `FindProtobuf`).
- **Dependencies install per-package** (`dnf install "$p" || warn`) so one name
  that doesn't exist on a given Fedora can't silently drop the whole list.
- **Packaging uses native `rpmbuild`, not fpm** — fpm's `copy_entry` calls
  `lchmod` on `.so` symlinks, which Linux rejects (`EPERM`).
- `%files` paths are **quoted** — KiCad footprint filenames contain spaces and
  parentheses.
- `%global __os_install_post %{nil}` + `%global debug_package %{nil}` — never
  let rpm strip the RPATH'd, patchelf'd binaries or build a -debuginfo package.

## Install test

`.github/workflows/fedora-rpm-install-test.yml` installs the released RPM on a
clean `fedora:41` and asserts every binary resolves its libraries (`ldd`, no
"not found"). It runs automatically on release publish.
