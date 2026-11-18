"""slope-settings: System Settings, About This Computer, Force Quit and friends."""
import argparse
import os
import platform
import re
import sys
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402
from .common import load_settings, logout, run_quiet, save_settings, spawn  # noqa: E402

VERSION = "1.0"
CODENAME = "Glacier"


def read_version():
    try:
        with open("/etc/slope-release") as f:
            for line in f:
                if line.startswith("VERSION="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return VERSION


def logo_pixbuf(size):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    art.draw_logo(cr, size * 0.04, size * 0.04, size * 0.92)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


def wallpaper_thumb(name, w=200, h=112):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    art.rounded_rect(cr, 0, 0, w, h, 10)
    cr.clip()
    art.draw_wallpaper(cr, w, h, name)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h)


def heading(text):
    lbl = Gtk.Label(xalign=0)
    lbl.set_markup("<span size='large' weight='bold'>%s</span>" % GLib.markup_escape_text(text))
    lbl.set_margin_bottom(6)
    return lbl


def row(label, widget):
    b = Gtk.Box(spacing=12)
    lbl = Gtk.Label(label=label, xalign=0)
    b.pack_start(lbl, True, True, 0)
    b.pack_end(widget, False, False, 0)
    return b


def system_info():
    info = {}
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"] = re.sub(r"\s+", " ", line.split(":", 1)[1].strip())
                    break
        info["cores"] = os.cpu_count()
    except OSError:
        pass
    try:
        with open("/proc/meminfo") as f:
            kb = int(f.readline().split()[1])
            info["mem"] = "%.1f GB" % (kb / 1024 / 1024)
    except (OSError, ValueError, IndexError):
        pass
    gpu = ""
    for line in run_quiet(["lspci"]).splitlines():
        if "VGA" in line or "3D controller" in line or "Display controller" in line:
            gpu = line.split(":", 2)[-1].strip()
            break
    info["gpu"] = gpu or "Unknown"
    info["kernel"] = platform.release()
    base = ""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    base = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    info["base"] = base
    disp = Gdk.Display.get_default()
    mon = disp.get_primary_monitor() or disp.get_monitor(0)
    g = mon.get_geometry()
    info["display"] = "%d × %d" % (g.width, g.height)
    info["host"] = GLib.get_host_name()
    try:
        st = os.statvfs("/")
        info["disk"] = "%.1f GB available of %.1f GB" % (st.f_bavail * st.f_frsize / 1e9, st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        pass
    return info


class AboutWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="About This Computer")
        self.set_wmclass("slope-about", "Slope-about")
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_skip_taskbar_hint(True)
        box = Gtk.Box(spacing=28)
        box.set_border_width(30)
        self.add(box)
        box.pack_start(Gtk.Image.new_from_pixbuf(logo_pixbuf(150)), False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(v, True, True, 0)
        t = Gtk.Label(xalign=0)
        t.set_markup("<span size='xx-large'><b>Slope</b> %s</span>" % GLib.markup_escape_text(CODENAME))
        v.pack_start(t, False, False, 0)
        ver = Gtk.Label(label="Version %s" % read_version(), xalign=0)
        ver.get_style_context().add_class("dim-label")
        v.pack_start(ver, False, False, 0)
        v.pack_start(Gtk.Box(), False, False, 6)
        info = system_info()
        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        rows = [("Computer Name", info.get("host", "")),
                ("Processor", "%s (%s cores)" % (info.get("cpu", "Unknown"), info.get("cores", "?"))),
                ("Memory", info.get("mem", "")),
                ("Graphics", info.get("gpu", "")),
                ("Display", info.get("display", "")),
                ("Startup Disk", info.get("disk", "")),
                ("Kernel", "Linux " + info.get("kernel", "")),
                ("Based on", info.get("base", ""))]
        for i, (k, val) in enumerate(rows):
            kl = Gtk.Label(xalign=1)
            kl.set_markup("<b>%s</b>" % GLib.markup_escape_text(k))
            vl = Gtk.Label(label=val, xalign=0)
            vl.set_selectable(True)
            vl.set_max_width_chars(48)
            vl.set_line_wrap(True)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(vl, 1, i, 1, 1)
        v.pack_start(grid, False, False, 0)
        bb = Gtk.Box(spacing=8)
        bb.set_margin_top(16)
        more = Gtk.Button(label="System Settings…")
        more.connect("clicked", lambda *_: spawn(["slope-settings"]))
        bb.pack_start(more, False, False, 0)
        ok = Gtk.Button(label="OK")
        ok.get_style_context().add_class("suggested-action")
        ok.connect("clicked", lambda *_: self.destroy())
        bb.pack_end(ok, False, False, 0)
        v.pack_end(bb, False, False, 0)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()


class ForceQuitWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Force Quit Applications")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck
        self.set_wmclass("slope-forcequit", "Slope-forcequit")
        self.set_default_size(380, 360)
        self.set_skip_taskbar_hint(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(16)
        self.add(box)
        lbl = Gtk.Label(xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(46)
        lbl.set_markup("If an app doesn’t respond for a while, select its name and click Force Quit.\n"
                       "<small>You may lose any unsaved changes.</small>")
        box.pack_start(lbl, False, False, 0)
        self.store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, int)
        tv = Gtk.TreeView(model=self.store)
        tv.set_headers_visible(False)
        col = Gtk.TreeViewColumn()
        pr = Gtk.CellRendererPixbuf()
        tr = Gtk.CellRendererText()
        col.pack_start(pr, False)
        col.pack_start(tr, True)
        col.add_attribute(pr, "pixbuf", 0)
        col.add_attribute(tr, "text", 1)
        tv.append_column(col)
        self.tv = tv
        sw = Gtk.ScrolledWindow()
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.add(tv)
        box.pack_start(sw, True, True, 0)
        bb = Gtk.Box()
        btn = Gtk.Button(label="Force Quit")
        btn.get_style_context().add_class("destructive-action")
        btn.connect("clicked", self._kill)
        bb.pack_end(btn, False, False, 0)
        box.pack_start(bb, False, False, 0)
        screen = Wnck.Screen.get_default()
        screen.force_update()
        seen = set()
        from .common import AppIndex, friendly_name
        idx = AppIndex()
        for w in screen.get_windows():
            if w.get_window_type() not in (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG):
                continue
            if (w.get_class_group_name() or "").startswith("Slope"):
                continue
            pid = w.get_pid()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            info = idx.match(w.get_class_group_name(), w.get_class_instance_name())
            name = friendly_name(info) if info else (w.get_class_group_name() or w.get_name())
            icon = w.get_mini_icon()
            self.store.append([icon, name, pid])
        self.connect("destroy", Gtk.main_quit)
        self.show_all()

    def _kill(self, *_):
        model, it = self.tv.get_selection().get_selected()
        if it is None:
            return
        pid = model[it][2]
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        model.remove(it)


class SettingsWindow(Gtk.Window):
    def __init__(self, page=None):
        super().__init__(title="System Settings")
        self.set_wmclass("slope-settings", "Slope-settings")
        self.set_default_size(820, 560)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("preferences-system")
        self.s = load_settings()
        hb = Gtk.Box()
        self.add(hb)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        side = Gtk.StackSidebar()
        side.set_stack(self.stack)
        side.set_size_request(200, -1)
        hb.pack_start(side, False, False, 0)
        hb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        hb.pack_start(self.stack, True, True, 0)

        self._add("appearance", "Wallpaper", self._appearance())
        self._add("dock", "Dock & Windows", self._dock())
        self._add("displays", "Displays", self._displays())
        self._add("clock", "Date & Time", self._clock())
        self._add("shortcuts", "Keyboard Shortcuts", self._shortcuts())
        self._add("about", "About", self._about())
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        if page:
            child = self.stack.get_child_by_name(page)
            if child is not None:
                self.stack.set_visible_child(child)

    def _add(self, name, title, widget):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        widget.set_border_width(24)
        sw.add(widget)
        self.stack.add_titled(sw, name, title)

    def _save(self, key, value):
        self.s[key] = value
        save_settings(self.s)

    # -- pages -------------------------------------------------------------
    def _appearance(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.pack_start(heading("Wallpaper"), False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(3)
        flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        cur = self.s.get("wallpaper", "slope:aqua")
        for name, label in art.PALETTE_LABELS.items():
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            b.pack_start(Gtk.Image.new_from_pixbuf(wallpaper_thumb(name)), False, False, 0)
            b.pack_start(Gtk.Label(label=label), False, False, 0)
            child = Gtk.FlowBoxChild()
            child.add(b)
            child.value = "slope:" + name
            flow.add(child)
            if child.value == cur:
                flow.select_child(child)
        flow.connect("child-activated", lambda f, c: self._save("wallpaper", c.value))
        v.pack_start(flow, False, False, 0)
        pick = Gtk.Button(label="Choose a Picture…")
        pick.set_halign(Gtk.Align.START)
        pick.connect("clicked", self._pick_wallpaper)
        v.pack_start(pick, False, False, 0)
        return v

    def _pick_wallpaper(self, *_):
        dlg = Gtk.FileChooserDialog(title="Choose a Picture", parent=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK)
        f = Gtk.FileFilter()
        f.set_name("Images")
        f.add_pixbuf_formats()
        dlg.add_filter(f)
        pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pics and os.path.isdir(pics):
            dlg.set_current_folder(pics)
        if dlg.run() == Gtk.ResponseType.OK:
            self._save("wallpaper", dlg.get_filename())
        dlg.destroy()

    def _dock(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        v.pack_start(heading("Dock"), False, False, 0)

        size = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 32, 80, 2)
        size.set_value(self.s.get("dock_icon_size", 52))
        size.set_size_request(260, -1)
        size.set_draw_value(False)
        size.connect("value-changed", lambda w: self._save("dock_icon_size", int(w.get_value())))
        v.pack_start(row("Size", size), False, False, 0)

        mag = Gtk.Switch()
        mag.set_active(self.s.get("dock_magnify", True))
        mag.connect("notify::active", lambda w, p: self._save("dock_magnify", w.get_active()))
        v.pack_start(row("Magnification", mag), False, False, 0)

        msize = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 56, 128, 2)
        msize.set_value(self.s.get("dock_magnify_size", 88))
        msize.set_size_request(260, -1)
        msize.set_draw_value(False)
        msize.connect("value-changed", lambda w: self._save("dock_magnify_size", int(w.get_value())))
        v.pack_start(row("Magnified size", msize), False, False, 0)

        style = Gtk.ComboBoxText()
        style.append("shelf", "Glass shelf (3D)")
        style.append("box", "Glass panel")
        style.set_active_id(self.s.get("dock_style", "shelf"))
        style.connect("changed", lambda w: self._save("dock_style", w.get_active_id()))
        v.pack_start(row("Style", style), False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Windows"), False, False, 0)
        hide = Gtk.Switch()
        hide.set_active(self.s.get("close_hides_app", True))
        hide.connect("notify::active", lambda w, p: self._save("close_hides_app", w.get_active()))
        v.pack_start(row("Keep apps running when their last window is closed", hide), False, False, 0)
        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_markup("<small>Like on a Mac: the red button closes the window but the app stays in the "
                        "Dock with a glowing dot. Click its icon to bring the window back, or choose "
                        "<b>Quit</b> (⌘Q) to really close the app.</small>")
        note.get_style_context().add_class("dim-label")
        v.pack_start(note, False, False, 0)
        return v

    def _displays(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.pack_start(heading("Displays"), False, False, 0)
        out = run_quiet(["xrandr", "-q"])
        outputs = []
        cur = None
        for line in out.splitlines():
            m = re.match(r"^(\S+) connected", line)
            if m:
                cur = {"name": m.group(1), "modes": [], "current": None}
                outputs.append(cur)
                continue
            m = re.match(r"^\s+(\d+x\d+)\s+(.*)$", line)
            if m and cur is not None:
                mode = m.group(1)
                if mode not in cur["modes"]:
                    cur["modes"].append(mode)
                if "*" in m.group(2):
                    cur["current"] = mode
            elif not line.startswith(" "):
                cur = None
        if not outputs:
            v.pack_start(Gtk.Label(label="No displays found.", xalign=0), False, False, 0)
        for o in outputs:
            combo = Gtk.ComboBoxText()
            for mode in o["modes"]:
                combo.append(mode, mode.replace("x", " × "))
            if o["current"]:
                combo.set_active_id(o["current"])
            combo.connect("changed", lambda c, name=o["name"]: spawn(
                ["xrandr", "--output", name, "--mode", c.get_active_id()]))
            v.pack_start(row("Resolution (%s)" % o["name"], combo), False, False, 0)
        return v

    def _clock(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.pack_start(heading("Menu Bar Clock"), False, False, 0)
        for key, label in (("clock_24h", "Use a 24-hour clock"),
                           ("clock_show_date", "Show the date"),
                           ("clock_seconds", "Show seconds")):
            sw = Gtk.Switch()
            sw.set_active(self.s.get(key, False))
            sw.connect("notify::active", lambda w, p, k=key: self._save(k, w.get_active()))
            v.pack_start(row(label, sw), False, False, 0)
        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Time Zone"), False, False, 0)
        tz = run_quiet(["timedatectl", "show", "-p", "Timezone", "--value"]).strip() or time.tzname[0]
        combo = Gtk.ComboBoxText.new_with_entry()
        zones = []
        try:
            zones = run_quiet(["timedatectl", "list-timezones"], timeout=5).split()
        except Exception:
            pass
        for z in zones:
            combo.append(z, z)
        combo.get_child().set_text(tz)
        apply = Gtk.Button(label="Set Time Zone")
        apply.connect("clicked", lambda *_: spawn(["pkexec", "timedatectl", "set-timezone",
                                                   combo.get_child().get_text()]))
        hb = Gtk.Box(spacing=8)
        hb.pack_start(combo, True, True, 0)
        hb.pack_start(apply, False, False, 0)
        v.pack_start(hb, False, False, 0)
        return v

    def _shortcuts(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(heading("Keyboard Shortcuts"), False, False, 0)
        note = Gtk.Label(xalign=0)
        note.set_markup("<small>The <b>Super</b> (Windows) key works like the Mac <b>⌘ Command</b> key.</small>")
        note.get_style_context().add_class("dim-label")
        v.pack_start(note, False, False, 0)
        grid = Gtk.Grid(column_spacing=24, row_spacing=6)
        items = [
            ("⌘ Space", "Spotlight search"),
            ("⌘ W  /  Alt F4", "Close window (the app keeps running)"),
            ("⌘ Q", "Quit the app"),
            ("⌘ H", "Hide the app"),
            ("⌥ ⌘ H", "Hide other apps"),
            ("⌘ M", "Minimize window"),
            ("⌘ Tab", "Switch windows"),
            ("⌘ ↑ / ← / →", "Zoom / tile left / tile right"),
            ("⌃ ⌘ F", "Full screen"),
            ("⌘ ,", "System Settings"),
            ("⌥ ⌘ Esc", "Force Quit"),
            ("⌘ T", "New Terminal"),
            ("⌘ E", "Files"),
            ("⇧ ⌘ 3  /  Print", "Screenshot to Pictures"),
            ("⇧ ⌘ Q", "Log out"),
        ]
        for i, (k, d) in enumerate(items):
            kl = Gtk.Label(xalign=0)
            kl.set_markup("<b>%s</b>" % GLib.markup_escape_text(k))
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(Gtk.Label(label=d, xalign=0), 1, i, 1, 1)
        v.pack_start(grid, False, False, 0)
        return v

    def _about(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(Gtk.Image.new_from_pixbuf(logo_pixbuf(128)), False, False, 0)
        t = Gtk.Label()
        t.set_markup("<span size='xx-large'><b>Slope</b> %s</span>" % CODENAME)
        v.pack_start(t, False, False, 0)
        v.pack_start(Gtk.Label(label="Version %s" % read_version()), False, False, 0)
        info = system_info()
        for k, label in (("cpu", "Processor"), ("mem", "Memory"), ("gpu", "Graphics"), ("base", "Based on")):
            if info.get(k):
                lbl = Gtk.Label()
                lbl.set_markup("<b>%s</b>  %s" % (label, GLib.markup_escape_text(str(info[k]))))
                v.pack_start(lbl, False, False, 0)
        b = Gtk.Button(label="More Info…")
        b.set_halign(Gtk.Align.CENTER)
        b.get_style_context().add_class("suggested-action")
        b.connect("clicked", lambda *_: spawn(["slope-settings", "--about"]))
        v.pack_start(b, False, False, 12)
        return v


def screenshot():
    root = Gdk.get_default_root_window()
    w, h = root.get_width(), root.get_height()
    pb = Gdk.pixbuf_get_from_window(root, 0, 0, w, h)
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~/Pictures")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("Screenshot %Y-%m-%d at %H.%M.%S.png"))
    pb.savev(path, "png", [], [])
    spawn(["notify-send", "-i", "camera-photo", "Screenshot saved", os.path.basename(path)])


def confirm_logout():
    dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE,
                            text="Are you sure you want to quit all applications and log out now?")
    dlg.set_wmclass("slope-dialog", "Slope")
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok = dlg.add_button("Log Out", Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action")
    dlg.set_position(Gtk.WindowPosition.CENTER)
    img = Gtk.Image.new_from_pixbuf(logo_pixbuf(64))
    img.show()
    dlg.set_image(img)
    if dlg.run() == Gtk.ResponseType.OK:
        logout()
    dlg.destroy()


def main():
    ap = argparse.ArgumentParser(prog="slope-settings")
    ap.add_argument("--page")
    ap.add_argument("--about", action="store_true")
    ap.add_argument("--force-quit", action="store_true")
    ap.add_argument("--logout", action="store_true")
    ap.add_argument("--screenshot", action="store_true")
    args = ap.parse_args()
    GLib.set_prgname("slope-settings")
    if args.screenshot:
        Gtk.init(None)
        screenshot()
        return 0
    if args.logout:
        Gtk.init(None)
        confirm_logout()
        return 0
    if args.about:
        AboutWindow()
    elif args.force_quit:
        ForceQuitWindow()
    else:
        app = Gio.Application(application_id="org.slope.Settings", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        try:
            app.register(None)
        except GLib.Error:
            pass
        if app.get_is_remote():
            app.run(sys.argv)
            return 0
        win = SettingsWindow(args.page)

        def on_cmd(a, cl):
            argv = cl.get_arguments()
            if "--page" in argv and argv.index("--page") + 1 < len(argv):
                child = win.stack.get_child_by_name(argv[argv.index("--page") + 1])
                if child is not None:
                    win.stack.set_visible_child(child)
            win.present()
            return 0
        app.connect("command-line", on_cmd)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
