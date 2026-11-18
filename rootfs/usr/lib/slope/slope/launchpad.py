"""slope-launchpad: a full-screen, glassy grid of every installed app.

Open it from the Launchpad icon in the Dock, with F4 or Super+A.  Type to
search, click an app to open it, right-click for "Keep in Dock".  Esc or a
click on the background closes it.  Running it again while it is open
closes it too.
"""
import os
import sys

import cairo
import gi

from . import art
from .common import (SHARE, desktop_key, friendly_name, icon_for, launch, load_settings,
                     update_setting)

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

ICON = 80
HIDDEN = {"slope-launchpad", "slope-spotlight"}

CSS = b"""
.slope-launchpad { background: transparent; }
.slope-launchpad entry {
  min-height: 30px;
  min-width: 260px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.45);
  color: #ffffff;
  caret-color: #ffffff;
  background-color: rgba(255, 255, 255, 0.18);
  background-image: linear-gradient(to bottom, rgba(255,255,255,0.28), rgba(255,255,255,0.08));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.5), 0 2px 8px rgba(0, 0, 0, 0.25);
}
.slope-launchpad entry image { color: rgba(255, 255, 255, 0.85); }
.slope-launchpad scrolledwindow, .slope-launchpad viewport,
.slope-launchpad flowbox { background: transparent; border: none; }
.slope-launchpad flowboxchild {
  padding: 10px 6px;
  border-radius: 18px;
  background: transparent;
  outline: none;
}
.slope-launchpad flowboxchild:hover {
  background-image: linear-gradient(to bottom, rgba(255,255,255,0.22), rgba(255,255,255,0.06));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.35);
}
.slope-launchpad flowboxchild:selected {
  background-image: linear-gradient(to bottom, rgba(140,198,255,0.55), rgba(47,134,243,0.40));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.5), 0 0 12px rgba(80, 160, 255, 0.5);
}
.slope-launchpad flowboxchild image {
  transition: -gtk-icon-transform 320ms cubic-bezier(0.2, 0.8, 0.2, 1);
}
.slope-launchpad flowboxchild {
  transition: opacity 260ms ease-out, background-image 150ms ease-out;
}
/* opening: icons start large and transparent, then settle (like macOS Launchpad) */
.slope-launchpad.zoom flowboxchild image { -gtk-icon-transform: scale(1.3); }
.slope-launchpad.zoom flowboxchild { opacity: 0; }
.slope-launchpad label.app {
  color: #ffffff;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.75);
  font-size: 10.5pt;
}
.slope-launchpad label.hint {
  color: rgba(255, 255, 255, 0.7);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
  font-size: 9pt;
}
"""


def blurred_wallpaper(w, h):
    """Wallpaper drawn tiny and scaled up: a cheap, pretty frosted blur."""
    small_w, small_h = max(8, w // 24), max(8, h // 24)
    small = cairo.ImageSurface(cairo.FORMAT_RGB24, small_w, small_h)
    cr = cairo.Context(small)
    wp = load_settings().get("wallpaper", "slope:aqua")
    drawn = False
    if not wp.startswith("slope:"):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(wp, small_w * 2, small_h * 2, False)
            cr.scale(small_w / pb.get_width(), small_h / pb.get_height())
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
            drawn = True
        except GLib.Error:
            pass
    if not drawn:
        cr.scale(small_w / float(w), small_h / float(h))
        art.draw_wallpaper(cr, w, h, wp.split(":", 1)[1] if wp.startswith("slope:") else "aqua")
    big = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c2 = cairo.Context(big)
    c2.scale(w / float(small_w), h / float(small_h))
    c2.set_source_surface(small, 0, 0)
    c2.get_source().set_filter(cairo.FILTER_BILINEAR)
    c2.get_source().set_extend(cairo.EXTEND_PAD)
    c2.paint()
    c2.identity_matrix()
    g = cairo.LinearGradient(0, 0, 0, h)
    g.add_color_stop_rgba(0, 0.02, 0.04, 0.12, 0.45)
    g.add_color_stop_rgba(1, 0.02, 0.04, 0.12, 0.25)
    c2.set_source(g)
    c2.paint()
    return big


class Launchpad(Gtk.Window):
    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.set_wmclass("slope-launchpad", "Slope")
        self.set_title("slope-launchpad")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis is not None:
            self.set_visual(vis)
        self.get_style_context().add_class("slope-launchpad")

        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        self.geo = mon.get_geometry()
        self.move(self.geo.x, self.geo.y)
        self.set_default_size(self.geo.width, self.geo.height)
        self.bg = blurred_wallpaper(self.geo.width, self.geo.height)

        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("focus-out-event", lambda *_: GLib.timeout_add(200, self._maybe_close))
        self.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.connect("button-release-event", self._background_click)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        outer.set_margin_top(int(self.geo.height * 0.06))
        outer.set_margin_bottom(24)
        side = max(40, int(self.geo.width * 0.08))
        outer.set_margin_start(side)
        outer.set_margin_end(side)
        self.add(outer)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search")
        self.search.set_halign(Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self.flow.invalidate_filter())
        self.search.connect("activate", self._activate_first)
        outer.pack_start(self.search, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_overlay_scrolling(True)
        outer.pack_start(sw, True, True, 0)
        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_homogeneous(True)
        cols = max(4, min(8, (self.geo.width - 2 * side) // 150))
        self.flow.set_min_children_per_line(cols)
        self.flow.set_max_children_per_line(cols)
        self.flow.set_row_spacing(14)
        self.flow.set_column_spacing(14)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_activate_on_single_click(True)
        self.flow.set_filter_func(self._filter)
        self.flow.connect("child-activated", lambda f, c: self._open(c.appinfo))
        self.flow.connect("button-press-event", self._flow_press)
        sw.add(self.flow)

        hint = Gtk.Label(label="Click an app to open it  ·  Right-click → Keep in Dock  ·  Esc to close")
        hint.get_style_context().add_class("hint")
        outer.pack_start(hint, False, False, 0)

        self._populate()
        self.show_all()
        self.fullscreen()
        self.present()
        self.search.grab_focus()
        self._focused = False
        self.connect("focus-in-event", lambda *_: setattr(self, "_focused", True))
        self.set_opacity(0.0)
        self.get_style_context().add_class("zoom")
        GLib.timeout_add(16, self._fade_in)
        GLib.timeout_add(40, lambda: self.get_style_context().remove_class("zoom") or False)

    # -- content -----------------------------------------------------------
    def _populate(self):
        apps = []
        for a in Gio.AppInfo.get_all():
            if not isinstance(a, Gio.DesktopAppInfo) or not a.should_show():
                continue
            if desktop_key(a.get_id()) in HIDDEN:
                continue
            apps.append((friendly_name(a).lower(), a))
        apps.sort(key=lambda t: t[0])
        theme = Gtk.IconTheme.get_default()
        for _, a in apps:
            child = Gtk.FlowBoxChild()
            child.appinfo = a
            child.search = " ".join(filter(None, [friendly_name(a), a.get_name(), a.get_generic_name(),
                                                  " ".join(a.get_keywords() or []),
                                                  a.get_executable()])).lower()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            img = Gtk.Image.new_from_pixbuf(icon_for(a, ICON))
            box.pack_start(img, False, False, 0)
            lbl = Gtk.Label(label=friendly_name(a))
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(14)
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.get_style_context().add_class("app")
            box.pack_start(lbl, False, False, 0)
            child.add(box)
            child.set_tooltip_text(a.get_description() or a.get_name())
            self.flow.add(child)

    def _filter(self, child):
        q = self.search.get_text().strip().lower()
        return not q or all(part in child.search for part in q.split())

    def _visible_children(self):
        return [c for c in self.flow.get_children() if c.get_child_visible() and self._filter(c)]

    def _activate_first(self, *_):
        sel = self.flow.get_selected_children()
        target = sel[0] if sel and self._filter(sel[0]) else None
        if target is None:
            vis = self._visible_children()
            target = vis[0] if vis else None
        if target is not None:
            self._open(target.appinfo)

    def _open(self, appinfo):
        launch(appinfo)
        self.close_launchpad()

    # -- events ------------------------------------------------------------
    def _key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close_launchpad()
            return True
        if ev.keyval in (Gdk.KEY_Down, Gdk.KEY_Up, Gdk.KEY_Left, Gdk.KEY_Right) and self.search.has_focus():
            vis = self._visible_children()
            if vis:
                self.flow.select_child(vis[0])
                vis[0].grab_focus()
            return True
        return False

    def _flow_press(self, flow, ev):
        child = flow.get_child_at_pos(int(ev.x), int(ev.y))
        if child is None:
            if ev.button == 1:
                self.close_launchpad()
            return True
        if ev.button == 3:
            self._menu(child, ev)
            return True
        return False

    def _background_click(self, w, ev):
        # only clicks on the bare background (not the search field or an app)
        if ev.button == 1 and Gtk.get_event_widget(ev) is self:
            self.close_launchpad()
        return False

    def _menu(self, child, ev):
        a = child.appinfo
        ids = load_settings().get("dock_apps", [])
        pinned = any(desktop_key(i) == desktop_key(a.get_id()) for i in ids)
        menu = Gtk.Menu()
        op = Gtk.MenuItem(label="Open")
        op.connect("activate", lambda *_: self._open(a))
        menu.append(op)
        menu.append(Gtk.SeparatorMenuItem())
        keep = Gtk.CheckMenuItem(label="Keep in Dock")
        keep.set_active(pinned)

        def toggle(m):
            cur = load_settings().get("dock_apps", [])
            if m.get_active():
                if a.get_id() not in cur:
                    cur.append(a.get_id())
            else:
                cur = [i for i in cur if desktop_key(i) != desktop_key(a.get_id())]
            update_setting("dock_apps", cur)
        keep.connect("toggled", toggle)
        menu.append(keep)
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(ev)

    def _maybe_close(self):
        # only close on focus loss if no popup menu of ours is open
        if self._focused and not self.is_active() and not Gtk.grab_get_current():
            self.close_launchpad()
        return False

    def _fade_in(self):
        o = min(1.0, self.get_opacity() + 0.2)
        self.set_opacity(o)
        return o < 1.0

    def close_launchpad(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True

        def step():
            o = self.get_opacity() - 0.25
            if o <= 0:
                Gtk.main_quit()
                return False
            self.set_opacity(o)
            return True
        GLib.timeout_add(16, step)

    def _draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(self.bg, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False


def main():
    GLib.set_prgname("slope-launchpad")
    app = Gio.Application(application_id="org.slope.Launchpad", flags=Gio.ApplicationFlags.FLAGS_NONE)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        app.activate()          # the open Launchpad closes itself
        return 0
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    shell_css = os.path.join(SHARE, "shell.css")
    if os.path.exists(shell_css):
        p2 = Gtk.CssProvider()
        try:
            p2.load_from_path(shell_css)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), p2,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass
    win = Launchpad(app)
    app.connect("activate", lambda *_: win.close_launchpad())
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
