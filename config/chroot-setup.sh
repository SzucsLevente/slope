#!/bin/bash
# Runs inside the image chroot after packages and the Slope files are installed.
set -euo pipefail

echo "--- locale & time"
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen
echo 'LANG=en_US.UTF-8' > /etc/default/locale
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
echo UTC > /etc/timezone

echo "--- artwork"
mkdir -p /usr/share/backgrounds/slope
PYTHONPATH=/usr/lib/slope python3 -m slope.art /usr/share/backgrounds/slope
# glossy traffic-light buttons for the (patched) Openbox theme
cp /usr/share/backgrounds/slope/titlebuttons/*.png /usr/share/themes/Slope/openbox-3/
rm -rf /usr/share/backgrounds/slope/titlebuttons
# Launchpad icon and installer branding
install -Dm644 /usr/share/backgrounds/slope/launchpad-256.png /usr/share/icons/hicolor/256x256/apps/slope-launchpad.png
install -Dm644 /usr/share/backgrounds/slope/installer-256.png /usr/share/icons/hicolor/256x256/apps/slope-installer.png
mkdir -p /etc/calamares/branding/slope
cp /usr/share/backgrounds/slope/logo-256.png /etc/calamares/branding/slope/logo.png
cp /usr/share/backgrounds/slope/calamares-welcome.png /etc/calamares/branding/slope/welcome.png
cp /usr/share/backgrounds/slope/calamares-slide-*.png /etc/calamares/branding/slope/
cp /usr/share/backgrounds/slope/logo-128.png /usr/share/plymouth/themes/slope/logo.png
cp /usr/share/backgrounds/slope/dot.png /usr/share/plymouth/themes/slope/dot.png
install -Dm644 /usr/share/backgrounds/slope/logo-256.png /usr/share/icons/hicolor/256x256/apps/slope.png
install -Dm644 /usr/share/backgrounds/slope/logo-64.png /usr/share/icons/hicolor/64x64/apps/slope.png
gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true

echo "--- byte-compile the shell"
python3 -m compileall -q /usr/lib/slope/slope

echo "--- boot splash"
update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth \
  /usr/share/plymouth/themes/slope/slope.plymouth 300
update-alternatives --set default.plymouth /usr/share/plymouth/themes/slope/slope.plymouth

echo "--- default session & apps"
update-alternatives --install /usr/bin/x-session-manager x-session-manager /usr/bin/slope-session 90
update-alternatives --set x-session-manager /usr/bin/slope-session
update-alternatives --set x-terminal-emulator /usr/bin/xfce4-terminal.wrapper 2>/dev/null || true
update-alternatives --set x-www-browser /usr/bin/falkon 2>/dev/null || true
# only offer the Slope session at the login screen
rm -f /usr/share/xsessions/openbox.desktop /usr/share/xsessions/lightdm-xsession.desktop

# hide technical launchers from Spotlight
for f in debian-xterm debian-uxterm openbox obconf lxappearance picom compton \
         display-im6.q16 display-im7.q16 nm-applet thunar-settings thunar-bulk-rename \
         thunar-volman-settings org.xfce.mousepad-settings xfce4-terminal-settings \
         calamares org.kde.calamares xfce4-notifyd-config vim; do
  d="/usr/share/applications/$f.desktop"
  if [ -f "$d" ]; then
    if grep -q '^NoDisplay=' "$d"; then
      sed -i 's/^NoDisplay=.*/NoDisplay=true/' "$d"
    else
      sed -i '0,/^\[Desktop Entry\]/s//[Desktop Entry]\nNoDisplay=true/' "$d"
    fi
  fi
done
# nm-applet has no tray to live in: the top bar shows network status instead
rm -f /etc/xdg/autostart/nm-applet.desktop

# Mac-style names for the bundled apps
rename_app() {  # desktop-file new-name
  local f="/usr/share/applications/$1"
  [ -f "$f" ] || return 0
  sed -i "0,/^Name=.*/s//Name=$2/" "$f"
  sed -i '/^Name\[/d' "$f"
}
rename_app thunar.desktop "Files"
rename_app xfce4-terminal.desktop "Terminal"
rename_app org.xfce.mousepad.desktop "TextEdit"
rename_app org.xfce.ristretto.desktop "Preview"
rename_app galculator.desktop "Calculator"
rename_app xfce4-taskmanager.desktop "Activity Monitor"
rename_app org.kde.falkon.desktop "Browser"
rename_app atril.desktop "Documents"
rename_app xarchiver.desktop "Archive Utility"
rename_app pavucontrol.desktop "Sound"
rename_app org.pulseaudio.pavucontrol.desktop "Sound"
update-desktop-database -q /usr/share/applications || true

echo "--- desktop defaults for every new user"
mkdir -p /etc/skel/.config/slope /etc/skel/Desktop
# Terminal: a clean light profile that suits the theme
mkdir -p /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml
cat > /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-terminal.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-terminal" version="1.0">
  <property name="font-name" type="string" value="DejaVu Sans Mono 11"/>
  <property name="color-foreground" type="string" value="#1d1d1f"/>
  <property name="color-background" type="string" value="#fbfbfd"/>
  <property name="color-cursor" type="string" value="#2f86f3"/>
  <property name="color-palette" type="string" value="#1d1d1f;#c41a16;#007400;#826b28;#0b4bc2;#a90d91;#3e8087;#c7c7c7;#686868;#e0443c;#28c840;#d79c19;#2f86f3;#c54fd8;#26a4b5;#ffffff"/>
  <property name="misc-menubar-default" type="bool" value="true"/>
  <property name="misc-default-geometry" type="string" value="96x28"/>
  <property name="scrolling-bar" type="string" value="TERMINAL_SCROLLBAR_RIGHT"/>
  <property name="title-mode" type="string" value="TERMINAL_TITLE_REPLACE"/>
</channel>
EOF
# Thunar: keep its menu bar (it moves into the global menu)
cat > /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="thunar" version="1.0">
  <property name="last-menubar-visible" type="bool" value="true"/>
  <property name="last-view" type="string" value="ThunarIconView"/>
  <property name="misc-single-click" type="bool" value="false"/>
  <property name="last-show-hidden" type="bool" value="false"/>
</channel>
EOF
# notifications in the top-right corner, glassy
cat > /etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-notifyd.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-notifyd" version="1.0">
  <property name="notify-location" type="uint" value="2"/>
  <property name="initial-opacity" type="double" value="0.95"/>
  <property name="theme" type="string" value="Default"/>
  <property name="expire-timeout" type="int" value="6"/>
</channel>
EOF
# GTK 2 apps
cat > /etc/skel/.gtkrc-2.0 <<'EOF'
gtk-theme-name="Adwaita"
gtk-icon-theme-name="Papirus"
gtk-font-name="Inter 10"
EOF

echo "--- networking"
# Wired: NetworkManager.  Wi-Fi: iwd.  DNS: systemd-resolved.
systemctl enable NetworkManager systemd-resolved iwd 2>/dev/null || true
# wpa_supplicant would fight iwd over the Wi-Fi card
systemctl mask wpa_supplicant.service 2>/dev/null || true
chmod 600 /etc/netplan/*.yaml
install -Dm644 /usr/share/backgrounds/slope/wifi-256.png /usr/share/icons/hicolor/256x256/apps/slope-wifi.png
gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true

echo "--- live session"
systemctl set-default graphical.target
# casper autologins the live user into the default (Slope) session
mkdir -p /etc/lightdm/lightdm.conf.d

echo "--- initramfs"
update-initramfs -u -k all
