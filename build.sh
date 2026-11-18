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
for tool in debootstrap mksquashfs xorriso grub-mkrescue mformat; do
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
mount_chroot() {
  mount --bind /dev "$CHROOT/dev"
  mount --bind /dev/pts "$CHROOT/dev/pts"
  mount -t proc proc "$CHROOT/proc"
  mount -t sysfs sysfs "$CHROOT/sys"
  mount -t tmpfs tmpfs "$CHROOT/run"
  mount -t tmpfs tmpfs "$CHROOT/tmp"
}
umount_chroot() {
  for m in tmp run sys proc dev/pts dev; do
    if mountpoint -q "$CHROOT/$m" 2>/dev/null; then
      umount -l "$CHROOT/$m" || true
    fi
  done
}
trap umount_chroot EXIT
in_chroot() {
  chroot "$CHROOT" /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true "$@"
}

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

# ---------------------------------------------------------------------------
# 3. Slope itself
# ---------------------------------------------------------------------------
log "Installing the Slope desktop"
cp -a "$HERE/rootfs/." "$CHROOT/"
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

# ---------------------------------------------------------------------------
# 4. clean up the image
# ---------------------------------------------------------------------------
log "Cleaning up"
in_chroot apt-get clean
rm -rf "$CHROOT"/var/lib/apt/lists/* "$CHROOT"/var/cache/apt/*.bin
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
