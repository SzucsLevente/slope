"""Full screen, the macOS way.

The green button (or Control-Command-F) puts a window in full screen: it
covers the whole screen, the top bar and the Dock disappear.  Moving the
pointer to the very top of the screen slides down a bar with the window's
traffic lights and title, to close it, minimize it or leave full screen.
"""
import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Wnck

from .common import spawn

BAR_H = 30
THEME = "/usr/share/themes/Slope/openbox-3/"


class FullscreenBar(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.shell = shell
        self.set_wmclass("slope-fullscreen-bar", "Slope")
        self.set_app_paintable(True)
        self.connect("draw", self._draw)
        self.target = None          # the full screen Wnck.Window
        self._poll = None
        self._at_top_since = None
        self._left_at = None

        box = Gtk.Box(spacing=8)
        box.set_border_width(5)
        self.add(box)
        for name, tip, cb in (("close", "Close", self._close),
                              ("iconify", "Minimize", self._minimize),
                              ("max", "Exit Full Screen", self._exit)):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("flat")
            b.set_tooltip_text(tip)
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(THEME + name + ".png", 14, 14)
                b.add(Gtk.Image.new_from_pixbuf(pb))
            except GLib.Error:
                b.set_label(tip)
            b.connect("clicked", lambda *_a, f=cb: f())
            box.pack_start(b, False, False, 0)
        self.title = Gtk.Label()
        self.title.set_ellipsize(3)
        box.set_center_widget(self.title)
        ex = Gtk.Button(label="Exit Full Screen")
        ex.connect("clicked", lambda *_: self._exit())
        box.pack_end(ex, False, False, 0)

        screen = Wnck.Screen.get_default()
        screen.connect("active-window-changed", lambda *_: self._watch())
        screen.connect("window-closed", lambda *_: self._watch())
        self._state_ids = {}
        self._watch()

    # -- which window is full screen ---------------------------------------------
    def _watch(self):
        screen = Wnck.Screen.get_default()
        w = screen.get_active_window()
        if w is not None and w.get_xid() not in self._state_ids:
            self._state_ids[w.get_xid()] = w.connect("state-changed", lambda *_: self._watch())
        self.target = w if (w is not None and w.is_fullscreen()) else None
        if self.target is not None:
            if self._poll is None:
                self._poll = GLib.timeout_add(120, self._check_pointer)
        else:
            if self._poll is not None:
                GLib.source_remove(self._poll)
                self._poll = None
            self.hide()

    def _check_pointer(self):
        if self.target is None:
            self._poll = None
            return False
        seat = Gdk.Display.get_default().get_default_seat()
        _s, x, y = seat.get_pointer().get_position()
        now = GLib.get_monotonic_time()
        if not self.get_visible():
            if y <= 1:
                if self._at_top_since is None:
                    self._at_top_since = now
                elif now - self._at_top_since > 350000:       # rest at the top for a moment
                    self._reveal()
            else:
                self._at_top_since = None
        else:
            if y > BAR_H + 24:
                if self._left_at is None:
                    self._left_at = now
                elif now - self._left_at > 500000:
                    self.hide()
            else:
                self._left_at = None
        return True

    def _reveal(self):
        self._at_top_since = None
        self._left_at = None
        geo = self.shell.monitor_geometry()
        self.title.set_markup("<b>%s</b>" % GLib.markup_escape_text(self.target.get_name() or ""))
        self.move(geo.x, geo.y)
        self.resize(geo.width, BAR_H)
        self.show_all()

    # -- actions -------------------------------------------------------------------
    def _exit(self):
        if self.target is not None:
            self.target.set_fullscreen(False)
        self.hide()

    def _minimize(self):
        if self.target is not None:
            self.target.minimize()
        self.hide()

    def _close(self):
        if self.target is not None:
            spawn(["slope-ctl", "close-window", str(self.target.get_xid())])
        self.hide()

    def _draw(self, w, cr):
        a = self.get_allocation()
        g = cairo.LinearGradient(0, 0, 0, a.height)
        g.add_color_stop_rgb(0, 0.97, 0.97, 0.98)
        g.add_color_stop_rgb(1, 0.86, 0.87, 0.89)
        cr.set_source(g)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.rectangle(0, a.height - 1, a.width, 1)
        cr.fill()
        return False
