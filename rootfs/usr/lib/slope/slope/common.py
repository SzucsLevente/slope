"""Shared helpers for the Slope desktop: settings, app lookup and window control."""
import json
import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import restore_app_env  # noqa: E402

Gtk.init_check(None)
restore_app_env()

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "slope")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
SHARE = "/usr/share/slope"

DEFAULTS = {
    "dock_apps": [
        "slope-files.desktop",
        "org.kde.falkon.desktop",
        "xfce4-terminal.desktop",
        "org.xfce.mousepad.desktop",
        "org.xfce.ristretto.desktop",
        "galculator.desktop",
        "xfce4-taskmanager.desktop",
        "slope-store.desktop",
        "slope-settings.desktop",
    ],
    "dock_icon_size": 52,
    "dock_magnify": True,
    "dock_magnify_size": 88,
    "dock_style": "shelf",          # "shelf" (3D glass) or "box"
    "dock_position": "bottom",      # "bottom", "left" or "right"
    "wallpaper": "slope:aqua",      # "slope:<palette>" or an image path
    "clock_24h": True,
    "clock_show_date": True,
    "clock_seconds": False,
    "close_hides_app": True,        # the macOS behaviour: red X keeps the app alive
    "compositor": "auto",           # "auto", "glx", "xrender" or "off" (slope-compositor)
}

# Friendlier, macOS-style names for the bundled apps.
FRIENDLY_NAMES = {
    "thunar": "Files",
    "slope-files": "Files",
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


def dark_theme():
    """True while the dark appearance (the Slope-Dark GTK theme) is on."""
    name = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
    return name.endswith("-Dark") or name.endswith("-dark")


def on_theme_change(cb):
    """Call cb() whenever light / dark appearance switches."""
    Gtk.Settings.get_default().connect("notify::gtk-theme-name", lambda *_: cb())


def install_css(light, dark=None):
    """An app's own style sheet, plus dark overrides while dark mode is on
    (app CSS beats the theme, so it has to follow the switch itself)."""
    screen = Gdk.Screen.get_default()
    prov = Gtk.CssProvider()
    prov.load_from_data(light)
    Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    if dark is None:
        return
    dprov = Gtk.CssProvider()
    dprov.load_from_data(dark)
    state = {"on": False}

    def update():
        want = dark_theme()
        if want and not state["on"]:
            Gtk.StyleContext.add_provider_for_screen(screen, dprov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        elif not want and state["on"]:
            Gtk.StyleContext.remove_provider_for_screen(screen, dprov)
        state["on"] = want
    update()
    on_theme_change(update)


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    # Files (slope-files) replaced Thunar as the file manager
    apps = data.get("dock_apps")
    if isinstance(apps, list) and "thunar.desktop" in apps:
        data["dock_apps"] = ["slope-files.desktop" if a == "thunar.desktop" else a for a in apps
                             if a != "slope-files.desktop" or "thunar.desktop" not in apps]
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


def letter_icon(name, size):
    """Glossy generated icon for apps that ship none."""
    import cairo
    from . import art
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    art.draw_letter_icon(cr, size * 0.04, size * 0.04, size * 0.92, name)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


def icon_for(appinfo, size, fallback="application-x-executable", name=None):
    """Pixbuf for an app.  Apps without a usable icon get a generated one."""
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
        for n in names:
            if theme.has_icon(n):
                try:
                    pb = theme.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
                    if pb is not None:
                        return pb
                except GLib.Error:
                    pass
        return letter_icon(name or friendly_name(appinfo), size)
    if name:
        return letter_icon(name, size)
    try:
        return theme.load_icon(fallback, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return None


def _app_keys(appinfo):
    keys = set()
    for v in (appinfo.get_startup_wm_class(), (appinfo.get_id() or "")[:-len(".desktop")],
              os.path.basename((appinfo.get_executable() or ""))):
        if v:
            keys.add(v.lower())
            keys.add(v.lower().rsplit(".", 1)[-1])
    keys.discard("")
    return keys


def reopen_hidden(appinfo):
    """Opening an app whose windows were all closed (it kept running) brings
    those windows back instead of starting it a second time, like on a Mac."""
    try:
        from .xutil import X11
        x = X11()
        keys = _app_keys(appinfo)
        targets = []
        for xid in x.clients():
            if x.is_hidden(xid) and {s.lower() for s in x.wm_class(xid) if s} & keys:
                targets.append(xid)
        for xid in targets:
            x.unhide(xid)
            x.activate(xid)
        x.flush()
        return bool(targets)
    except Exception as e:      # never block a launch
        print("slope: reopen failed:", e)
        return False


def _retire_windowless(appinfo):
    try:
        from . import keepalive
        from .xutil import X11
        x = X11()
        keepalive.retire_windowless(appinfo, {x.pid(c) for c in x.clients()})
    except Exception as e:
        print("slope: retire failed:", e)


def launch(appinfo, uris=None):
    if not uris and reopen_hidden(appinfo):
        return True
    _retire_windowless(appinfo)
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

_ACTION_NAMES = [
    (("private", "incognito"), "New Private Window"),
    (("new-window", "newwindow", "new_window", "window"), "New Window"),
    (("new-tab", "newtab", "new_tab", "tab"), "New Tab"),
]


def launcher_actions(appinfo):
    """The .desktop file's own actions ("New Window", "New Private Window",
    "Compose Message"...) as [(label, callback)], with Mac-style names for
    the common ones, so browsers' private windows are one click away."""
    if appinfo is None:
        return []
    out = []
    for a in appinfo.list_actions():
        low = a.lower()
        label = appinfo.get_action_name(a) or a
        rank = 9
        for i, (keys, nice) in enumerate(_ACTION_NAMES):
            if any(k in low for k in keys):
                label, rank = nice, i
                break
        if "profile" in low:
            continue                        # Firefox's profile manager: not for everyday menus

        def run(a=a):
            appinfo.launch_action(a, Gdk.Display.get_default().get_app_launch_context())
        out.append((rank, label, run))
    out.sort(key=lambda t: (t[0] if t[0] != 0 else 1.5, t[1]))  # Window, Private Window, Tab, others
    return [(label, run) for _r, label, run in out]


def open_app(desktop_id, target=None):
    """Start an app through its .desktop file (so it behaves as an app: it
    keeps running when its windows are closed), optionally opening a folder,
    file or URI.  Falls back to running the command directly."""
    try:
        info = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        info = None
    uris = None
    if target:
        uris = [target if "://" in target else Gio.File.new_for_path(target).get_uri()]
    if info is not None and launch(info, uris):
        return True
    cmd = {"slope-files.desktop": "slope-files", "thunar.desktop": "thunar", "xfce4-terminal.desktop": "xfce4-terminal",
           "pavucontrol.desktop": "pavucontrol"}.get(desktop_id, desktop_id[:-8])
    spawn([cmd] + ([target] if target else []))
    return False


def logout():
    spawn(["openbox", "--exit"])


def power(action):
    spawn(["systemctl", action])
