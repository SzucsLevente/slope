"""Shared helpers for the Slope desktop: settings, app lookup and window control."""
import json
import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "slope")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
SHARE = "/usr/share/slope"

DEFAULTS = {
    "dock_apps": [
        "thunar.desktop",
        "org.kde.falkon.desktop",
        "xfce4-terminal.desktop",
        "org.xfce.mousepad.desktop",
        "org.xfce.ristretto.desktop",
        "galculator.desktop",
        "xfce4-taskmanager.desktop",
        "slope-settings.desktop",
    ],
    "dock_icon_size": 52,
    "dock_magnify": True,
    "dock_magnify_size": 88,
    "dock_style": "shelf",          # "shelf" (3D glass) or "box"
    "wallpaper": "slope:aqua",      # "slope:<palette>" or an image path
    "clock_24h": True,
    "clock_show_date": True,
    "clock_seconds": False,
    "close_hides_app": True,        # the macOS behaviour: red X keeps the app alive
}

# Friendlier, macOS-style names for the bundled apps.
FRIENDLY_NAMES = {
    "thunar": "Files",
    "xfce4-terminal": "Terminal",
    "org.xfce.mousepad": "TextEdit",
    "mousepad": "TextEdit",
    "org.xfce.ristretto": "Preview",
    "ristretto": "Preview",
    "galculator": "Calculator",
    "xfce4-taskmanager": "Activity Monitor",
    "org.xfce.taskmanager": "Activity Monitor",
    "org.kde.falkon": "Browser",
    "falkon": "Browser",
    "pavucontrol": "Sound",
    "org.pulseaudio.pavucontrol": "Sound",
    "slope-settings": "System Settings",
    "nm-connection-editor": "Network",
}

# Our own utility windows use this WM class and never show up in the dock.
SHELL_CLASS = "Slope"


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    return data


def save_settings(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def update_setting(key, value):
    s = load_settings()
    s[key] = value
    save_settings(s)


def spawn(argv):
    """Start a program detached from us."""
    if isinstance(argv, str):
        argv = ["/bin/sh", "-c", argv]
    try:
        GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH)
    except GLib.Error as e:
        print("slope: cannot start %s: %s" % (argv, e.message))


def have(cmd):
    return shutil.which(cmd) is not None


def run_quiet(argv, timeout=3):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

def desktop_key(app_id):
    return (app_id or "").lower().removesuffix(".desktop")


def friendly_name(appinfo=None, fallback=""):
    if appinfo is not None:
        key = desktop_key(appinfo.get_id())
        if key in FRIENDLY_NAMES:
            return FRIENDLY_NAMES[key]
        return appinfo.get_name() or fallback
    return FRIENDLY_NAMES.get((fallback or "").lower(), fallback)


class AppIndex:
    """Maps X11 windows (WM_CLASS) to .desktop entries."""

    def __init__(self):
        self.reload()
        self._monitor = Gio.AppInfoMonitor.get()
        self._monitor.connect("changed", lambda *_: self.reload())

    def reload(self):
        self.by_id = {}
        # several lookup tables, consulted from most to least reliable
        self.by_wmclass, self.by_idkey, self.by_idtail, self.by_exec = {}, {}, {}, {}
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            app_id = info.get_id()
            self.by_id[app_id] = info
            hidden = info.get_nodisplay()
            wmc = info.get_startup_wm_class()
            if wmc:
                self._put(self.by_wmclass, wmc.lower(), info, hidden)
            key = desktop_key(app_id)
            self._put(self.by_idkey, key, info, hidden)
            self._put(self.by_idtail, key.split(".")[-1], info, hidden)
            cmd = (info.get_commandline() or "").split()
            exe = info.get_executable()
            if exe:
                # "mousepad --preferences" must not win over plain "mousepad %F"
                extra = [a for a in cmd[1:] if not a.startswith("%")]
                self._put(self.by_exec, os.path.basename(exe).lower(), info, hidden or bool(extra))

    @staticmethod
    def _put(table, key, info, weak):
        if not key:
            return
        cur = table.get(key)
        if cur is None or (cur[1] and not weak):
            table[key] = (info, weak)

    def get(self, app_id):
        info = self.by_id.get(app_id)
        if info is None:
            try:
                info = Gio.DesktopAppInfo.new(app_id)
            except TypeError:
                info = None
        return info

    def match(self, res_class, res_name):
        keys = []
        for k in (res_class, res_name):
            if k:
                k = k.lower()
                keys += [k, k.split(".")[-1], k.replace(" ", "-")]
        for table in (self.by_wmclass, self.by_idkey, self.by_idtail, self.by_exec):
            for k in keys:
                hit = table.get(k)
                if hit is not None:
                    return hit[0]
        return None


def icon_for(appinfo, size, fallback="application-x-executable"):
    theme = Gtk.IconTheme.get_default()
    names = []
    if appinfo is not None:
        gicon = appinfo.get_icon()
        if gicon is not None:
            if isinstance(gicon, Gio.FileIcon):
                path = gicon.get_file().get_path()
                if path:
                    try:
                        from gi.repository import GdkPixbuf
                        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
                    except GLib.Error:
                        pass
            elif isinstance(gicon, Gio.ThemedIcon):
                names.extend(gicon.get_names())
    names.append(fallback)
    for n in names:
        try:
            pb = theme.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if pb is not None:
                return pb
        except GLib.Error:
            continue
    return None


def launch(appinfo, uris=None):
    ctx = Gdk.Display.get_default().get_app_launch_context()
    try:
        if uris:
            appinfo.launch_uris(uris, ctx)
        else:
            appinfo.launch([], ctx)
        return True
    except GLib.Error as e:
        print("slope: launch failed:", e.message)
        return False


# --------------------------------------------------------------------------
# Session actions
# --------------------------------------------------------------------------

def logout():
    spawn(["openbox", "--exit"])


def power(action):
    spawn(["systemctl", action])
