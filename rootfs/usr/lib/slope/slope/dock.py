"""The Slope dock: a glossy glass shelf with magnifying icons."""
import math
import os
import time

import cairo
import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import art  # noqa: E402
from .common import icon_for, spawn  # noqa: E402

TRASH_DIR = os.path.join(GLib.get_user_data_dir(), "Trash", "files")


class Slot:
    __slots__ = ("kind", "group", "x", "size", "width", "grow", "target")

    def __init__(self, kind, group=None, grow=1.0):
        self.kind = kind        # "app", "sep", "trash", "launchpad"
        self.group = group
        self.x = 0.0
        self.size = 0.0
        self.width = 0.0
        self.grow = grow        # 0..1: icons pop in when apps start, shrink away when they quit
        self.target = 1.0

    @property
    def key(self):
        return self.group.key if self.group is not None else self.kind


class Dock(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.shell = shell
        self.apps = shell.apps
        self.set_wmclass("slope-dock", "Slope")
        self.set_title("slope-dock")
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.stick()
        self.set_accept_focus(False)
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis is not None:
            self.set_visual(vis)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK |
                        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect("draw", self._draw)
        self.connect("motion-notify-event", self._motion)
        self.connect("leave-notify-event", self._leave)
        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release)
        self.connect("realize", lambda *_: self._update_input_shape())
        Gtk.IconTheme.get_default().connect("changed", lambda *_: (self.icons.clear(), self.queue_draw()))

        self.slots = []
        self.icons = {}
        self.mouse_x = None
        self.hover = False
        self.mag = 0.0
        self.pressed = None
        self.menu_open = False
        self.bounces = {}
        self._anim_id = None
        self._trash_full = False
        self._label_key = None
        self._label_start = 0.0

        self.apps.connect("changed", lambda *_: self.refresh())
        self.apps.connect("app-launched", self._on_launched)
        GLib.timeout_add_seconds(3, self._poll_trash)
        self._poll_trash()
        self.configure()

    # -- configuration ----------------------------------------------------
    def configure(self):
        s = self.shell.settings
        self.S = int(max(24, min(128, s.get("dock_icon_size", 52))))
        self.M = int(max(self.S, min(160, s.get("dock_magnify_size", 88)))) if s.get("dock_magnify", True) else self.S
        self.style = s.get("dock_style", "shelf")
        self.gap = round(self.S * 0.16)
        self.label_h = 34
        if self.style == "shelf":
            self.shelf_h = round(self.S * 0.40)
            self.below = round(self.shelf_h * 0.5) + 3          # baseline -> bottom
        else:
            self.pad = round(self.S * 0.14)
            self.below = self.pad + 6
        self.icons.clear()
        self.refresh()

    def refresh(self):
        first = not self.slots
        old = {sl.key: (i, sl) for i, sl in enumerate(self.slots) if sl.kind == "app"}
        slots = [Slot("launchpad")]
        for g in self.apps.groups():
            prev = old.pop(g.key, None)
            if prev is None:
                sl = Slot("app", g, grow=1.0 if first else 0.0)
            else:
                sl = prev[1]
                sl.group = g
            sl.target = 1.0
            slots.append(sl)
        # apps that went away shrink out of the Dock where they were
        for key, (idx, sl) in sorted(old.items(), key=lambda kv: kv[1][0]):
            if sl.grow <= 0.02:
                continue
            sl.target = 0.0
            slots.insert(min(idx, len(slots)), sl)
        slots.append(Slot("sep"))
        slots.append(Slot("trash"))
        self.slots = slots
        self.place()
        if any(abs(sl.grow - sl.target) > 0.01 for sl in slots):
            self._kick()
        self.queue_draw()

    def base_width(self):
        w = 0
        for sl in self.slots:
            w += self.gap * 2 if sl.kind == "sep" else self.S + self.gap
        return w

    def place(self):
        geo = self.shell.monitor_geometry()
        self.H = self.label_h + max(self.M, int(self.S * 1.5)) + self.below
        self.W = min(geo.width, int(self.base_width() + 2 * (self.M - self.S) * 1.9 + self.S * 2 + 40))
        self.geo = geo
        x = geo.x + (geo.width - self.W) // 2
        y = geo.y + geo.height - self.H
        self.set_size_request(self.W, self.H)
        self.resize(self.W, self.H)
        self.move(x, y)
        self.show()
        reserve = self.below + self.S + 4
        self.shell.set_strut(self, bottom=reserve, bottom_start=x, bottom_end=x + self.W - 1)
        self._update_input_shape()

    # -- layout -------------------------------------------------------------
    def _layout(self):
        S, M, gap = self.S, self.M, self.gap
        base_total = sum(gap * 2 if sl.kind == "sep" else (S + gap) * sl.grow for sl in self.slots)
        bx = (self.W - base_total) / 2.0
        R = S * 2.4
        total = 0.0
        for sl in self.slots:
            if sl.kind == "sep":
                sl.size, sl.width = 0, gap * 2
                bx += sl.width
                total += sl.width
                continue
            c = bx + (S + gap) / 2.0
            f = 0.0
            if self.mouse_x is not None and self.mag > 0:
                d = abs(self.mouse_x - c)
                if d < R:
                    f = (math.cos(math.pi * d / R) + 1) / 2.0
            sl.size = (S + (M - S) * f * self.mag) * sl.grow
            sl.width = sl.size + gap * sl.grow
            bx += (S + gap) * sl.grow
            total += sl.width
        x = (self.W - total) / 2.0
        for sl in self.slots:
            sl.x = x
            x += sl.width
        return (self.W - total) / 2.0, total

    def _slot_at(self, x):
        for sl in self.slots:
            if sl.kind != "sep" and sl.target > 0 and sl.x <= x < sl.x + sl.width:
                return sl
        return None

    def _update_input_shape(self):
        if self.get_window() is None:
            return
        if self.hover or self.menu_open:
            rect = cairo.RectangleInt(0, 0, self.W, self.H)
        else:
            content = self.base_width()
            x0 = int((self.W - content) / 2 - self.S * 0.4)
            top = self.H - self.below - self.S - 2
            rect = cairo.RectangleInt(max(0, x0), top, int(min(self.W, content + self.S * 0.8)), self.H - top)
        self.input_shape_combine_region(cairo.Region(rect))

    # -- animation ----------------------------------------------------------
    def _kick(self):
        if self._anim_id is None:
            self._anim_id = GLib.timeout_add(16, self._anim)

    def _anim(self):
        target = 1.0 if self.hover else 0.0
        self.mag += (target - self.mag) * 0.3
        if abs(target - self.mag) < 0.01:
            self.mag = target
        now = time.monotonic()
        for k, (start, settle) in list(self.bounces.items()):
            if now - start > 8 or (settle and now >= settle):
                del self.bounces[k]
        growing = False
        for sl in self.slots:
            if sl.grow != sl.target:
                sl.grow += (sl.target - sl.grow) * 0.22
                if abs(sl.grow - sl.target) < 0.01:
                    sl.grow = sl.target
                else:
                    growing = True
        if any(sl.target == 0 and sl.grow == 0 for sl in self.slots):
            self.slots = [sl for sl in self.slots if not (sl.target == 0 and sl.grow == 0)]
            self.place()
        fading = self.hover and self._label_alpha() < 1.0
        self.queue_draw()
        if self.mag == target and not self.bounces and not growing and not fading:
            self._anim_id = None
            if not self.hover:
                self.mouse_x = None
            return False
        return True

    def _label_alpha(self):
        return min(1.0, (time.monotonic() - self._label_start) / 0.15)

    def _on_launched(self, apps, key):
        period = 0.62
        self.bounces[key] = (time.monotonic(), None)
        self._kick()

        def check():
            if key not in self.bounces:
                return False
            g = self.apps.group_by_key(key)
            if g is not None and g.running:
                start, _ = self.bounces[key]
                el = time.monotonic() - start
                end = start + (math.floor(el / period) + 1) * period
                self.bounces[key] = (start, end)
                return False
            return True
        GLib.timeout_add(150, check)

    def _bounce_offset(self, key):
        b = self.bounces.get(key)
        if not b:
            return 0.0
        el = time.monotonic() - b[0]
        return abs(math.sin(el * math.pi / 0.62)) * self.S * 0.45

    # -- events -------------------------------------------------------------
    def _motion(self, w, ev):
        top_limit = self.H - self.below - (self.M if self.hover else self.S) - 6
        x0 = (self.W - self.base_width()) / 2 - self.S * 0.5
        x1 = self.W - x0
        inside = ev.y >= top_limit and x0 <= ev.x <= x1
        if inside != self.hover:
            self.hover = inside
            self._update_input_shape()
        self.mouse_x = ev.x if inside else self.mouse_x
        self._kick()
        return True

    def _leave(self, w, ev):
        if self.menu_open:
            return False
        self.hover = False
        self.pressed = None
        self._label_key = None
        self._update_input_shape()
        self._kick()
        return False

    def _press(self, w, ev):
        self.shell.panel.dismiss_popovers()
        sl = self._slot_at(ev.x)
        self.pressed = sl
        self.queue_draw()
        if sl is not None and ev.button == 3:
            self._popup_menu(sl, ev)
            self.pressed = None
        return True

    def _release(self, w, ev):
        sl = self._slot_at(ev.x)
        pressed, self.pressed = self.pressed, None
        self.queue_draw()
        if sl is None or sl is not pressed or ev.button == 3:
            return True
        ts = ev.time
        if sl.kind == "trash":
            spawn(["thunar", "trash:///"])
        elif sl.kind == "launchpad":
            spawn(["slope-launchpad"])
        elif ev.button == 2 and sl.group.appinfo is not None:
            self.apps.launch(sl.group)
        elif ev.button == 1:
            g = sl.group
            if not g.running and g.key in self.bounces:
                return True
            self.apps.activate_group(g, ts)
        return True

    def _popup_menu(self, sl, ev):
        menu = Gtk.Menu()
        menu.get_style_context().add_class("slope-dock-menu")
        if sl.kind == "launchpad":
            menu.append(self._mi("Open Launchpad", lambda: spawn(["slope-launchpad"])))
            menu.append(self._mi("Spotlight Search", lambda: spawn(["slope-spotlight"])))
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(self._mi("Dock Settings…", lambda: spawn(["slope-settings", "--page", "dock"])))
        elif sl.kind == "trash":
            menu.append(self._mi("Open", lambda: spawn(["thunar", "trash:///"])))
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(self._mi("Empty Trash", lambda: spawn(["gio", "trash", "--empty"]), self._trash_full))
        else:
            g = sl.group
            if g.windows:
                for w in g.windows:
                    title = w.get_name() or g.name
                    title = title if len(title) < 50 else title[:47] + "…"
                    if self.apps.is_hidden(w):
                        title += "  (closed)"
                    elif w.is_minimized():
                        title += "  (minimized)"
                    menu.append(self._mi(title, lambda win=w: self.apps.show_window(win, self.apps.now(self.get_window()))))
                menu.append(Gtk.SeparatorMenuItem())
            if g.appinfo is not None:
                menu.append(self._mi("New Window" if g.running else "Open", lambda: self.apps.launch(g)))
                for action in g.appinfo.list_actions()[:6]:
                    label = g.appinfo.get_action_name(action)
                    menu.append(self._mi(label, lambda a=action: g.appinfo.launch_action(
                        a, Gdk.Display.get_default().get_app_launch_context())))
                menu.append(Gtk.SeparatorMenuItem())
                opts = Gtk.Menu()
                keep = Gtk.CheckMenuItem(label="Keep in Dock")
                keep.set_active(g.pinned)
                keep.connect("toggled", lambda m: self.apps.pin(g, m.get_active()))
                opts.append(keep)
                oi = Gtk.MenuItem(label="Options")
                oi.set_submenu(opts)
                menu.append(oi)
            if g.running:
                menu.append(Gtk.SeparatorMenuItem())
                menu.append(self._mi("Show All Windows", lambda: self.apps.activate_group(
                    g, self.apps.now(self.get_window()))))
                menu.append(self._mi("Hide", lambda: self.apps.hide_group(g)))
                menu.append(self._mi("Quit", lambda: self.apps.quit_group(g)))
        menu.show_all()
        self.menu_open = True

        def done(*_):
            self.menu_open = False
            self.hover = False
            self._update_input_shape()
            self._kick()
        menu.connect("deactivate", done)
        rect = Gdk.Rectangle()
        top = self.H - self.below - sl.size - 8
        rect.x, rect.y, rect.width, rect.height = int(sl.x), int(top), int(sl.width), 1
        menu.attach_to_widget(self, None)
        menu.popup_at_rect(self.get_window(), rect, Gdk.Gravity.NORTH, Gdk.Gravity.SOUTH, ev)

    @staticmethod
    def _mi(label, cb, sensitive=True):
        mi = Gtk.MenuItem(label=label)
        mi.set_sensitive(sensitive)
        mi.connect("activate", lambda *_: cb())
        return mi

    def _poll_trash(self):
        try:
            full = bool(os.listdir(TRASH_DIR))
        except OSError:
            full = False
        if full != self._trash_full:
            self._trash_full = full
            self.icons.pop("trash", None)
            self.queue_draw()
        return True

    # -- drawing ------------------------------------------------------------
    @staticmethod
    def _is_generic_icon(pb):
        """True if pb is just the toolkit's stand-in "application" icon."""
        small = pb.scale_simple(16, 16, GdkPixbuf.InterpType.BILINEAR)
        refs = []
        # Openbox gives icon-less windows this stand-in icon
        try:
            refs.append(GdkPixbuf.Pixbuf.new_from_file("/usr/share/slope/default-window-icon.png"))
        except GLib.Error:
            pass
        theme = Gtk.IconTheme.get_default()
        for name in ("application-x-executable", "application-default-icon"):
            try:
                refs.append(theme.load_icon(name, 48, Gtk.IconLookupFlags.FORCE_SIZE))
            except GLib.Error:
                pass
        for ref in refs:
            if ref is None or ref.get_has_alpha() != small.get_has_alpha():
                continue
            a, b = small.get_pixels(), ref.scale_simple(16, 16, GdkPixbuf.InterpType.BILINEAR).get_pixels()
            if len(a) == len(b) and sum(abs(x - y) for x, y in zip(a, b)) / len(a) < 14:
                return True
        return False

    @staticmethod
    def _art_icon(px, painter):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
        cr = cairo.Context(surf)
        painter(cr, px * 0.03, px * 0.03, px * 0.94)
        return Gdk.pixbuf_get_from_surface(surf, 0, 0, px, px)

    def _icon(self, sl):
        key = sl.key
        pb = self.icons.get(key)
        if pb is None:
            px = int(self.M * self.get_scale_factor())
            if sl.kind == "trash":
                pb = icon_for(None, px, "user-trash-full" if self._trash_full else "user-trash")
            elif sl.kind == "launchpad":
                if Gtk.IconTheme.get_default().has_icon("slope-launchpad"):
                    pb = icon_for(None, px, "slope-launchpad")
                else:
                    pb = self._art_icon(px, art.draw_launchpad_icon)
            elif sl.group.appinfo is not None:
                pb = icon_for(sl.group.appinfo, px)
            else:
                # a window with no .desktop entry: use its own icon, or make one
                w = getattr(sl.group, "icon_window", None)
                pb = None
                if w is not None and not w.get_icon_is_fallback():
                    pb = w.get_icon()
                    if pb is not None and self._is_generic_icon(pb):
                        pb = None
                if pb is None:
                    pb = icon_for(None, px, name=sl.group.name)
            self.icons[key] = pb
        return pb

    def _draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        x0, total = self._layout()
        H, S = self.H, self.S
        baseline = H - self.below
        side = S * 0.35

        if self.style == "shelf":
            path = self._shelf(cr, x0 - side, x0 + total + side)
        else:
            path = self._box(cr, x0 - side * 0.4, x0 + total + side * 0.4)

        for sl in self.slots:
            if sl.kind == "sep":
                cx = sl.x + sl.width / 2.0
                if self.style == "shelf":
                    cr.set_source_rgba(1, 1, 1, 0.55)
                    cr.rectangle(cx - 0.5, H - 3 - self.shelf_h + 3, 1, self.shelf_h - 6)
                else:
                    cr.set_source_rgba(1, 1, 1, 0.45)
                    cr.rectangle(cx - 0.5, baseline - S + 4, 1, S - 8)
                cr.fill()
                continue
            if sl.size < 1:
                continue
            pb = self._icon(sl)
            if pb is None:
                continue
            size = sl.size
            ix = sl.x + self.gap / 2.0
            iy = baseline - size - self._bounce_offset(sl.key)
            scale = size / pb.get_width()
            # reflection on the glass shelf
            if self.style == "shelf":
                cr.save()
                path()
                cr.clip()
                cr.push_group()
                y0 = baseline + (baseline - (iy + size))
                cr.translate(ix, y0 + size)
                cr.scale(scale, -scale)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.get_source().set_filter(cairo.FILTER_GOOD)
                cr.paint()
                src = cr.pop_group()
                cr.set_source(src)
                mg = cairo.LinearGradient(0, baseline, 0, baseline + self.shelf_h * 0.8)
                mg.add_color_stop_rgba(0, 0, 0, 0, 0.42)
                mg.add_color_stop_rgba(1, 0, 0, 0, 0.0)
                cr.mask(mg)
                cr.restore()
            cr.save()
            cr.translate(ix, iy)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            if self.pressed is sl:
                pat = cr.get_source()
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.mask(pat)
            cr.restore()
            if sl.kind == "app" and sl.group.running and sl.grow > 0.6:
                self._dot(cr, ix + size / 2.0, H - (self.shelf_h * 0.28 + 3 if self.style == "shelf" else 4.5))

        # name label above the hovered icon
        if self.hover and self.mouse_x is not None and not self.menu_open:
            sl = self._slot_at(self.mouse_x)
            if sl is not None:
                if sl.key != self._label_key:
                    self._label_key, self._label_start = sl.key, time.monotonic()
                    self._kick()
                name = {"trash": "Trash", "launchpad": "Launchpad"}.get(sl.kind) or sl.group.name
                cr.push_group()
                self._label(cr, sl.x + sl.width / 2.0, baseline - sl.size - 10, name)
                cr.pop_group_to_source()
                cr.paint_with_alpha(self._label_alpha())
        return False

    def _shelf(self, cr, xl, xr):
        H, sh = self.H, self.shelf_h
        top = H - 3 - sh
        bot = H - 3
        inset = sh * 0.9

        def path():
            cr.new_path()
            cr.move_to(xl, bot)
            cr.line_to(xl + inset, top)
            cr.line_to(xr - inset, top)
            cr.line_to(xr, bot)
            cr.close_path()

        # soft shadow
        cr.save()
        for i in range(4):
            cr.new_path()
            cr.move_to(xl - i, bot + 2)
            cr.line_to(xl + inset, top + 2)
            cr.line_to(xr - inset, top + 2)
            cr.line_to(xr + i, bot + 2)
            cr.close_path()
            cr.set_source_rgba(0, 0, 0, 0.06)
            cr.fill()
        cr.restore()

        path()
        g = cairo.LinearGradient(0, top, 0, bot)
        g.add_color_stop_rgba(0, 0.98, 0.99, 1, 0.70)
        g.add_color_stop_rgba(0.45, 0.82, 0.88, 0.96, 0.52)
        g.add_color_stop_rgba(1, 0.70, 0.78, 0.90, 0.60)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.set_line_width(1)
        cr.stroke()
        # glossy streak
        cr.save()
        path()
        cr.clip()
        sg = cairo.LinearGradient(xl, 0, xr, 0)
        sg.add_color_stop_rgba(0, 1, 1, 1, 0)
        sg.add_color_stop_rgba(0.35, 1, 1, 1, 0.35)
        sg.add_color_stop_rgba(0.5, 1, 1, 1, 0.05)
        sg.add_color_stop_rgba(0.75, 1, 1, 1, 0.3)
        sg.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.rectangle(xl, top, xr - xl, sh * 0.45)
        cr.set_source(sg)
        cr.fill()
        cr.restore()
        # bright top edge and front lip
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.rectangle(xl + inset, top, xr - xl - 2 * inset, 1)
        cr.fill()
        lip = cairo.LinearGradient(0, bot - 4, 0, bot)
        lip.add_color_stop_rgba(0, 1, 1, 1, 0.85)
        lip.add_color_stop_rgba(1, 0.75, 0.82, 0.92, 0.85)
        cr.set_source(lip)
        cr.rectangle(xl + 3, bot - 4, xr - xl - 6, 4)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(xl, bot, xr - xl, 1)
        cr.fill()
        return path

    def _box(self, cr, xl, xr):
        H, S = self.H, self.S
        top = H - self.below - S - self.pad
        bot = H - 4
        r = S * 0.30

        def path():
            art.rounded_rect(cr, xl, top, xr - xl, bot - top, r)

        path()
        g = cairo.LinearGradient(0, top, 0, bot)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.55)
        g.add_color_stop_rgba(1, 0.85, 0.9, 0.98, 0.38)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.7)
        cr.set_line_width(1.2)
        cr.stroke()
        cr.save()
        path()
        cr.clip()
        art.rounded_rect(cr, xl + 2, top + 2, xr - xl - 4, (bot - top) * 0.46, r * 0.9)
        hg = cairo.LinearGradient(0, top, 0, top + (bot - top) * 0.5)
        hg.add_color_stop_rgba(0, 1, 1, 1, 0.6)
        hg.add_color_stop_rgba(1, 1, 1, 1, 0.05)
        cr.set_source(hg)
        cr.fill()
        cr.restore()
        art.rounded_rect(cr, xl - 0.5, top - 0.5, xr - xl + 1, bot - top + 1, r)
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.stroke()
        return path

    @staticmethod
    def _dot(cr, x, y):
        glow = cairo.RadialGradient(x, y, 0, x, y, 7)
        glow.add_color_stop_rgba(0, 0.6, 0.85, 1, 0.75)
        glow.add_color_stop_rgba(1, 0.6, 0.85, 1, 0)
        cr.set_source(glow)
        cr.arc(x, y, 7, 0, 2 * math.pi)
        cr.fill()
        core = cairo.RadialGradient(x, y - 0.8, 0, x, y, 2.6)
        core.add_color_stop_rgba(0, 1, 1, 1, 1)
        core.add_color_stop_rgba(1, 0.75, 0.9, 1, 1)
        cr.set_source(core)
        cr.arc(x, y, 2.6, 0, 2 * math.pi)
        cr.fill()

    def _label(self, cr, cx, bottom, text):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Inter, Sans 10"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        w, h = tw + 22, th + 10
        x = max(2, min(self.W - w - 2, cx - w / 2.0))
        y = max(2, bottom - h)
        art.rounded_rect(cr, x, y, w, h, h / 2.0)
        g = cairo.LinearGradient(0, y, 0, y + h)
        g.add_color_stop_rgba(0, 0.30, 0.32, 0.38, 0.88)
        g.add_color_stop_rgba(0.5, 0.15, 0.16, 0.2, 0.88)
        g.add_color_stop_rgba(1, 0.1, 0.1, 0.14, 0.9)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        cr.stroke()
        art.rounded_rect(cr, x + 3, y + 1.5, w - 6, h * 0.45, h / 2.5)
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.fill()
        cr.move_to(x + 11, y + 5)
        cr.set_source_rgba(1, 1, 1, 1)
        PangoCairo.show_layout(cr, layout)
