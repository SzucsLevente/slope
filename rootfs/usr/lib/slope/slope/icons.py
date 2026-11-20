"""slope-icons: builds the glossy "Slope" icon theme.

Every application icon is placed on a glossy, porcelain-glass rounded tile so
that all apps look alike in the Dock, Launchpad, Spotlight and window
titles, whatever icon style they ship.  The theme inherits Papirus (for
everything that is not an app icon) and hicolor.

  slope-icons --update          add tiles for apps that do not have one yet
                                (run as root by an apt hook after installs)
  slope-icons --rebuild         regenerate every tile
  slope-icons --user --update   same, for apps installed in your home folder
                                (~/.local/share/icons/Slope)

Icons whose name starts with "slope-" are Slope's own and already glossy.
"""
import argparse
import configparser
import glob
import math
import os
import subprocess
import sys

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from . import art  # noqa: E402

THEME = "Slope"
SIZES = (256, 128, 64, 48, 32, 24, 16)
SYSTEM_ROOT = "/usr/share/icons"
APP_DIRS = ["/usr/share/applications", "/usr/local/share/applications",
            "/var/lib/flatpak/exports/share/applications"]
SEARCH = [
    "/usr/share/icons/Papirus/64x64/apps",
    "/usr/share/icons/Papirus/48x48/apps",
    "/usr/share/icons/hicolor/scalable/apps",
    "/usr/share/icons/hicolor/512x512/apps",
    "/usr/share/icons/hicolor/256x256/apps",
    "/usr/share/icons/hicolor/192x192/apps",
    "/usr/share/icons/hicolor/128x128/apps",
    "/usr/share/icons/hicolor/96x96/apps",
    "/usr/share/icons/hicolor/64x64/apps",
    "/usr/share/icons/hicolor/48x48/apps",
    "/var/lib/flatpak/exports/share/icons/hicolor/scalable/apps",
    "/var/lib/flatpak/exports/share/icons/hicolor/512x512/apps",
    "/var/lib/flatpak/exports/share/icons/hicolor/256x256/apps",
    "/var/lib/flatpak/exports/share/icons/hicolor/128x128/apps",
    "/usr/share/pixmaps",
]
USER_SEARCH = [os.path.expanduser(p) for p in (
    "~/.local/share/icons/hicolor/scalable/apps",
    "~/.local/share/icons/hicolor/256x256/apps",
    "~/.local/share/icons/hicolor/128x128/apps",
    "~/.local/share/icons/hicolor/64x64/apps",
    "~/.local/share/icons/hicolor/48x48/apps",
    "~/.local/share/icons",
)]

# Popular apps people install later: their tiles are ready in the ISO, so the
# moment e.g. Firefox is installed it already has a matching icon.
POPULAR = """
firefox firefox-esr firefox-developer-edition thunderbird chromium chromium-browser google-chrome
brave-browser microsoft-edge opera vivaldi librewolf torbrowser epiphany midori
vlc mpv celluloid totem rhythmbox lollypop audacious clementine strawberry spotify
gimp inkscape krita blender darktable rawtherapee digikam shotwell kdenlive shotcut
openshot obs obs-studio audacity ardour lmms musescore handbrake
libreoffice-startcenter libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw
libreoffice-math libreoffice-base onlyoffice-desktopeditors wps-office okular evince atril
code vscodium code-oss sublime-text gedit kate geany jetbrains-toolbox intellij-idea-community
pycharm-community android-studio qtcreator gnome-builder
steam lutris heroic minecraft-launcher retroarch dolphin-emu supertuxkart 0ad wesnoth
discord telegram telegram-desktop signal-desktop element-desktop slack zoom skype teams
whatsapp-for-linux thunderbird-mail evolution geary mailspring
transmission transmission-gtk qbittorrent deluge filezilla remmina virtualbox gnome-boxes
virt-manager gparted gnome-disks baobab timeshift bleachbit synaptic gnome-software
keepassxc bitwarden 1password nextcloud dropbox syncthing
gnome-calculator gnome-calendar gnome-clocks gnome-weather gnome-maps gnome-contacts
cheese simple-scan system-config-printer hardinfo htop
""".split()


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------

def tile(cr, x, y, s):
    """The glossy porcelain tile every app icon sits on."""
    cr.save()
    for i in range(5):
        cr.set_source_rgba(0, 0, 0, 0.045)
        art.squircle(cr, x - i * 0.4, y + s * 0.02 + i * 0.5, s + i * 0.8)
        cr.fill()
    cr.restore()
    art.squircle(cr, x, y, s)
    g = cairo.LinearGradient(0, y, 0, y + s)
    g.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
    g.add_color_stop_rgb(0.55, 0.95, 0.96, 0.98)
    g.add_color_stop_rgb(1, 0.84, 0.87, 0.92)
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.20)
    cr.set_line_width(max(0.8, s / 110.0))
    cr.stroke()


def gloss(cr, x, y, s):
    cr.save()
    art.squircle(cr, x, y, s)
    cr.clip()
    cr.new_path()
    cr.move_to(x, y)
    cr.line_to(x + s, y)
    cr.line_to(x + s, y + s * 0.40)
    cr.curve_to(x + s * 0.7, y + s * 0.50, x + s * 0.3, y + s * 0.50, x, y + s * 0.40)
    cr.close_path()
    hg = cairo.LinearGradient(0, y, 0, y + s * 0.5)
    hg.add_color_stop_rgba(0, 1, 1, 1, 0.55)
    hg.add_color_stop_rgba(1, 1, 1, 1, 0.04)
    cr.set_source(hg)
    cr.fill()
    cr.restore()


def glossify(src, size):
    """Return a cairo surface: src (a GdkPixbuf) on a glossy tile."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    pad = size * 0.06
    s = size - 2 * pad
    tile(cr, pad, pad, s)
    inner = s * 0.72
    w, h = src.get_width(), src.get_height()
    sc = inner / max(w, h)
    cr.save()
    art.squircle(cr, pad, pad, s)
    cr.clip()
    cr.translate(pad + (s - w * sc) / 2.0, pad + (s - h * sc) / 2.0 - s * 0.01)
    cr.scale(sc, sc)
    from gi.repository import Gdk
    Gdk.cairo_set_source_pixbuf(cr, src, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    cr.restore()
    gloss(cr, pad, pad, s)
    return surf


# ---------------------------------------------------------------------------
# icon discovery
# ---------------------------------------------------------------------------

def find_source(name, extra_dirs=()):
    """Path of the best available original icon for an icon name or file."""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    base = name
    for ext in (".svg", ".png", ".xpm"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    for d in list(extra_dirs) + SEARCH:
        for ext in (".svg", ".png", ".xpm"):
            p = os.path.join(d, base + ext)
            if os.path.exists(p):
                return p
    return None


def load(path, px=256):
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, px, px, True)
    except GLib.Error:
        return None


def desktop_icons(dirs):
    """Icon names (or paths) used by .desktop files in dirs."""
    names = set()
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.desktop")):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                icon = cp.get("Desktop Entry", "Icon", fallback="").strip()
            except (configparser.Error, UnicodeDecodeError):
                continue
            if icon:
                names.add(icon)
    return names


def out_name(icon):
    return os.path.splitext(os.path.basename(icon))[0] if os.path.isabs(icon) else icon


# ---------------------------------------------------------------------------
# theme writing
# ---------------------------------------------------------------------------

def write_index(root):
    os.makedirs(root, exist_ok=True)
    dirs = ["%dx%d/apps" % (s, s) for s in SIZES]
    lines = ["[Icon Theme]", "Name=Slope",
             "Comment=Glossy app icons for the Slope desktop (built on Papirus)",
             "Inherits=Papirus,Adwaita,hicolor", "Example=folder",
             "Directories=" + ",".join(dirs), ""]
    for s in SIZES:
        lines += ["[%dx%d/apps]" % (s, s), "Size=%d" % s, "Context=Applications", "Type=Fixed", ""]
    with open(os.path.join(root, "index.theme"), "w") as f:
        f.write("\n".join(lines))


def make(root, icon, extra_dirs=(), force=False):
    name = out_name(icon)
    if name.startswith("slope-") or not name:
        return False
    target = os.path.join(root, "256x256/apps", name + ".png")
    if os.path.exists(target) and not force:
        return False
    src_path = find_source(icon, extra_dirs)
    if src_path is None:
        return False
    src = load(src_path)
    if src is None:
        return False
    big = glossify(src, 256)
    for s in SIZES:
        d = os.path.join(root, "%dx%d/apps" % (s, s))
        os.makedirs(d, exist_ok=True)
        if s == 256:
            surf = big
        else:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, s, s)
            cr = cairo.Context(surf)
            cr.scale(s / 256.0, s / 256.0)
            cr.set_source_surface(big, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BEST)
            cr.paint()
        surf.write_to_png(os.path.join(d, name + ".png"))
    return True


def refresh_cache(root):
    try:
        subprocess.run(["gtk-update-icon-cache", "-q", "-f", "-t", root], check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="slope-icons", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="add tiles for new apps")
    ap.add_argument("--rebuild", action="store_true", help="regenerate every tile")
    ap.add_argument("--popular", action="store_true", help="also pre-build tiles for popular apps")
    ap.add_argument("--user", action="store_true", help="work on ~/.local/share/icons/Slope")
    ap.add_argument("--root", help="theme directory (default: system or user location)")
    args = ap.parse_args(argv)
    if not (args.update or args.rebuild):
        ap.print_help()
        return 0
    if args.user:
        root = args.root or os.path.join(GLib.get_user_data_dir(), "icons", THEME)
        dirs = [os.path.join(GLib.get_user_data_dir(), "applications"),
                os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]
        extra = USER_SEARCH
    else:
        root = args.root or os.path.join(SYSTEM_ROOT, THEME)
        dirs = APP_DIRS
        extra = ()
    icons = desktop_icons(dirs)
    if args.popular:
        icons |= set(POPULAR)
    write_index(root)
    made = 0
    for icon in sorted(icons):
        try:
            if make(root, icon, extra, force=args.rebuild):
                made += 1
        except Exception as e:      # one broken icon must never stop an apt run
            print("slope-icons: %s: %s" % (icon, e), file=sys.stderr)
    if made:
        refresh_cache(root)
    print("slope-icons: %d new glossy icon(s) in %s" % (made, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
