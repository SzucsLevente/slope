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
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402
from .common import load_settings, logout, run_quiet, save_settings, spawn  # noqa: E402

VERSION = "1.2"
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


def pick_layout(parent):
    """A searchable list of keyboard layouts; returns [layout, variant] or None."""
    from . import keyboard
    dlg = Gtk.Dialog(title="Add Input Source", transient_for=parent, modal=True)
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
    dlg.set_default_size(460, 480)
    box = dlg.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    search = Gtk.SearchEntry()
    search.set_placeholder_text("Search, e.g. Hungarian")
    box.pack_start(search, False, False, 0)
    store = Gtk.ListStore(str, str, str)       # description, layout, variant
    for lay, (desc, variants) in sorted(keyboard.catalog().items(), key=lambda kv: kv[1][0].lower()):
        store.append([desc, lay, ""])
        for var, vdesc in variants:
            store.append([vdesc, lay, var])
    filt = store.filter_new()
    filt.set_visible_func(lambda m, it, _d: search.get_text().lower() in m[it][0].lower())
    search.connect("search-changed", lambda *_: filt.refilter())
    tv = Gtk.TreeView(model=filt)
    tv.set_headers_visible(False)
    tv.append_column(Gtk.TreeViewColumn("Layout", Gtk.CellRendererText(), text=0))
    tv.connect("row-activated", lambda *_: dlg.response(Gtk.ResponseType.OK))
    sw = Gtk.ScrolledWindow()
    sw.set_shadow_type(Gtk.ShadowType.IN)
    sw.add(tv)
    box.pack_start(sw, True, True, 0)
    dlg.show_all()
    result = None
    if dlg.run() == Gtk.ResponseType.OK:
        model, it = tv.get_selection().get_selected()
        if it is not None:
            result = [model[it][1], model[it][2]]
    dlg.destroy()
    return result


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


class WelcomeWindow(Gtk.Window):
    """Shown on the live USB: try Slope or install it."""

    def __init__(self):
        super().__init__(title="Welcome to Slope")
        self.set_wmclass("slope-welcome", "Slope-welcome")
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_skip_taskbar_hint(True)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        v.set_border_width(32)
        self.add(v)
        v.pack_start(Gtk.Image.new_from_pixbuf(logo_pixbuf(128)), False, False, 0)
        t = Gtk.Label()
        t.set_markup("<span size='xx-large' weight='bold'>Welcome to Slope</span>")
        v.pack_start(t, False, False, 0)
        body = Gtk.Label()
        body.set_line_wrap(True)
        body.set_max_width_chars(52)
        body.set_justify(Gtk.Justification.CENTER)
        body.set_text("You are running Slope from the USB stick or DVD. Look around as long as you "
                      "like: nothing is changed on this computer.\n\nWhen you are ready, install "
                      "Slope on the disk. You can also start the installer later from the Dock "
                      "or the Slope menu.")
        v.pack_start(body, False, False, 6)
        bb = Gtk.Box(spacing=12)
        bb.set_halign(Gtk.Align.CENTER)
        bb.set_margin_top(10)
        tryit = Gtk.Button(label="Try Slope")
        tryit.connect("clicked", lambda *_: self.destroy())
        install = Gtk.Button(label="Install Slope…")
        install.get_style_context().add_class("suggested-action")
        install.connect("clicked", lambda *_: (spawn(["slope-install"]), self.destroy()))
        bb.pack_start(tryit, False, False, 0)
        bb.pack_start(install, False, False, 0)
        v.pack_start(bb, False, False, 0)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        install.grab_focus()


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
        self.set_icon_name("slope-settings")
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
        self._add("trackpad", "Trackpad & Mouse", self._trackpad())
        self._add("network", "Wi-Fi & Network", self._network())
        self._add("displays", "Displays", self._displays())
        self._add("clock", "Date & Time", self._clock())
        self._add("keyboard", "Keyboard", self._keyboard())
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

        pos = Gtk.ComboBoxText()
        pos.append("left", "Left")
        pos.append("bottom", "Bottom")
        pos.append("right", "Right")
        pos.set_active_id(self.s.get("dock_position", "bottom"))
        pos.connect("changed", lambda w: self._save("dock_position", w.get_active_id()))
        v.pack_start(row("Position on screen", pos), False, False, 0)

        style = Gtk.ComboBoxText()
        style.append("shelf", "Glass shelf (3D)")
        style.append("box", "Glass panel")
        style.set_active_id(self.s.get("dock_style", "shelf"))
        style.connect("changed", lambda w: self._save("dock_style", w.get_active_id()))
        v.pack_start(row("Style", style), False, False, 0)
        snote = Gtk.Label(xalign=0)
        snote.set_markup("<small>On the left or right the Dock is always a glass panel.</small>")
        snote.get_style_context().add_class("dim-label")
        v.pack_start(snote, False, False, 0)

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

        fx = Gtk.ComboBoxText()
        fx.append("auto", "Automatic")
        fx.append("glx", "Full (OpenGL, frosted glass)")
        fx.append("xrender", "Basic (no frosted glass)")
        fx.append("off", "Off (no transparency or animations)")
        fx.set_active_id(self.s.get("compositor", "auto"))
        fx.connect("changed", lambda w: self._save("compositor", w.get_active_id()))
        v.pack_start(row("Visual effects", fx), False, False, 0)
        fxnote = Gtk.Label(xalign=0)
        fxnote.set_line_wrap(True)
        fxnote.set_markup("<small>If windows, the Dock or menus look wrong (smeared, black or blurry areas), "
                          "try <b>Basic</b>, or <b>Off</b>.</small>")
        fxnote.get_style_context().add_class("dim-label")
        v.pack_start(fxnote, False, False, 0)
        return v

    def _trackpad(self):
        from . import inputdev
        cfg = inputdev.load()
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        def save(key, value):
            cfg[key] = value
            self._save(key, value)
            inputdev.apply(cfg)

        def switch(key, label, note=None):
            sw = Gtk.Switch()
            sw.set_active(bool(cfg[key]))
            sw.connect("notify::active", lambda w, p: save(key, w.get_active()))
            v.pack_start(row(label, sw), False, False, 0)
            if note:
                lab = Gtk.Label(xalign=0)
                lab.set_line_wrap(True)
                lab.set_markup("<small>%s</small>" % note)
                lab.get_style_context().add_class("dim-label")
                v.pack_start(lab, False, False, 0)

        def slider(key, label, lo, hi, step, invert=False, left="Slow", right="Fast"):
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
            sc.set_draw_value(False)
            sc.set_size_request(260, -1)
            sc.set_inverted(invert)
            sc.set_value(cfg[key])
            sc.add_mark(lo, Gtk.PositionType.BOTTOM, right if invert else left)
            sc.add_mark(hi, Gtk.PositionType.BOTTOM, left if invert else right)
            sc.connect("value-changed", lambda w: save(key, round(w.get_value(), 2)
                                                         if isinstance(step, float) else int(w.get_value())))
            v.pack_start(row(label, sc), False, False, 0)

        def combo(key, label, options):
            cb = Gtk.ComboBoxText()
            for oid, text in options:
                cb.append(oid, text)
            cb.set_active_id(cfg[key])
            cb.connect("changed", lambda w: save(key, w.get_active_id()))
            v.pack_start(row(label, cb), False, False, 0)

        found = inputdev.devices()
        v.pack_start(heading("Scrolling"), False, False, 0)
        switch("natural_scroll", "Natural scrolling",
               "On: the content moves with your fingers, like on a Mac or a phone (swipe up to "
               "scroll down). Off: scrolling works like a classic mouse wheel. Used for the "
               "trackpad and every mouse.")
        names = ", ".join(GLib.markup_escape_text(n) for _d, n, _k in found) or "none found"
        lab = Gtk.Label(xalign=0)
        lab.set_line_wrap(True)
        lab.set_markup("<small>Pointing devices: %s</small>" % names)
        lab.get_style_context().add_class("dim-label")
        v.pack_start(lab, False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Trackpad"), False, False, 0)
        if not any(k == "touchpad" for _d, _n, k in found):
            lab = Gtk.Label(xalign=0)
            lab.set_line_wrap(True)
            lab.set_markup("<small>No trackpad is connected right now. These settings are used as "
                           "soon as one is.</small>")
            lab.get_style_context().add_class("dim-label")
            v.pack_start(lab, False, False, 0)
        switch("tp_tap", "Tap to click")
        combo("tp_click", "Secondary click", [("clickfinger", "Click with two fingers"),
                                              ("areas", "Click in the bottom-right corner")])
        combo("tp_scroll", "Scroll with", [("twofinger", "Two fingers"), ("edge", "The right edge")])
        slider("tp_speed", "Tracking speed", -1.0, 1.0, 0.05)
        slider("tp_scroll_speed", "Scrolling speed", 5, 50, 1, invert=True)
        switch("tp_dwt", "Ignore the trackpad while typing")

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Gestures"), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        labels = [(k, l) for k, l in inputdev.ACTIONS.items()]
        for i, (g, text) in enumerate(inputdev.GESTURES):
            lab = Gtk.Label(label=text, xalign=0)
            lab.set_hexpand(True)
            cb = Gtk.ComboBoxText()
            for aid, (alabel, _cmd) in labels:
                cb.append(aid, alabel)
            current = cfg["gestures"].get(g, "none")
            if current.startswith("cmd:"):
                cb.append(current, "Command: " + current[4:])
            cb.set_active_id(current)
            if cb.get_active_id() is None:
                cb.set_active_id("none")

            def changed(w, g=g):
                gestures = dict(cfg["gestures"])
                gestures[g] = w.get_active_id()
                save("gestures", gestures)
            cb.connect("changed", changed)
            grid.attach(lab, 0, i, 1, 1)
            grid.attach(cb, 1, i, 1, 1)
        v.pack_start(grid, False, False, 0)
        lab = Gtk.Label(xalign=0)
        lab.set_line_wrap(True)
        lab.set_markup("<small>Two fingers always scroll. To run your own command for a gesture, put "
                       "<tt>\"swipe-up-3\": \"cmd:your command\"</tt> under <tt>\"gestures\"</tt> in "
                       "<tt>~/.config/slope/settings.json</tt>.</small>")
        lab.get_style_context().add_class("dim-label")
        v.pack_start(lab, False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Mouse"), False, False, 0)
        slider("mouse_speed", "Tracking speed", -1.0, 1.0, 0.05)
        return v

    def _network(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.pack_start(heading("Wi-Fi & Network"), False, False, 0)
        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_text("Wi-Fi is handled by iwd and wired connections by NetworkManager. "
                      "Ethernet connects automatically when you plug in a cable.")
        v.pack_start(note, False, False, 0)
        b = Gtk.Button(label="Open Wi-Fi…")
        b.get_style_context().add_class("suggested-action")
        b.set_halign(Gtk.Align.START)
        b.connect("clicked", lambda *_: spawn(["slope-wifi"]))
        v.pack_start(b, False, False, 0)
        b2 = Gtk.Button(label="Advanced Network Settings…")
        b2.set_halign(Gtk.Align.START)
        b2.connect("clicked", lambda *_: spawn(["nm-connection-editor"]))
        v.pack_start(b2, False, False, 0)
        return v

    def _displays(self):
        from . import display
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        v.pack_start(heading("Brightness"), False, False, 0)
        bright = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        bright.set_draw_value(False)
        bright.set_size_request(260, -1)
        bright.set_value(display.get_brightness())
        bright.connect("value-changed", lambda w: display.set_brightness(w.get_value()))
        v.pack_start(row("Screen brightness", bright), False, False, 0)
        bnote = Gtk.Label(xalign=0)
        bnote.set_line_wrap(True)
        bnote.set_markup("<small>The brightness keys (F1 / F2 on a Mac) work too. "
                         + ("This screen has no backlight control, so it is dimmed in software."
                            if display.backlight() is None else "") + "</small>")
        bnote.get_style_context().add_class("dim-label")
        v.pack_start(bnote, False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Displays"), False, False, 0)
        outs = display.outputs()
        if not outs:
            v.pack_start(Gtk.Label(label="No displays found.", xalign=0), False, False, 0)
        for o in outs:
            combo = Gtk.ComboBoxText()
            for mode in o["modes"]:
                combo.append(mode, mode.replace("x", " × "))
            if o["current"]:
                combo.set_active_id(o["current"])
            # saved, so the resolution is set again at the next login
            combo.connect("changed", lambda c, name=o["name"]: display.set_mode(name, c.get_active_id()))
            v.pack_start(row("Resolution (%s)" % o["name"], combo), False, False, 0)
        return v

    def _keyboard(self):
        from . import keyboard
        cfg = keyboard.load()
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        def save(key, value):
            cfg[key] = value
            self._save(key, value)
            keyboard.apply(cfg)

        v.pack_start(heading("Input Sources"), False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.SINGLE)
        frame = Gtk.Frame()
        frame.add(lb)
        v.pack_start(frame, False, False, 0)

        def fill():
            for c in lb.get_children():
                lb.remove(c)
            for i, (lay, var) in enumerate(keyboard.layouts(cfg)):
                r = Gtk.ListBoxRow()
                hb = Gtk.Box(spacing=8)
                hb.set_border_width(8)
                hb.pack_start(Gtk.Label(label=keyboard.describe(lay, var), xalign=0), True, True, 0)
                if i == 0:
                    d = Gtk.Label(label="Default")
                    d.get_style_context().add_class("dim-label")
                    hb.pack_end(d, False, False, 0)
                r.add(hb)
                lb.add(r)
            lb.show_all()

        def current():
            return [list(x) for x in keyboard.layouts(cfg)]

        def add(*_):
            choice = pick_layout(self)
            if choice:
                lay = current()
                if choice not in lay:
                    lay.append(choice)
                save("kb_layouts", lay)
                fill()

        def remove(*_):
            r = lb.get_selected_row()
            lay = current()
            if r is None or len(lay) < 2:
                return
            del lay[r.get_index()]
            save("kb_layouts", lay)
            fill()

        def make_default(*_):
            r = lb.get_selected_row()
            lay = current()
            if r is None or r.get_index() == 0:
                return
            lay.insert(0, lay.pop(r.get_index()))
            save("kb_layouts", lay)
            fill()

        bb = Gtk.Box(spacing=6)
        for label, cb in (("Add…", add), ("Remove", remove), ("Make Default", make_default)):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            bb.pack_start(b, False, False, 0)
        v.pack_start(bb, False, False, 0)
        fill()

        sw = Gtk.ComboBoxText()
        for opt, label in keyboard.SWITCH_CHOICES:
            sw.append(opt, label)
        sw.set_active_id(cfg["kb_switch"])
        sw.connect("changed", lambda w: save("kb_switch", w.get_active_id()))
        v.pack_start(row("Switch input sources with", sw), False, False, 0)

        test = Gtk.Entry()
        test.set_placeholder_text("Type here to try the keyboard")
        v.pack_start(test, False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        v.pack_start(heading("Keys"), False, False, 0)

        def switch(key, label, note=None):
            w = Gtk.Switch()
            w.set_active(bool(cfg[key]))
            w.connect("notify::active", lambda w_, p: save(key, w_.get_active()))
            v.pack_start(row(label, w), False, False, 0)
            if note:
                n = Gtk.Label(xalign=0)
                n.set_line_wrap(True)
                n.set_markup("<small>%s</small>" % note)
                n.get_style_context().add_class("dim-label")
                v.pack_start(n, False, False, 0)

        switch("kb_option_altgr", "Both ⌥ Option keys type special characters",
               "Like on a Mac. Normally only the right ⌥ Option (AltGr) key does, e.g. "
               "⌥ V = @ on a Hungarian keyboard.")
        switch("kb_swap_iso", "Swap the key above Tab and the key left of Z",
               "For Apple keyboards where these two keys (e.g. <b>0</b> and <b>í</b> on a Hungarian "
               "keyboard, <b>§</b> and <b>&lt;</b> on others) type each other’s characters.")
        switch("kb_swap_alt_win", "Use the key next to the space bar as ⌘ Command",
               "For PC keyboards: swaps Alt and the Windows key, so ⌘ sits where it is on a Mac.")
        comp = Gtk.ComboBoxText()
        for opt, label in keyboard.COMPOSE_CHOICES:
            comp.append(opt, label)
        comp.set_active_id(cfg["kb_compose"])
        comp.connect("changed", lambda w: save("kb_compose", w.get_active_id()))
        v.pack_start(row("Compose key", comp), False, False, 0)

        v.pack_start(Gtk.Separator(), False, False, 6)
        login = Gtk.Button(label="Use on the Login Screen Too…")
        login.set_halign(Gtk.Align.START)
        login.connect("clicked", lambda *_: keyboard.save_for_login_screen(cfg))
        v.pack_start(login, False, False, 0)
        lnote = Gtk.Label(xalign=0)
        lnote.set_line_wrap(True)
        lnote.set_markup("<small>Your settings apply when you log in. This also makes them the "
                         "computer’s keyboard for the login screen (asks for your password).</small>")
        lnote.get_style_context().add_class("dim-label")
        v.pack_start(lnote, False, False, 0)
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
            ("⌃ ⌘ F  /  green button", "Full screen (point at the top of the screen to leave it)"),
            ("Double-click the title bar", "Zoom the window"),
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
    ap.add_argument("--welcome", action="store_true")
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
    if args.welcome:
        WelcomeWindow()
    elif args.about:
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
        # like a Mac app: the close button closes the window, Settings keeps
        # running in the Dock and opens a fresh window when opened again
        from . import keepalive
        keepalive.keep_window(win, "org.slope.Settings", "/usr/share/applications/slope-settings.desktop")
        first_page = win.stack.get_visible_child_name()

        def on_cmd(a, cl):
            argv = cl.get_arguments()
            if not win.get_visible() and first_page:
                win.stack.set_visible_child_name(first_page)
            if "--page" in argv and argv.index("--page") + 1 < len(argv):
                child = win.stack.get_child_by_name(argv[argv.index("--page") + 1])
                if child is not None:
                    win.stack.set_visible_child(child)
            win.present()
            return 0
        app.connect("command-line", on_cmd)
    Gtk.main()
    from . import keepalive
    keepalive.unregister()
    os._exit(0)     # (Force Quit uses libwnck, which can crash during teardown)


if __name__ == "__main__":
    sys.exit(main())
