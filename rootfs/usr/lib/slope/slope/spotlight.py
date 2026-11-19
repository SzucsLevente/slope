"""slope-spotlight: a glassy search bar for apps, files, settings and quick maths."""
import ast
import math
import operator
import os
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import art  # noqa: E402
from .common import SHARE, friendly_name, icon_for, launch, spawn  # noqa: E402

W = 680

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos,
        ast.FloorDiv: operator.floordiv}
_NAMES = {"pi": math.pi, "e": math.e, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log10, "ln": math.log, "abs": abs, "round": round}


def calc(expr):
    """Safely evaluate a small arithmetic expression."""
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", ".")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 1000:
                raise ValueError
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _NAMES and not callable(_NAMES[n.id]):
            return _NAMES[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_NAMES.get(n.func.id)):
            return _NAMES[n.func.id](*[ev(a) for a in n.args])
        raise ValueError
    if not any(c.isdigit() for c in expr) or not any(c in expr for c in "+-*/%^()"):
        return None
    try:
        v = ev(ast.parse(expr, mode="eval"))
    except Exception:
        return None
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            v = int(v)
        else:
            v = round(v, 10)
    return v


SETTINGS_PAGES = [
    ("Wallpaper", "appearance", "preferences-desktop-wallpaper"),
    ("Dock", "dock", "preferences-desktop"),
    ("Displays", "displays", "preferences-desktop-display"),
    ("Date & Time", "clock", "preferences-system-time"),
    ("Keyboard Shortcuts", "shortcuts", "preferences-desktop-keyboard"),
    ("About This Computer", "about", "computer"),
]


class Spotlight(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_wmclass("slope-spotlight", "Slope")
        self.set_title("slope-spotlight")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis is not None:
            self.set_visual(vis)
        self.get_style_context().add_class("slope-spotlight")
        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("focus-out-event", lambda *_: GLib.timeout_add(150, self._maybe_quit))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_border_width(10)
        self.add(box)
        row = Gtk.Box(spacing=8)
        row.set_margin_start(10)
        icon = Gtk.Image.new_from_icon_name("system-search-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        icon.set_pixel_size(26)
        row.pack_start(icon, False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Spotlight Search")
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda *_: self._run(self.list.get_selected_row()))
        row.pack_start(self.entry, True, True, 0)
        box.pack_start(row, False, False, 0)

        self.sep = Gtk.Separator()
        self.sep.set_margin_top(6)
        box.pack_start(self.sep, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list.connect("row-activated", lambda lb, r: self._run(r))
        self.scroll = self.list
        box.pack_start(self.list, True, True, 6)

        self.apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
        self.set_default_size(W, -1)
        self.set_size_request(W, -1)
        self._place()
        self.show_all()
        self.sep.hide()
        self.scroll.hide()
        self.present()
        self.entry.grab_focus()
        self._focused_once = False
        self.connect("focus-in-event", lambda *_: setattr(self, "_focused_once", True))

    def _place(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        g = mon.get_geometry()
        self._x = g.x + (g.width - W) // 2
        self._y = g.y + int(g.height * 0.22)
        # slide down a little while fading in
        self._anim_step = 0
        self.move(self._x, self._y - 14)
        self.set_opacity(0.0)
        GLib.timeout_add(16, self._appear)

    def _appear(self):
        self._anim_step += 1
        t = min(1.0, self._anim_step / 9.0)
        e = 1 - (1 - t) ** 3
        self.move(self._x, int(self._y - 14 * (1 - e)))
        self.set_opacity(e)
        return t < 1.0

    def _maybe_quit(self):
        if not self.is_active() and self._focused_once:
            Gtk.main_quit()
        return False

    def _draw(self, w, cr):
        a = self.get_allocation()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        r = 14
        art.rounded_rect(cr, 0.5, 0.5, a.width - 1, a.height - 1, r)
        g = cairo.LinearGradient(0, 0, 0, a.height)
        g.add_color_stop_rgba(0, 0.98, 0.985, 1, 0.97)
        g.add_color_stop_rgba(1, 0.93, 0.945, 0.97, 0.96)
        cr.set_source(g)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.set_line_width(1)
        cr.stroke()
        art.rounded_rect(cr, 3, 2, a.width - 6, 30, r - 2)
        hg = cairo.LinearGradient(0, 0, 0, 32)
        hg.add_color_stop_rgba(0, 1, 1, 1, 0.9)
        hg.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(hg)
        cr.fill()
        return False

    def _key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        if ev.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            rows = self.list.get_children()
            if not rows:
                return True
            cur = self.list.get_selected_row()
            i = rows.index(cur) if cur in rows else -1
            i = max(0, min(len(rows) - 1, i + (1 if ev.keyval == Gdk.KEY_Down else -1)))
            self.list.select_row(rows[i])
            rows[i].grab_focus()
            self.entry.grab_focus_without_selecting()
            return True
        return False

    def _row(self, icon, title, subtitle, action):
        row = Gtk.ListBoxRow()
        row.action = action
        b = Gtk.Box(spacing=10)
        if isinstance(icon, Gio.AppInfo):
            img = Gtk.Image.new_from_pixbuf(icon_for(icon, 32))
        elif isinstance(icon, Gio.Icon):
            img = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DND)
        else:
            img = Gtk.Image.new_from_icon_name(icon or "application-x-executable", Gtk.IconSize.DND)
        img.set_pixel_size(32)
        b.pack_start(img, False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t = Gtk.Label(label=title, xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(t, False, False, 0)
        if subtitle:
            s = Gtk.Label(label=subtitle, xalign=0)
            s.set_ellipsize(Pango.EllipsizeMode.END)
            s.get_style_context().add_class("subtitle")
            v.pack_start(s, False, False, 0)
        b.pack_start(v, True, True, 0)
        row.add(b)
        return row

    def _changed(self, entry):
        for c in self.list.get_children():
            self.list.remove(c)
        q = entry.get_text().strip()
        if not q:
            self.sep.hide()
            self.scroll.hide()
            self.resize(W, 1)
            return
        ql = q.lower()
        rows = []
        v = calc(q)
        if v is not None:
            rows.append(self._row("accessories-calculator", "= %s" % v, "Press Return to copy the result",
                                  ("copy", str(v))))
        scored = []
        for a in self.apps:
            names = [friendly_name(a), a.get_name() or "", a.get_generic_name() or "",
                     " ".join(a.get_keywords() or []), a.get_executable() or ""]
            best = 0
            for i, n in enumerate(names):
                n = n.lower()
                if n.startswith(ql):
                    best = max(best, 100 - i * 5)
                elif ql in n:
                    best = max(best, 60 - i * 5)
            if best:
                scored.append((best, friendly_name(a), a))
        scored.sort(key=lambda t: (-t[0], t[1].lower()))
        for _, name, a in scored[:8]:
            sub = "Application"
            if name != a.get_name():
                sub = "Application — %s" % a.get_name()
            rows.append(self._row(a, name, sub, ("app", a)))
        for title, page, icon in SETTINGS_PAGES:
            if ql in title.lower():
                rows.append(self._row(icon, title, "System Settings", ("settings", page)))
        rows += self._files(ql)
        rows.append(self._row("system-search", "Search files for “%s”" % q, "Open in Files",
                              ("search", q)))
        if not v and " " not in q:
            rows.append(self._row("utilities-terminal", "Run “%s”" % q, "Run command", ("run", q)))
        for r in rows[:10]:
            self.list.add(r)
        self.list.show_all()
        self.sep.show()
        self.scroll.show()
        if rows:
            self.list.select_row(rows[0])
        self.resize(W, 1)

    def _files(self, ql):
        if len(ql) < 2:
            return []
        out = []
        home = GLib.get_home_dir()
        roots = [home] + [os.path.join(home, d) for d in ("Desktop", "Documents", "Downloads", "Pictures", "Music")]
        seen = set()
        for root in roots:
            try:
                names = os.listdir(root)
            except OSError:
                continue
            for n in names:
                if n.startswith(".") or ql not in n.lower():
                    continue
                p = os.path.join(root, n)
                if p in seen:
                    continue
                seen.add(p)
                icon = "folder" if os.path.isdir(p) else "text-x-generic"
                ct, _ = Gio.content_type_guess(p, None)
                if not os.path.isdir(p) and ct:
                    gicon = Gio.content_type_get_icon(ct)
                    out.append(self._row(gicon, n, p.replace(home, "~", 1), ("open", p)))
                else:
                    out.append(self._row(icon, n, p.replace(home, "~", 1), ("open", p)))
                if len(out) >= 5:
                    return out
        return out

    def _run(self, row):
        if row is None:
            return
        kind, arg = row.action
        if kind == "app":
            launch(arg)
        elif kind == "copy":
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text(arg, -1)
            cb.store()
        elif kind == "settings":
            spawn(["slope-settings", "--page", arg])
        elif kind == "open":
            spawn(["xdg-open", arg])
        elif kind == "search":
            spawn(["thunar", GLib.get_home_dir()])
        elif kind == "run":
            spawn(["/bin/sh", "-c", arg])
        Gtk.main_quit()


def main():
    GLib.set_prgname("slope-spotlight")
    # only one Spotlight at a time: a second invocation toggles it off
    app = Gio.Application(application_id="org.slope.Spotlight", flags=Gio.ApplicationFlags.FLAGS_NONE)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        app.activate()
        return 0
    app.connect("activate", lambda *_: Gtk.main_quit())
    prov = Gtk.CssProvider()
    try:
        prov.load_from_path(os.path.join(SHARE, "shell.css"))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except GLib.Error:
        pass
    Spotlight()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
