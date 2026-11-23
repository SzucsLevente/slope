"""slope-shell: desktop background, top bar and dock in one process."""
import os
import signal
import subprocess
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402
from .apps import Apps  # noqa: E402
from .common import (SETTINGS_PATH, open_app, SHARE, friendly_name, icon_for, load_settings,  # noqa: E402
                     logout, power, spawn)


class Desktop(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.shell = shell
        self.set_wmclass("slope-desktop", "Slope")
        self.set_title("slope-desktop")
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_below(True)
        self.stick()
        self.set_app_paintable(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self._cache = None
        self._old = None
        self._fade = 1.0
        self.place()
        from .desktop_icons import DesktopIcons
        self.icons = DesktopIcons(self, shell)

    def place(self):
        scr = self.get_screen()
        w, h = scr.get_width(), scr.get_height()
        self.move(0, 0)
        self.set_size_request(w, h)
        self.resize(w, h)
        self._cache = None
        self.show()
        self.queue_draw()

    def _render(self, w, h):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(surf)
        wp = self.shell.settings.get("wallpaper", "slope:aqua")
        if wp.startswith("slope:"):
            art.draw_wallpaper(cr, w, h, wp.split(":", 1)[1])
        else:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(wp)
                sc = max(w / pb.get_width(), h / pb.get_height())
                cr.translate((w - pb.get_width() * sc) / 2, (h - pb.get_height() * sc) / 2)
                cr.scale(sc, sc)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_GOOD)
                cr.paint()
            except GLib.Error:
                art.draw_wallpaper(cr, w, h, "aqua")
        return surf

    def _draw(self, w, cr):
        a = self.get_allocation()
        if self._cache is None or self._cache.get_width() != a.width or self._cache.get_height() != a.height:
            self._cache = self._render(a.width, a.height)
        cr.set_source_surface(self._cache, 0, 0)
        cr.paint()
        if self._old is not None and self._fade < 1.0:
            # cross-fade from the previous wallpaper
            cr.set_source_surface(self._old, 0, 0)
            cr.paint_with_alpha(1.0 - self._fade)
        if getattr(self, "icons", None) is not None:
            self.icons.draw(cr)
        return False

    def reload(self):
        """New wallpaper: cross-fade into it."""
        old = self._cache
        a = self.get_allocation()
        self._cache = self._render(a.width, a.height) if a.width > 1 else None
        if old is None or self._cache is None:
            self.queue_draw()
            return
        self._old, self._fade = old, 0.0

        def step():
            self._fade = min(1.0, self._fade + 0.06)
            self.queue_draw()
            if self._fade >= 1.0:
                self._old = None
                return False
            return True
        GLib.timeout_add(16, step)

    def _press(self, w, ev):
        self.shell.panel.dismiss_popovers()
        return self.icons.handle_press(ev)


class Shell:
    def __init__(self):
        self.settings = load_settings()
        self._load_css()
        self.apps = Apps(self.settings)
        self._xstrut = None

        from .dock import Dock
        from .panel import Panel
        self.desktop = Desktop(self)
        self.panel = Panel(self)
        self.dock = Dock(self)

        scr = Gdk.Screen.get_default()
        scr.connect("size-changed", lambda *_: GLib.timeout_add(300, self._relayout))
        scr.connect("monitors-changed", lambda *_: GLib.timeout_add(300, self._relayout))

        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        # a trackpad or mouse plugged in later gets the Trackpad & Mouse settings too
        seat = Gdk.Display.get_default().get_default_seat()
        seat.connect("device-added", lambda *_: GLib.timeout_add(800, self._apply_input))
        self._mon = Gio.File.new_for_path(SETTINGS_PATH).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._mon.connect("changed", self._settings_changed)

        # new apps: notify, give them glossy icons, mark them in Launchpad
        self._apps_check = None
        Gio.AppInfoMonitor.get().connect("changed", lambda *_: self._queue_app_check())
        GLib.timeout_add_seconds(8, self._check_new_apps)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)

    def _load_css(self):
        prov = Gtk.CssProvider()
        try:
            prov.load_from_path(os.path.join(SHARE, "shell.css"))
        except GLib.Error:
            here = os.path.join(os.path.dirname(__file__), "..", "..", "..", "share", "slope", "shell.css")
            try:
                prov.load_from_path(here)
            except GLib.Error:
                return
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _relayout(self):
        self.desktop.place()
        self.panel.place()
        self.dock.place()
        GLib.timeout_add(500, lambda: (self.desktop.icons.layout(), False)[1])
        return False

    def _apply_input(self):
        import threading
        from . import inputdev
        threading.Thread(target=inputdev.apply, daemon=True).start()
        return False

    def _settings_changed(self, *args):
        new = load_settings()
        wallpaper_changed = new.get("wallpaper") != self.settings.get("wallpaper")
        dock_moved = new.get("dock_position", "bottom") != self.settings.get("dock_position", "bottom")
        fx_changed = new.get("compositor", "auto") != self.settings.get("compositor", "auto")
        self.settings.clear()
        self.settings.update(new)
        if wallpaper_changed:
            self.desktop.reload()
        self.dock.configure()
        if dock_moved:
            # the free area for desktop icons changes with the Dock
            GLib.timeout_add(600, lambda: (self.desktop.icons.layout(), False)[1])
            # windows minimize towards the Dock: update the compositor too
            from . import picomconf
            picomconf.write(new.get("dock_position", "bottom"))
        if fx_changed and not dock_moved:
            # slope-compositor starts picom again in the new mode (or not at all)
            subprocess.run(["pkill", "-x", "-u", str(os.getuid()), "picom"], capture_output=True)
        self.panel._tick()

    # -- newly installed apps --------------------------------------------
    def _queue_app_check(self):
        if self._apps_check is None:
            self._apps_check = GLib.timeout_add_seconds(3, self._check_new_apps)

    def _check_new_apps(self):
        self._apps_check = None
        import json
        path = os.path.join(os.path.dirname(SETTINGS_PATH), "known-apps.json")
        apps = {a.get_id(): a for a in Gio.AppInfo.get_all()
                if isinstance(a, Gio.DesktopAppInfo) and a.should_show()}
        try:
            with open(path) as f:
                known = set(json.load(f))
        except (OSError, ValueError):
            known = None
        new = [] if known is None else sorted(set(apps) - known)
        try:
            with open(path + ".tmp", "w") as f:
                json.dump(sorted(apps), f)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
        current = [i for i in self.settings.get("new_apps", []) if i in apps]
        for app_id in new:
            info = apps[app_id]
            if app_id not in current:
                current.append(app_id)
            icon = info.get_icon()
            spawn(["notify-send", "-a", "Slope", "-i", icon.to_string() if icon else "system-software-install",
                   "%s was installed" % friendly_name(info), "You can find it in Launchpad."])
        if current != self.settings.get("new_apps", []):
            from .common import update_setting
            update_setting("new_apps", current)
        if new:
            # icons for apps installed in the home folder (system ones: apt hook)
            spawn(["slope-icons", "--user", "--update"])
            for delay in (2, 20):     # the apt hook may still be drawing icons
                GLib.timeout_add_seconds(delay, self._refresh_icons)
        return False

    def _refresh_icons(self):
        Gtk.IconTheme.get_default().rescan_if_needed()
        self.dock.icons.clear()
        self.dock.queue_draw()
        return False

    # -- helpers used by the panel and dock ----------------------------------
    def monitor_geometry(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        return mon.get_geometry()

    def set_strut(self, win, top=0, bottom=0, top_start=0, top_end=0, bottom_start=0, bottom_end=0,
                  left=0, right=0, left_start=0, left_end=0, right_start=0, right_end=0):
        gw = win.get_window()
        if gw is None:
            return
        from gi.repository import GdkX11  # noqa: F401
        xid = gw.get_xid()
        x = self.apps.x
        vals = [left, right, top, bottom, left_start, left_end, right_start, right_end,
                top_start, top_end, bottom_start, bottom_end]
        w = x.win(xid)
        w.change_property(x.atom("_NET_WM_STRUT_PARTIAL"), x.Xatom.CARDINAL, 32, vals)
        w.change_property(x.atom("_NET_WM_STRUT"), x.Xatom.CARDINAL, 32, vals[:4])
        x.flush()

    def ask(self, title, body, ok_label, destructive=True):
        dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=title)
        dlg.set_wmclass("slope-dialog", "Slope")
        dlg.format_secondary_text(body)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        ok.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_keep_above(True)
        img = Gtk.Image.new_from_pixbuf(self._logo(64))
        img.show()
        dlg.set_image(img)
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def confirm(self, action):
        texts = {
            "poweroff": ("Are you sure you want to shut down your computer now?", "Shut Down"),
            "restart": ("Are you sure you want to restart your computer now?", "Restart"),
            "logout": ("Are you sure you want to quit all applications and log out now?", "Log Out"),
        }
        title, label = texts[action]
        if self.ask(title, "Any unsaved changes will be lost.", label):
            if action == "logout":
                logout()
            elif action == "restart":
                power("reboot")
            else:
                power("poweroff")

    def _logo(self, size):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surf)
        art.draw_logo(cr, 2, 2, size - 4)
        return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)

    def about_app(self, group):
        dlg = Gtk.Dialog(title="About %s" % group.name)
        dlg.set_wmclass("slope-dialog", "Slope")
        dlg.set_default_size(320, -1)
        dlg.set_resizable(False)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_border_width(24)
        pb = icon_for(group.appinfo, 96) if group.appinfo else None
        if pb is not None:
            box.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        t = Gtk.Label()
        t.set_markup("<span size='x-large' weight='bold'>%s</span>" % GLib.markup_escape_text(group.name))
        box.pack_start(t, False, False, 0)
        if group.appinfo is not None:
            real = group.appinfo.get_name()
            if real and real != group.name:
                box.pack_start(Gtk.Label(label=real), False, False, 0)
            desc = group.appinfo.get_description() or ""
            if desc:
                d = Gtk.Label(label=desc)
                d.set_line_wrap(True)
                d.set_max_width_chars(40)
                d.set_justify(Gtk.Justification.CENTER)
                d.get_style_context().add_class("dim-label")
                box.pack_start(d, False, False, 0)
        dlg.add_button("OK", Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def wifi_connect(self, ssid, security):
        argv = ["nmcli", "dev", "wifi", "connect", ssid]
        if security and security != "--":
            known = os.popen("nmcli -t -f NAME connection show").read().splitlines()
            if ssid in known:
                spawn(["nmcli", "connection", "up", "id", ssid])
                return
            dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
                                    text="The Wi-Fi network “%s” requires a password." % ssid)
            dlg.set_wmclass("slope-dialog", "Slope")
            entry = Gtk.Entry()
            entry.set_visibility(False)
            entry.set_activates_default(True)
            entry.set_placeholder_text("Password")
            dlg.get_message_area().pack_start(entry, False, False, 0)
            dlg.set_default_response(Gtk.ResponseType.OK)
            dlg.show_all()
            resp = dlg.run()
            pw = entry.get_text()
            dlg.destroy()
            if resp != Gtk.ResponseType.OK:
                return
            argv += ["password", pw]
        spawn(argv)


def main():
    GLib.set_prgname("slope-shell")
    Gdk.set_program_class("Slope")
    Shell()
    Gtk.main()
    # skip interpreter teardown: finalising libwnck windows at exit can crash
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
