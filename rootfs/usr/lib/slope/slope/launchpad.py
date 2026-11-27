"""slope-launchpad: a full-screen, glassy grid of every installed app.

Open it from the Launchpad icon in the Dock, with F4 or Super+A.  Type to
search, click an app to open it, right-click for "Keep in Dock".  Esc or a
click on the background closes it.  Running it again while it is open
closes it too.

It stays loaded after the first time (the session also starts it with
--background), so it opens instantly.  Opening and closing are animated on
the window's frame clock: the frosted copy of the screen fades in while the
icons settle from a little larger, and closing plays it backwards until the
window is fully transparent, only then is it hidden.
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

ICON = 88
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
  background-image: linear-gradient(to bottom, alpha(mix(@slope_accent, #ffffff, 0.47), 0.55), alpha(@slope_accent, 0.40));
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.5), 0 0 12px alpha(mix(@slope_accent, #ffffff, 0.17), 0.5);
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
.slope-launchpad button.page-dot {
  min-width: 9px; min-height: 9px; padding: 0; margin: 0 5px;
  border-radius: 999px; border: none; box-shadow: none;
  background-image: none; background-color: rgba(255, 255, 255, 0.32);
  transition: background-color 200ms ease-out;
}
.slope-launchpad button.page-dot:hover { background-color: rgba(255, 255, 255, 0.6); }
.slope-launchpad button.page-dot.current { background-color: rgba(255, 255, 255, 0.95); }
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


def frosted_screen(geo):
    """A blurred, slightly darkened copy of what is on the screen now
    (like macOS Launchpad); the wallpaper when the screen can't be read."""
    w, h = geo.width, geo.height
    try:
        shot = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), geo.x, geo.y, w, h)
    except Exception:
        shot = None
    if shot is None:
        return blurred_wallpaper(w, h)
    # shrink in two steps (smoother than one big jump), then scale back up
    mid = shot.scale_simple(max(8, w // 6), max(8, h // 6), GdkPixbuf.InterpType.BILINEAR)
    small_w, small_h = max(8, w // 28), max(8, h // 28)
    tiny = mid.scale_simple(small_w, small_h, GdkPixbuf.InterpType.BILINEAR)
    small = cairo.ImageSurface(cairo.FORMAT_RGB24, small_w, small_h)
    cr = cairo.Context(small)
    Gdk.cairo_set_source_pixbuf(cr, tiny, 0, 0)
    cr.paint()
    big = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    c2 = cairo.Context(big)
    c2.scale(w / float(small_w), h / float(small_h))
    c2.set_source_surface(small, 0, 0)
    c2.get_source().set_filter(cairo.FILTER_BILINEAR)
    c2.get_source().set_extend(cairo.EXTEND_PAD)
    c2.paint()
    c2.identity_matrix()
    g = cairo.LinearGradient(0, 0, 0, h)
    g.add_color_stop_rgba(0, 0.02, 0.04, 0.12, 0.42)
    g.add_color_stop_rgba(1, 0.02, 0.04, 0.12, 0.30)
    c2.set_source(g)
    c2.paint()
    return big


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in(t):
    return t * t * t


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
        self.bg = None
        self.shown = False
        self._closing = False
        self._anim = None
        self._dirty = False
        Gio.AppInfoMonitor.get().connect("changed", lambda *_: setattr(self, "_dirty", True))

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
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("activate", self._activate_first)
        outer.pack_start(self.search, False, False, 0)

        # pages of apps, like on a Mac: swipe, scroll or use the arrow keys
        self.cols = max(4, min(8, (self.geo.width - 2 * side) // 160))
        usable = self.geo.height - int(self.geo.height * 0.06) - 24 - 30 - 40 - 30 - 18 * 3
        self.rows = max(2, min(6, usable // 136))
        self.pages = Gtk.Stack()
        self.pages.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.pages.set_transition_duration(380)
        self.pages.set_vexpand(True)
        outer.pack_start(self.pages, True, True, 0)
        self.flows = []
        self.page = 0
        self.results = self._new_flow()
        self.results.set_filter_func(self._filter)
        # search results can be any length: they scroll (and must not make
        # the pages area taller than the screen)
        self.results_sw = Gtk.ScrolledWindow()
        self.results_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.results_sw.set_overlay_scrolling(True)
        self.results_sw.add(self.results)
        self.pages.add_named(self.results_sw, "results")
        self.dots = Gtk.Box()
        self.dots.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self.dots, False, False, 0)
        self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("scroll-event", self._scroll)
        self._scroll_acc = 0.0
        self._scroll_lock = 0

        hint = Gtk.Label(label="Click an app to open it  ·  Swipe or scroll for more  ·  Right-click → Keep in Dock  ·  Esc to close")
        hint.get_style_context().add_class("hint")
        outer.pack_start(hint, False, False, 0)

        self._populate()
        self._focused = False
        self.connect("focus-in-event", lambda *_: setattr(self, "_focused", True))
        self.connect("delete-event", lambda *_: (self.close_launchpad(), True)[1])

    # -- open / close ----------------------------------------------------------
    def toggle(self):
        if self.shown and not self._closing:
            self.close_launchpad()
        else:
            self.open_launchpad()

    def open_launchpad(self):
        if self.shown and not self._closing:
            return
        if not self.shown:
            if self._dirty or load_settings().get("new_apps", []) != self.new_apps:
                self._populate()
                self._dirty = False
            self.bg = frosted_screen(self.geo)          # before we cover the screen
            self.search.set_text("")
            for f in self._all_flows():
                f.unselect_all()
            self._show_page(0, animate=False)
            self.set_opacity(0.0)
            self.get_style_context().add_class("zoom")
            self.fullscreen()
            self.show_all()
            self.present()
            self.search.grab_focus()
            self._focused = False
            # the icons settle from a little larger while the window fades in
            GLib.timeout_add(30, lambda: self.get_style_context().remove_class("zoom") or False)
        self.shown = True
        self._closing = False
        self._animate(1.0, 0.26, ease_out)

    def close_launchpad(self):
        if not self.shown or self._closing:
            return
        self._closing = True
        self.get_style_context().add_class("zoom")     # icons grow and fade away

        def done():
            # fully transparent before it goes: nothing for the compositor to flash
            self.hide()
            self.shown = False
            self._closing = False
        self._animate(0.0, 0.22, ease_in, done)

    def _animate(self, target, duration, curve, done=None):
        """Window opacity to target, on the frame clock (one step per refresh)."""
        if self._anim is not None:
            self.remove_tick_callback(self._anim)
        start_val = self.get_opacity()
        state = {"t0": None}

        def tick(_w, clock):
            now = clock.get_frame_time() / 1e6
            if state["t0"] is None:
                state["t0"] = now
            p = min(1.0, (now - state["t0"]) / duration)
            self.set_opacity(start_val + (target - start_val) * curve(p))
            if p < 1.0:
                return True
            self._anim = None
            if done:
                done()
            return False
        self._anim = self.add_tick_callback(tick)

    # -- content -----------------------------------------------------------
    def _new_flow(self):
        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(self.cols)
        flow.set_max_children_per_line(self.cols)
        flow.set_row_spacing(14)
        flow.set_column_spacing(14)
        flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flow.set_activate_on_single_click(True)
        flow.connect("child-activated", lambda f, c: self._open(c.appinfo))
        flow.connect("button-press-event", self._flow_press)
        return flow

    @property
    def flow(self):
        """The grid on screen: the search results, or the current page."""
        if self.search.get_text().strip() or not self.flows:
            return self.results
        return self.flows[min(self.page, len(self.flows) - 1)]

    def _all_flows(self):
        return self.flows + [self.results]

    def _clear(self):
        for f in self.flows:
            self.pages.remove(f)
        self.flows = []
        for c in self.results.get_children():
            self.results.remove(c)

    def _populate(self):
        self.new_apps = list(load_settings().get("new_apps", []))
        self._clear()
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
            lbl = Gtk.Label()
            if a.get_id() in self.new_apps:
                # newly installed: a blue dot until it is opened, like on a Mac
                lbl.set_markup("<span foreground='#59b4ff'>●</span> " + GLib.markup_escape_text(friendly_name(a)))
            else:
                lbl.set_text(friendly_name(a))
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(14)
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.get_style_context().add_class("app")
            box.pack_start(lbl, False, False, 0)
            child.add(box)
            child.set_tooltip_text(a.get_description() or a.get_name())
            per_page = self.cols * self.rows
            n = sum(len(f.get_children()) for f in self.flows)
            if n % per_page == 0:
                f = self._new_flow()
                self.flows.append(f)
                self.pages.add_named(f, "page%d" % len(self.flows))
                f.show_all()
            self.flows[-1].add(child)
            # the same app in the search results grid
            twin = Gtk.FlowBoxChild()
            twin.appinfo = a
            twin.search = child.search
            tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            tbox.pack_start(Gtk.Image.new_from_pixbuf(img.get_pixbuf()), False, False, 0)
            tl = Gtk.Label()
            tl.set_markup(lbl.get_label()) if a.get_id() in self.new_apps else tl.set_text(lbl.get_text())
            tl.set_ellipsize(Pango.EllipsizeMode.END)
            tl.set_max_width_chars(14)
            tl.get_style_context().add_class("app")
            tbox.pack_start(tl, False, False, 0)
            twin.add(tbox)
            self.results.add(twin)
        self._show_page(0, animate=False)

    def _filter(self, child):
        q = self.search.get_text().strip().lower()
        return not q or all(part in child.search for part in q.split())

    def _visible_children(self):
        if self.flow is self.results:
            return [c for c in self.results.get_children() if c.get_child_visible() and self._filter(c)]
        return list(self.flow.get_children())

    # -- pages -------------------------------------------------------------------
    def _rebuild_dots(self):
        for c in self.dots.get_children():
            self.dots.remove(c)
        if self.search.get_text().strip() or len(self.flows) < 2:
            return
        for i in range(len(self.flows)):
            b = Gtk.Button()
            b.get_style_context().add_class("page-dot")
            if i == self.page:
                b.get_style_context().add_class("current")
            b.connect("clicked", lambda _b, k=i: self._show_page(k))
            self.dots.pack_start(b, False, False, 0)
        self.dots.show_all()

    def _show_page(self, k, animate=True):
        if not self.flows:
            return
        k = max(0, min(len(self.flows) - 1, k))
        if not animate:
            self.pages.set_transition_type(Gtk.StackTransitionType.NONE)
        elif k > self.page:
            self.pages.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        else:
            self.pages.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.page = k
        self.pages.set_visible_child(self.flows[k])
        self._rebuild_dots()

    def _search_changed(self):
        if self.search.get_text().strip():
            self.results.invalidate_filter()
            self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.pages.set_visible_child(self.results_sw)
            self._rebuild_dots()
        else:
            self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.pages.set_visible_child(self.flows[self.page] if self.flows else self.results_sw)
            self._rebuild_dots()

    def _scroll(self, w, ev):
        """Two-finger swipes, the mouse wheel: next / previous page."""
        if self.search.get_text().strip():
            return False
        now = GLib.get_monotonic_time()
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            _ok, dx, dy = ev.get_scroll_deltas()
            self._scroll_acc += dx if abs(dx) > abs(dy) else dy
            if now < self._scroll_lock:
                self._scroll_acc = 0.0
                return True
            if abs(self._scroll_acc) > 1.6:
                self._show_page(self.page + (1 if self._scroll_acc > 0 else -1))
                self._scroll_acc = 0.0
                self._scroll_lock = now + 450000      # one page per swipe
            return True
        if now < self._scroll_lock:
            return True
        if ev.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            self._show_page(self.page + 1)
        elif ev.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            self._show_page(self.page - 1)
        self._scroll_lock = now + 250000
        return True

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
        if appinfo.get_id() in self.new_apps:
            update_setting("new_apps", [i for i in self.new_apps if i != appinfo.get_id()])
        self.close_launchpad()

    # -- events ------------------------------------------------------------
    def _key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close_launchpad()
            return True
        if not self.search.get_text().strip() and ev.keyval in (Gdk.KEY_Left, Gdk.KEY_Right,
                                                               Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            forward = ev.keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down)
            self._show_page(self.page + (1 if forward else -1))
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

    def _draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        if self.bg is not None:
            cr.set_source_surface(self.bg, 0, 0)
        else:
            cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False


def main():
    background = "--background" in sys.argv[1:]
    GLib.set_prgname("slope-launchpad")
    app = Gio.Application(application_id="org.slope.Launchpad", flags=Gio.ApplicationFlags.FLAGS_NONE)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        if not background:
            app.activate()      # opens the loaded Launchpad, or closes it when open
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
    app.connect("activate", lambda *_: win.toggle())
    if not background:
        win.open_launchpad()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
