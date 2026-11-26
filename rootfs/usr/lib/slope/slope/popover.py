"""Glassy drop-down panels for the top bar (Sound, Clock, ...).

GTK menus are bad at hosting sliders and calendars (the menu keeps the
pointer grab, so dragging a slider does nothing).  A popover is a small
override-redirect window with its own pointer grab: clicks inside go to its
widgets, a click anywhere else (or Esc) closes it.
"""
import math

import cairo
from gi.repository import Gdk, GLib, Gtk

from . import art

SHADOW = 14     # transparent margin around the glass for the drop shadow
RADIUS = 12


class Popover(Gtk.Window):
    def __init__(self, width=300):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_wmclass("slope-popover", "Slope")
        self.set_title("slope-popover")
        self.set_app_paintable(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        vis = self.get_screen().get_rgba_visual()
        self._rgba = vis is not None and self.get_screen().is_composited()
        if vis is not None:
            self.set_visual(vis)
        self.get_style_context().add_class("slope-popover")
        self.width = width
        self.connect("draw", self._draw)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("button-press-event", self._press)
        self.connect("key-press-event", self._key)
        self.connect("grab-broken-event", lambda *_: self.dismiss())
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_border_width(SHADOW)
        self.add(outer)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content.set_border_width(14)
        self.content.set_size_request(width, -1)
        outer.pack_start(self.content, True, True, 0)
        self._seat = None
        self.on_show = None
        self.opened_at = 0

    # -- show / hide --------------------------------------------------------
    def popup(self, anchor_x, top):
        """Show centred under anchor_x (screen coordinates), at y = top."""
        if self.on_show:
            self.on_show()
        self.content.show_all()
        self.get_child().show_all()
        _, nat = self.get_preferred_size()
        w, h = max(nat.width, self.width + 2 * SHADOW), nat.height
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        geo = mon.get_geometry()
        x = int(anchor_x - w / 2)
        x = max(geo.x - SHADOW + 4, min(geo.x + geo.width - w + SHADOW - 4, x))
        self.move(x, int(top - SHADOW + 4))
        self.resize(w, h)
        self.set_opacity(0.0)
        self.show_all()
        self.present()
        self.opened_at = GLib.get_monotonic_time()
        GLib.timeout_add(16, self._fade_in)
        GLib.idle_add(self._grab)

    def _fade_in(self):
        o = min(1.0, self.get_opacity() + 0.25)
        self.set_opacity(o)
        return o < 1.0

    def _grab(self):
        gw = self.get_window()
        if gw is None:
            return False
        self._seat = Gdk.Display.get_default().get_default_seat()
        status = self._seat.grab(gw, Gdk.SeatCapabilities.ALL, True, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            # try again shortly: the click that opened us may still hold a grab
            GLib.timeout_add(60, self._grab)
        return False

    def dismiss(self):
        if self._seat is not None:
            self._seat.ungrab()
            self._seat = None
        self.hide()

    def toggle(self, anchor_x, top):
        if self.get_visible():
            self.dismiss()
        else:
            self.popup(anchor_x, top)

    def _press(self, w, ev):
        a = self.get_allocation()
        inside = (SHADOW <= ev.x <= a.width - SHADOW and SHADOW <= ev.y <= a.height - SHADOW)
        if not inside and ev.window == self.get_window():
            self.dismiss()
            return True
        if ev.window != self.get_window() and not self._is_ours(ev.window):
            self.dismiss()
            return True
        return False

    def _is_ours(self, gdkwin):
        while gdkwin is not None:
            if gdkwin == self.get_window():
                return True
            gdkwin = gdkwin.get_parent()
        return False

    def _key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    # -- drawing ------------------------------------------------------------
    def _draw(self, w, cr):
        a = self.get_allocation()
        self._shape(a)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        x, y, ww, hh = SHADOW, SHADOW, a.width - 2 * SHADOW, a.height - 2 * SHADOW
        if self._rgba:
            for i in range(SHADOW, 0, -2):
                art.rounded_rect(cr, x - i + 2, y - i + 6, ww + 2 * i - 4, hh + 2 * i - 6, RADIUS + i)
                cr.set_source_rgba(0, 0, 0, 0.018)
                cr.fill()
        art.rounded_rect(cr, x, y, ww, hh, RADIUS)
        g = cairo.LinearGradient(0, y, 0, y + hh)
        g.add_color_stop_rgba(0, 0.985, 0.99, 1.0, 0.96)
        g.add_color_stop_rgba(1, 0.93, 0.945, 0.97, 0.95)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.set_line_width(1)
        cr.stroke()
        art.rounded_rect(cr, x + 2, y + 1.5, ww - 4, 26, RADIUS - 2)
        hg = cairo.LinearGradient(0, y, 0, y + 28)
        hg.add_color_stop_rgba(0, 1, 1, 1, 0.9)
        hg.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(hg)
        cr.fill()
        return False


    def _shape(self, a):
        """Without a compositor, cut the window to the glass (no black margin)."""
        key = None
        if not self.get_screen().is_composited():
            key = (a.width, a.height)
        if key == getattr(self, "_shape_key", None):
            return
        self._shape_key = key
        if key is None:
            self.shape_combine_region(None)
            return
        x, y, w, h, r = SHADOW, SHADOW, a.width - 2 * SHADOW, a.height - 2 * SHADOW, RADIUS
        region = cairo.Region()
        for dy in range(0, h, 2):
            e = min(dy + 1, h - dy - 1)
            inset = r - math.sqrt(max(0.0, r * r - (r - e) ** 2)) if e < r else 0
            region.union(cairo.RectangleInt(int(x + inset), y + dy, int(w - 2 * inset), min(2, h - dy)))
        self.shape_combine_region(region)


def heading(text):
    lbl = Gtk.Label(xalign=0)
    lbl.set_markup("<b>%s</b>" % GLib.markup_escape_text(text))
    return lbl


def link_button(text, cb):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class("flat")
    b.get_style_context().add_class("slope-popover-link")
    b.set_halign(Gtk.Align.START)
    b.connect("clicked", lambda *_: cb())
    return b
