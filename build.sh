#!/usr/bin/env bash
#
# Build the Slope live ISO (hybrid BIOS + UEFI, boots from USB stick or DVD).
#
#   sudo ./build.sh            # full build -> out/slope-<version>-amd64.iso
#   sudo ./build.sh --clean    # throw away the cached work directory first
#   sudo ./build.sh --update   # reuse the previous chroot (fast rebuild after editing Slope files)
#
# Needs an Ubuntu or Debian host with:
#   sudo apt install debootstrap squashfs-tools xorriso grub-pc-bin \
#                    grub-efi-amd64-bin grub-common mtools dosfstools
#
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-/var/tmp/slope-work}
OUT=${OUT:-$HERE/out}
SUITE=${SUITE:-noble}
ARCH=amd64
MIRROR=${MIRROR:-http://archive.ubuntu.com/ubuntu}
SQUASHFS_COMP=${SQUASHFS_COMP:-xz}
VERSION=$(cat "$HERE/VERSION")
ISO="$OUT/slope-$VERSION-$ARCH.iso"
CHROOT="$WORK/chroot"
ISODIR="$WORK/iso"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "please run as root: sudo $0"
for tool in debootstrap mksquashfs xorriso grub-mkrescue mformat curl; do
  command -v "$tool" >/dev/null || die "missing '$tool'. Install: apt install debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin grub-common mtools dosfstools"
done
[ -d /usr/lib/grub/i386-pc ] || die "missing BIOS GRUB modules: apt install grub-pc-bin"
[ -d /usr/lib/grub/x86_64-efi ] || die "missing UEFI GRUB modules: apt install grub-efi-amd64-bin"

MODE=${1:-}
if [ "$MODE" = "--clean" ]; then
  log "Removing $WORK"
  rm -rf "$WORK"
fi
mkdir -p "$WORK" "$OUT"

# ---------------------------------------------------------------------------
# chroot mounts
# ---------------------------------------------------------------------------
mount_chroot() {  # [root]
  local r=${1:-$CHROOT}
  mount --bind /dev "$r/dev"
  mount --bind /dev/pts "$r/dev/pts"
  mount -t proc proc "$r/proc"
  mount -t sysfs sysfs "$r/sys"
  mount -t tmpfs tmpfs "$r/run"
  mount -t tmpfs tmpfs "$r/tmp"
}
umount_chroot() {  # [root]
  local r=${1:-$CHROOT}
  for m in tmp run sys proc dev/pts dev; do
    if mountpoint -q "$r/$m" 2>/dev/null; then
      umount -l "$r/$m" || true
    fi
  done
}
OBBUILD="$WORK/obbuild"
trap 'umount_chroot; umount_chroot "$OBBUILD"' EXIT
in_root() {  # root-dir command...
  local r=$1
  shift
  chroot "$r" /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true "$@"
}
in_chroot() { in_root "$CHROOT" "$@"; }

# ---------------------------------------------------------------------------
# 1. base system
# ---------------------------------------------------------------------------
umount_chroot
BASE="$WORK/base-$SUITE.tgz"
if [ "$MODE" = "--update" ] && [ -x "$CHROOT/usr/bin/apt-get" ]; then
  log "Reusing the existing system in $CHROOT (--update)"
elif rm -rf "$CHROOT" && [ -f "$BASE" ]; then
  log "Unpacking cached base system"
  mkdir -p "$CHROOT"
  tar -C "$CHROOT" -xzf "$BASE"
else
  log "Bootstrapping Ubuntu $SUITE"
  debootstrap --arch="$ARCH" --variant=minbase --components=main,universe \
    "$SUITE" "$CHROOT" "$MIRROR"
  tar -C "$CHROOT" -czf "$BASE" .
fi

# ---------------------------------------------------------------------------
# 1b. locally built packages, made once in a throwaway chroot and cached in
#     $WORK/debs/local-<hash>:
#       - Openbox with full-colour PNG title-bar buttons (packages/openbox)
#       - libconfig 1.7 + picom 12 back-ported for window animations (packages/picom)
#       - libslope-keepalive.so: Mac-style app lifetime for GTK apps (packages/keepalive)
#       - slope-gestures: trackpad gesture daemon (packages/gestures)
# ---------------------------------------------------------------------------
LOCAL_KEY=$(cat "$HERE"/packages/openbox/*.patch "$HERE"/packages/picom/sources.txt \
  "$HERE"/packages/keepalive/*.c "$HERE"/packages/gestures/*.c | sha256sum | cut -c1-12)
DEBS="$WORK/debs/local-$LOCAL_KEY"
if ! ls "$DEBS"/picom_*.deb "$DEBS"/openbox_*.deb "$DEBS"/libslope-keepalive.so \
     "$DEBS"/slope-gestures >/dev/null 2>&1; then
  log "Building local packages (Openbox patch, libconfig 1.7, picom 12)"
  [ -f "$BASE" ] || die "missing $BASE"
  umount_chroot "$OBBUILD"
  rm -rf "${OBBUILD:?}"
  mkdir -p "$OBBUILD"
  tar -C "$OBBUILD" -xzf "$BASE"
  mount_chroot "$OBBUILD"
  printf 'deb %s %s main universe\ndeb-src %s %s main universe\ndeb %s %s-updates main universe\ndeb-src %s %s-updates main universe\n' \
    "$MIRROR" "$SUITE" "$MIRROR" "$SUITE" "$MIRROR" "$SUITE" "$MIRROR" "$SUITE" > "$OBBUILD/etc/apt/sources.list"
  rm -f "$OBBUILD/etc/apt/sources.list.d/ubuntu.sources"
  rm -f "$OBBUILD/etc/resolv.conf"
  cp -L /etc/resolv.conf "$OBBUILD/etc/resolv.conf" 2>/dev/null || true
  mkdir -p "$OBBUILD/build"
  cp "$HERE"/packages/openbox/*.patch "$HERE"/packages/keepalive/*.c "$HERE"/packages/gestures/*.c \
     "$OBBUILD/build/"
  # source packages from the Ubuntu archive pool
  grep -v '^\s*#' "$HERE/packages/picom/sources.txt" | grep -v '^\s*$' | while read -r dir files; do
    for f in $files; do
      curl -fsSL -o "$OBBUILD/build/$f" "$MIRROR/pool/$dir/$f" || die "cannot download $f"
    done
  done
  in_root "$OBBUILD" apt-get update
  in_root "$OBBUILD" apt-get -y install --no-install-recommends dpkg-dev build-essential fakeroot patch
  in_root "$OBBUILD" apt-get -y build-dep --no-install-recommends openbox
  in_root "$OBBUILD" /bin/sh -ec 'cd /build && apt-get source openbox && cd openbox-*/ &&
    for p in ../*.patch; do patch -p1 < "$p"; done &&
    sed -i "1s/(\([^)]*\))/(\1+slope1)/" debian/changelog &&
    DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)" dpkg-buildpackage -b -uc -us'
  in_root "$OBBUILD" /bin/sh -ec 'cd /build && dpkg-source -x --no-check libconfig_*.dsc &&
    cd libconfig-1.7*/ && apt-get -y build-dep --no-install-recommends ./ &&
    sed -i "1s/(\([^)]*\))/(\1~slope24.04)/" debian/changelog &&
    DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)" dpkg-buildpackage -b -uc -us &&
    dpkg -i ../libconfig11_*.deb ../libconfig-dev_*.deb'
  in_root "$OBBUILD" /bin/sh -ec 'cd /build && dpkg-source -x --no-check picom_*.dsc &&
    cd picom-*/ && apt-get -y build-dep --no-install-recommends ./ &&
    sed -i "1s/(\([^)]*\))/(\1~slope24.04)/" debian/changelog &&
    DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)" dpkg-buildpackage -b -uc -us -d'
  in_root "$OBBUILD" apt-get -y install --no-install-recommends libx11-dev libxi-dev
  in_root "$OBBUILD" /bin/sh -ec 'cd /build &&
    gcc -shared -fPIC -O2 -Wall -Wl,-z,relro,-z,now -o libslope-keepalive.so slope-keepalive.c -ldl &&
    gcc -O2 -Wall -Wl,-z,relro,-z,now -o slope-gestures slope-gestures.c -lXi -lX11 -lm'
  mkdir -p "$DEBS"
  cp "$OBBUILD"/build/libslope-keepalive.so "$OBBUILD"/build/slope-gestures "$DEBS/"
  cp "$OBBUILD"/build/openbox_*_amd64.deb "$OBBUILD"/build/libobrender32v5_*.deb \
     "$OBBUILD"/build/libobt2v5_*.deb "$OBBUILD"/build/libconfig11_*.deb \
     "$OBBUILD"/build/picom_*_amd64.deb "$DEBS/"
  umount_chroot "$OBBUILD"
  rm -rf "${OBBUILD:?}"
fi

mount_chroot

# ---------------------------------------------------------------------------
# 2. packages
# ---------------------------------------------------------------------------
log "Configuring APT"
cat > "$CHROOT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main restricted universe multiverse
deb $MIRROR $SUITE-updates main restricted universe multiverse
deb http://security.ubuntu.com/ubuntu $SUITE-security main restricted universe multiverse
EOF
rm -f "$CHROOT/etc/apt/sources.list.d/ubuntu.sources"
# never start services inside the build chroot
printf '#!/bin/sh\nexit 101\n' > "$CHROOT/usr/sbin/policy-rc.d"
chmod +x "$CHROOT/usr/sbin/policy-rc.d"
echo slope > "$CHROOT/etc/hostname"
rm -f "$CHROOT/etc/resolv.conf"
cp -L /etc/resolv.conf "$CHROOT/etc/resolv.conf" 2>/dev/null || true
# hosts that need a proxy (CI, corporate networks) can pass it through
if [ -n "${http_proxy:-}" ]; then
  echo "Acquire::http::Proxy \"$http_proxy\";" > "$CHROOT/etc/apt/apt.conf.d/90build-proxy"
fi

log "Installing packages (this takes a while)"
in_chroot apt-get update
in_chroot apt-get -y upgrade
mapfile -t PKGS < <(grep -v '^\s*#' "$HERE/config/packages.txt" | grep -v '^\s*$')
in_chroot apt-get -y install --no-install-recommends "${PKGS[@]}"

log "Installing locally built packages (Openbox, picom 12)"
mkdir -p "$CHROOT/var/cache/slope-debs"
cp "$DEBS"/*.deb "$CHROOT/var/cache/slope-debs/"
in_chroot sh -c 'dpkg -i /var/cache/slope-debs/*.deb && apt-mark hold openbox libobrender32v5 libobt2v5 picom'
# the App Store catalogue (AppStream / DEP-11): apt only downloads it once the
# appstream package is installed, so update once more and build its cache
in_chroot apt-get update
in_chroot sh -c 'appstreamcli refresh --source=os --force >/dev/null 2>&1 || true'
install -D -m 644 "$DEBS/libslope-keepalive.so" "$CHROOT/usr/lib/slope/libslope-keepalive.so"
install -D -m 755 "$DEBS/slope-gestures" "$CHROOT/usr/lib/slope/slope-gestures"
rm -rf "$CHROOT/var/cache/slope-debs"

# ---------------------------------------------------------------------------
# 3. Slope itself
# ---------------------------------------------------------------------------
log "Installing the Slope desktop"
cp -a "$HERE/rootfs/." "$CHROOT/"
# what the App Store must never remove (Slope's own packages)
grep -v '^\s*#' "$HERE/config/packages.txt" | grep -v '^\s*$' > "$CHROOT/usr/share/slope/core-packages.txt"
printf '%s\n' openbox libobrender32v5 libobt2v5 picom libconfig11 >> "$CHROOT/usr/share/slope/core-packages.txt"
find "$CHROOT/usr/lib/slope" -name __pycache__ -prune -exec rm -rf {} +
cat > "$CHROOT/etc/slope-release" <<EOF
NAME="Slope"
VERSION="$VERSION"
CODENAME="Glacier"
BASE="Ubuntu 24.04 LTS"
BUILD_DATE="$(date -u +%Y-%m-%d)"
EOF
cp "$HERE/config/chroot-setup.sh" "$CHROOT/tmp/chroot-setup.sh"
in_chroot /bin/bash /tmp/chroot-setup.sh

# Firefox/Thunderbird as real .debs from Mozilla (Ubuntu's are snap wrappers).
# Pinned in rootfs/etc/apt/preferences.d/mozilla-firefox.pref.
mkdir -p "$CHROOT/etc/apt/keyrings"
if curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg \
     -o "$CHROOT/etc/apt/keyrings/packages.mozilla.org.asc"; then
  printf 'Types: deb\nURIs: https://packages.mozilla.org/apt\nSuites: mozilla\nComponents: main\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/packages.mozilla.org.asc\n' \
    > "$CHROOT/etc/apt/sources.list.d/mozilla.sources"
else
  echo "warning: could not download Mozilla's signing key; the image will not have the Firefox .deb repository" >&2
  rm -f "$CHROOT/etc/apt/keyrings/packages.mozilla.org.asc" "$CHROOT/etc/apt/sources.list.d/mozilla.sources"
fi


# ---------------------------------------------------------------------------
# 4. clean up the image
# ---------------------------------------------------------------------------
log "Cleaning up"
in_chroot apt-get clean
# keep only the App Store catalogue (DEP-11), so the store opens offline;
# apt fetches fresh package lists itself
find "$CHROOT"/var/lib/apt/lists -maxdepth 1 -type f ! -name '*dep11*' -delete
rm -rf "$CHROOT"/var/lib/apt/lists/partial/* "$CHROOT"/var/cache/apt/*.bin
rm -f "$CHROOT/usr/sbin/policy-rc.d" "$CHROOT/etc/apt/apt.conf.d/90build-proxy"
rm -f "$CHROOT"/var/log/*.log "$CHROOT"/var/log/apt/* 2>/dev/null || true
rm -f "$CHROOT"/root/.bash_history
: > "$CHROOT/etc/machine-id"
rm -f "$CHROOT/var/lib/dbus/machine-id"
rm -f "$CHROOT/etc/resolv.conf"
ln -s ../run/systemd/resolve/stub-resolv.conf "$CHROOT/etc/resolv.conf"
umount_chroot

# ---------------------------------------------------------------------------
# 5. ISO image
# ---------------------------------------------------------------------------
log "Preparing ISO tree"
rm -rf "$ISODIR"
mkdir -p "$ISODIR"/{casper,boot/grub,.disk}
KVER=$(ls "$CHROOT/boot" | sed -n 's/^vmlinuz-//p' | sort -V | tail -1)
[ -n "$KVER" ] || die "no kernel found in chroot"
cp "$CHROOT/boot/vmlinuz-$KVER" "$ISODIR/casper/vmlinuz"
cp "$CHROOT/boot/initrd.img-$KVER" "$ISODIR/casper/initrd"
cp "$CHROOT/usr/share/backgrounds/slope/grub.png" "$ISODIR/boot/grub/slope.png"

chroot "$CHROOT" dpkg-query -W --showformat='${Package} ${Version}\n' > "$ISODIR/casper/filesystem.manifest"
du -sx --block-size=1 "$CHROOT" | cut -f1 > "$ISODIR/casper/filesystem.size"
echo "Slope $VERSION \"Glacier\" - $ARCH ($(date -u +%Y%m%d))" > "$ISODIR/.disk/info"
touch "$ISODIR/.disk/base_installable"
echo "full_cd/single" > "$ISODIR/.disk/cd_type"

log "Compressing the root filesystem ($SQUASHFS_COMP)"
rm -f "$ISODIR/casper/filesystem.squashfs"
mksquashfs "$CHROOT" "$ISODIR/casper/filesystem.squashfs" -noappend -comp "$SQUASHFS_COMP" \
  -e boot/grub -wildcards -e 'proc/*' -e 'sys/*' -e 'dev/*' -e 'run/*' -e 'tmp/*'

cat > "$ISODIR/boot/grub/grub.cfg" <<'EOF'
set default=0
set timeout=6
insmod all_video
insmod gfxterm
insmod png
if loadfont unicode ; then
  set gfxmode=auto
  terminal_output gfxterm
fi
background_image /boot/grub/slope.png
set color_normal=white/black
set menu_color_normal=white/black
set menu_color_highlight=black/light-cyan

menuentry "Try Slope" {
  linux /casper/vmlinuz boot=casper quiet splash noprompt ---
  initrd /casper/initrd
}
menuentry "Try Slope (safe graphics)" {
  linux /casper/vmlinuz boot=casper nomodeset quiet splash noprompt ---
  initrd /casper/initrd
}
menuentry "Try Slope (copy to RAM, then remove the USB stick)" {
  linux /casper/vmlinuz boot=casper toram quiet splash noprompt ---
  initrd /casper/initrd
}
menuentry "Try Slope (verbose boot)" {
  linux /casper/vmlinuz boot=casper noprompt ---
  initrd /casper/initrd
}
if [ "$grub_platform" = "efi" ]; then
  menuentry "UEFI Firmware Settings" { fwsetup }
fi
EOF

log "Writing $ISO"
rm -f "$ISO"
grub-mkrescue -o "$ISO" "$ISODIR" -- -volid SLOPE
( cd "$OUT" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256" )
log "Done: $ISO ($(du -h "$ISO" | cut -f1))"
