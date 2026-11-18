#!/usr/bin/env bash
# Boot the built ISO in QEMU.  Usage: tools/run-qemu.sh [path/to.iso] [--uefi]
set -euo pipefail
ISO=${1:-$(ls -t "$(dirname "$0")"/../out/*.iso | head -1)}
ARGS=(-m 4096 -smp 4 -cdrom "$ISO" -boot d -vga virtio -display gtk,zoom-to-fit=on
      -device intel-hda -device hda-duplex -nic user,model=virtio-net-pci)
[ -e /dev/kvm ] && ARGS+=(-enable-kvm -cpu host)
if [ "${2:-}" = "--uefi" ]; then
  ARGS+=(-bios /usr/share/ovmf/OVMF.fd)
fi
exec qemu-system-x86_64 "${ARGS[@]}"
