"""slope-shell: desktop background, top bar and dock in one process."""
import os
import signal
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402
from .apps import Apps  # noqa: E402
from .common import SETTINGS_PATH, SHARE, icon_for, load_settings, logout, power, spawn  # noqa: E402


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
        if ev.button != 3:
            return False
        menu = Gtk.Menu()
        for label, cb in (
            ("New Folder on Desktop", self._new_folder),
            ("Open Terminal", lambda: spawn(["xfce4-terminal"])),
            ("Open Files", lambda: spawn(["thunar"])),
            (None, None),
            ("Change Wallpaper…", lambda: spawn(["slope-settings", "--page", "appearance"])),
            ("Display Settings…", lambda: spawn(["slope-settings", "--page", "displays"])),
        ):
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda m, c=cb: c())
            menu.append(mi)
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(ev)
        return True

    def _new_folder(self):
        d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP) or os.path.expanduser("~/Desktop")
        os.makedirs(d, exist_ok=True)
        base = os.path.join(d, "untitled folder")
        p, i = base, 2
        while os.path.exists(p):
            p = "%s %d" % (base, i)
            i += 1
        os.makedirs(p)
        spawn(["thunar", d])


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
        self._mon = Gio.File.new_for_path(SETTINGS_PATH).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._mon.connect("changed", self._settings_changed)

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
        return False

    def _settings_changed(self, *args):
        new = load_settings()
        wallpaper_changed = new.get("wallpaper") != self.settings.get("wallpaper")
        self.settings.clear()
        self.settings.update(new)
        if wallpaper_changed:
            self.desktop.reload()
        self.dock.configure()
        self.panel._tick()

    # -- helpers used by the panel and dock ----------------------------------
    def monitor_geometry(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        return mon.get_geometry()

    def set_strut(self, win, top=0, bottom=0, top_start=0, top_end=0, bottom_start=0, bottom_end=0):
        gw = win.get_window()
        if gw is None:
            return
        from gi.repository import GdkX11  # noqa: F401
        xid = gw.get_xid()
        x = self.apps.x
        vals = [0, 0, top, bottom, 0, 0, 0, 0, top_start, top_end, bottom_start, bottom_end]
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
