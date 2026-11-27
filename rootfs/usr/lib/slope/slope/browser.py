"""slope-browser: Slope's web browser (WebKitGTK, the engine family of Safari).

A Safari-like window: a glossy toolbar with back / forward, one field for
addresses and searches (with the page's loading progress), tabs under it,
a start page with your favourites, private windows (nothing is kept),
bookmarks, find on page, zoom, downloads to ~/Downloads, menus in the top
bar, and dark mode.

    slope-browser [--private] [URL ...]
"""
import html
import json
import os
import sys
import urllib.parse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, WebKit2  # noqa: E402

APP_ID = "org.slope.Browser"
DESKTOP = "/usr/share/applications/slope-browser.desktop"
CONFIG = os.path.join(GLib.get_user_config_dir(), "slope", "browser.json")
DATA = os.path.join(GLib.get_user_data_dir(), "slope-browser")
CACHE = os.path.join(GLib.get_user_cache_dir(), "slope-browser")
SEARCH = "https://duckduckgo.com/?q=%s"
HOME = "slope:start"

DEFAULT_BOOKMARKS = [
    ("Wikipedia", "https://www.wikipedia.org/"),
    ("DuckDuckGo", "https://duckduckgo.com/"),
    ("YouTube", "https://www.youtube.com/"),
    ("GitHub", "https://github.com/"),
    ("Ubuntu", "https://ubuntu.com/"),
    ("OpenStreetMap", "https://www.openstreetmap.org/"),
]

CSS = b"""
.slope-browser .toolbar-box { padding: 6px 10px; border-bottom: 1px solid rgba(0,0,0,0.22);
  background-image: linear-gradient(to bottom, #f4f4f5, #dcdcde); }
.slope-browser.private .toolbar-box { background-image: linear-gradient(to bottom, #4a4a50, #2e2e33);
  border-bottom-color: #111; }
.slope-browser .address { border-radius: 8px; min-height: 26px; }
.slope-browser .address progress { background-image: none; background-color: alpha(@slope_accent, 0.35);
  border: none; border-radius: 0; min-height: 2px; margin-bottom: -1px; }
.slope-browser .tabs-box { padding: 0; background-color: #cfcfd2; border-bottom: 1px solid rgba(0,0,0,0.25); }
.slope-browser .tabs-box .tab { border-right: 1px solid rgba(0,0,0,0.18); padding: 2px 6px;
  background-image: linear-gradient(to bottom, #cdcdd0, #c2c2c6); }
.slope-browser .tabs-box .tab.current { background-image: linear-gradient(to bottom, #f2f2f3, #e6e6e8); }
.slope-browser .tabs-box .tab label { font-size: 9.5pt; }
.slope-browser .tabs-box .tab button.tab-close { padding: 0; min-width: 18px; min-height: 18px; border-radius: 999px;
  border: none; box-shadow: none; background-image: none; opacity: 0.55; }
.slope-browser .tabs-box .tab button.tab-close:hover { opacity: 1; background-color: rgba(0,0,0,0.12); }

/* Private windows: graphite all over (like Safari), whatever the appearance */
.slope-browser.private .toolbar-box button {
  color: #f0f0f3; text-shadow: 0 -1px rgba(0,0,0,0.6); border-color: rgba(0,0,0,0.7);
  background-image: linear-gradient(to bottom, #66666c 0%, #56565c 20%, #48484e 49%, #3a3a3f 50%, #434348 75%, #515157 100%);
  box-shadow: inset 0 1px rgba(255,255,255,0.15), 0 1px 1px rgba(0,0,0,0.4); }
.slope-browser.private .toolbar-box button:hover {
  background-image: linear-gradient(to bottom, #72727a, #5f5f66 49%, #4b4b51 50%, #56565d); }
.slope-browser.private .toolbar-box button:disabled { color: rgba(240,240,243,0.35); }
.slope-browser.private .address { background-color: #1f1f23; color: #f0f0f3; border-color: rgba(0,0,0,0.75);
  caret-color: #ffffff; }
.slope-browser.private .private-badge { color: #ffffff; font-weight: bold; font-size: 9pt; padding: 2px 10px;
  border-radius: 999px; background-image: linear-gradient(to bottom, #8d8d96, #5e5e66);
  box-shadow: inset 0 1px rgba(255,255,255,0.3); }
.slope-browser.private .tabs-box { background-color: #232326; border-bottom-color: #0e0e10; }
.slope-browser.private .tabs-box .tab { border-right-color: #121214;
  background-image: linear-gradient(to bottom, #2e2e32, #28282b); }
.slope-browser.private .tabs-box .tab.current { background-image: linear-gradient(to bottom, #48484e, #3c3c41); }
.slope-browser.private .tabs-box .tab label, .slope-browser.private .tabs-box .tab button { color: #e8e8ec; }
.slope-browser.private .findbar { background-image: linear-gradient(to bottom, #3a3a3e, #2f2f33);
  border-bottom-color: #121214; }
.slope-browser .findbar { padding: 4px 10px; background-image: linear-gradient(to bottom, #ececee, #dedee0);
  border-bottom: 1px solid rgba(0,0,0,0.2); }
"""

DARK_CSS = b"""
.slope-browser .toolbar-box { background-image: linear-gradient(to bottom, #45454a, #333337); border-bottom-color: #121214; }
.slope-browser .tabs-box { background-color: #2a2a2d; border-bottom-color: #121214; }
.slope-browser .tabs-box .tab { background-image: linear-gradient(to bottom, #333337, #2c2c30);
  border-right-color: #19191b; }
.slope-browser .tabs-box .tab.current { background-image: linear-gradient(to bottom, #4a4a50, #3e3e43); }
.slope-browser .tabs-box .tab button.tab-close:hover { background-color: rgba(255,255,255,0.12); }
.slope-browser .findbar { background-image: linear-gradient(to bottom, #3a3a3e, #2f2f33); border-bottom-color: #121214; }
"""


def load_config():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("bookmarks", [list(b) for b in DEFAULT_BOOKMARKS])
    cfg.setdefault("history", [])
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, CONFIG)


def to_url(text):
    """What was typed in the address field: an address, or a search."""
    t = text.strip()
    if not t:
        return HOME
    if t.startswith(("http://", "https://", "file://", "about:", "slope:", "view-source:")):
        return t
    if " " not in t and ("." in t or t.startswith("localhost")) and not t.endswith("."):
        return "https://" + t
    return SEARCH % urllib.parse.quote_plus(t)


def start_page(bookmarks, dark, private=False):
    """The new-tab page: a search field and the favourites as glossy tiles."""
    bg, fg, tile = (("#1e1e21", "#ececf0", "#2f2f34") if dark else ("#f2f3f6", "#1d1d1f", "#ffffff"))
    tiles = "".join(
        '<a class="tile" href="%s"><span class="icon">%s</span><span class="name">%s</span></a>'
        % (html.escape(u, quote=True), html.escape((n or "?")[0].upper()), html.escape(n))
        for n, u in bookmarks[:24])
    return """<!doctype html><html><head><meta charset="utf-8"><title>Start Page</title><style>
body{margin:0;font-family:Inter,'Helvetica Neue',sans-serif;background:%(bg)s;color:%(fg)s;
 display:flex;flex-direction:column;align-items:center}
h1{font-weight:600;font-size:15px;opacity:.6;margin:64px 0 14px;letter-spacing:.02em}
form{width:min(560px,80vw)}
input{width:100%%;box-sizing:border-box;font-size:17px;padding:12px 18px;border-radius:12px;
 border:1px solid rgba(127,127,127,.35);background:%(tile)s;color:%(fg)s;outline:none;
 box-shadow:0 2px 10px rgba(0,0,0,.08)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,96px);gap:22px;justify-content:center;
 width:min(760px,90vw);margin-top:42px}
.tile{display:flex;flex-direction:column;align-items:center;gap:8px;text-decoration:none;color:%(fg)s;font-size:12px}
.icon{width:64px;height:64px;border-radius:16px;display:flex;align-items:center;justify-content:center;
 font-size:28px;font-weight:700;color:#fff;
 background:linear-gradient(to bottom,#7fbcff,#2f86f3 55%%,#1f6fe0);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.6),0 3px 8px rgba(0,0,0,.25)}
.tile:hover .icon{filter:brightness(1.08)}
.name{max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.private{margin:56px 0 -36px;max-width:520px;text-align:center;font-size:13px;opacity:.75;line-height:1.5}
.private b{display:block;font-size:17px;opacity:1;margin-bottom:4px}
</style></head><body>%(private)s<h1>Favorites</h1>
<form action="https://duckduckgo.com/"><input name="q" placeholder="Search or enter website name" autofocus></form>
<div class="grid">%(tiles)s</div></body></html>""" % {"bg": bg, "fg": fg, "tile": tile, "tiles": tiles,
       "private": ('<div class="private"><b>Private Browsing</b>Slope Browser won\u2019t remember the pages '
                   'you visit, your searches, cookies or website data from this window.</div>' if private else "")}


class Tab:
    def __init__(self, win, view):
        self.win, self.view = win, view
        # an event box, not a button: a button inside a button (the close
        # button) never receives its clicks
        self.button = Gtk.EventBox()
        box = Gtk.Box(spacing=6)
        box.get_style_context().add_class("tab")
        self.box = box
        self.close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        self.close.set_relief(Gtk.ReliefStyle.NONE)
        self.close.get_style_context().add_class("tab-close")
        self.close.set_tooltip_text("Close Tab")
        self.close.connect("clicked", lambda *_: win.close_tab(self))
        self.label = Gtk.Label(label="New Tab")
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_hexpand(True)
        box.pack_start(self.close, False, False, 0)
        box.pack_start(self.label, True, True, 0)
        box.pack_start(Gtk.Box(), False, False, 12)      # keeps the title centred
        self.button.add(box)

        def press(w, ev):
            if ev.button == 2:                            # middle click closes, like everywhere
                win.close_tab(self)
            elif ev.button == 1:
                win.select(self)
            return False
        self.button.connect("button-press-event", press)
        self.button.show_all()


class BrowserWindow(Gtk.ApplicationWindow):
    def __init__(self, app, private=False):
        super().__init__(application=app, title="Slope Browser")
        self.app, self.private = app, private
        self.set_default_size(1100, 760)
        self.set_icon_name("web-browser")
        ctx = self.get_style_context()
        ctx.add_class("slope-browser")
        if private:
            ctx.add_class("private")
        self.context = app.private_context() if private else app.context
        self.tabs, self.current = [], None

        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(v)
        bar = Gtk.Box(spacing=6)
        bar.get_style_context().add_class("toolbar-box")
        nav = Gtk.Box()
        nav.get_style_context().add_class("linked")
        self.back = self._button("go-previous-symbolic", "Back", lambda: self.view() and self.view().go_back())
        self.fwd = self._button("go-next-symbolic", "Forward", lambda: self.view() and self.view().go_forward())
        nav.pack_start(self.back, False, False, 0)
        nav.pack_start(self.fwd, False, False, 0)
        bar.pack_start(nav, False, False, 0)
        self.reload_btn = self._button("view-refresh-symbolic", "Reload", self.reload_or_stop)
        bar.pack_start(self.reload_btn, False, False, 0)
        self.address = Gtk.Entry()
        self.address.get_style_context().add_class("address")
        self.address.set_placeholder_text("Search or enter website name")
        self.address.set_hexpand(True)
        self.address.connect("activate", lambda e: self.go(e.get_text()))
        self.address.connect("focus-in-event", lambda *_: GLib.idle_add(self.address.select_region, 0, -1))
        comp = Gtk.EntryCompletion()
        self.history_store = Gtk.ListStore(str)
        comp.set_model(self.history_store)
        comp.set_text_column(0)
        comp.set_minimum_key_length(2)
        comp.set_match_func(lambda c, key, it, _d: key.lower() in (c.get_model()[it][0] or "").lower(), None)
        self.address.set_completion(comp)
        bar.pack_start(self.address, True, True, 0)
        if private:
            pl = Gtk.Label(label="Private")
            pl.get_style_context().add_class("private-badge")
            pl.set_valign(Gtk.Align.CENTER)
            pl.set_tooltip_text("Nothing from this window is kept: no history, cookies or cache")
            bar.pack_start(pl, False, False, 4)
        bar.pack_start(self._button("bookmark-new-symbolic", "Add Bookmark", self.add_bookmark), False, False, 0)
        bar.pack_start(self._button("list-add-symbolic", "New Tab", lambda: self.new_tab()), False, False, 0)
        v.pack_start(bar, False, False, 0)
        self.tabbar = Gtk.Box(homogeneous=True)
        self.tabbar.get_style_context().add_class("tabs-box")
        self.tabbar.set_no_show_all(True)
        v.pack_start(self.tabbar, False, False, 0)
        # find on page
        self.findbar = Gtk.Revealer()
        fb = Gtk.Box(spacing=6)
        fb.get_style_context().add_class("findbar")
        self.find = Gtk.SearchEntry()
        self.find.set_placeholder_text("Find on page")
        self.find.connect("search-changed", lambda e: self._find(e.get_text()))
        self.find.connect("activate", lambda *_: self.view() and self.view().get_find_controller().search_next())
        self.find.connect("key-press-event", self._find_key)
        self.find_lbl = Gtk.Label()
        fb.pack_start(self.find, False, False, 0)
        fb.pack_start(self._button("go-up-symbolic", "Previous",
                                   lambda: self.view() and self.view().get_find_controller().search_previous()),
                      False, False, 0)
        fb.pack_start(self._button("go-down-symbolic", "Next",
                                   lambda: self.view() and self.view().get_find_controller().search_next()),
                      False, False, 0)
        fb.pack_start(self.find_lbl, False, False, 4)
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: self.hide_find())
        fb.pack_end(done, False, False, 0)
        self.findbar.add(fb)
        v.pack_start(self.findbar, False, False, 0)
        self.pages = Gtk.Stack()
        v.pack_start(self.pages, True, True, 0)

        for name, cb in (("new-tab", lambda *_: self.new_tab()), ("close-tab", lambda *_: self.close_tab(self.current)),
                         ("open-location", lambda *_: self.address.grab_focus()),
                         ("reload", lambda *_: self.view() and self.view().reload()),
                         ("back", lambda *_: self.view() and self.view().go_back()),
                         ("forward", lambda *_: self.view() and self.view().go_forward()),
                         ("home", lambda *_: self.go(HOME)), ("find", lambda *_: self.show_find()),
                         ("zoom-in", lambda *_: self.zoom(1.1)), ("zoom-out", lambda *_: self.zoom(1 / 1.1)),
                         ("zoom-reset", lambda *_: self.zoom(None)), ("add-bookmark", lambda *_: self.add_bookmark()),
                         ("next-tab", lambda *_: self.cycle(1)), ("previous-tab", lambda *_: self.cycle(-1)),
                         ("open-bookmark", None), ("copy", lambda *_: self._edit("Copy")),
                         ("cut", lambda *_: self._edit("Cut")), ("paste", lambda *_: self._edit("Paste")),
                         ("select-all", lambda *_: self._edit("SelectAll"))):
            if name == "open-bookmark":
                a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
                a.connect("activate", lambda a, p: self.go(p.get_string()))
            else:
                a = Gio.SimpleAction.new(name, None)
                a.connect("activate", cb)
            self.add_action(a)
        self.refresh_history()
        self.show_all()

    def _button(self, icon, tip, cb):
        b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_: cb())
        return b

    # -- tabs --------------------------------------------------------------------
    def view(self):
        return self.current.view if self.current else None

    def new_tab(self, url=HOME, related=None, select=True):
        if related is not None:
            view = WebKit2.WebView.new_with_related_view(related)
        else:
            view = WebKit2.WebView(web_context=self.context)
        view.set_settings(self.app.settings)
        tab = Tab(self, view)
        view.connect("notify::title", lambda *_: self._titled(tab))
        view.connect("notify::uri", lambda *_: self._uri(tab))
        view.connect("notify::estimated-load-progress", lambda *_: self._progress(tab))
        view.connect("load-changed", lambda v, e: self._load_changed(tab, e))
        view.connect("create", lambda v, action: self._create(v))
        view.connect("decide-policy", self._policy)
        view.connect("load-failed", lambda v, e, uri, err: self._failed(v, uri, err))
        self.tabs.append(tab)
        self.pages.add(view)
        view.show()
        self.tabbar.pack_start(tab.button, True, True, 0)
        self._tabbar()
        if select:
            self.select(tab)
        if url:
            self.load(view, url)
        if url == HOME and select:
            self.address.grab_focus()
        return tab

    def _create(self, view):
        """A link opening a new window (target=_blank, window.open): a new tab."""
        return self.new_tab(None, related=view).view

    def select(self, tab):
        self.current = tab
        self.pages.set_visible_child(tab.view)
        for t in self.tabs:
            ctx = t.box.get_style_context()
            (ctx.add_class if t is tab else ctx.remove_class)("current")
        self._uri(tab)
        self._titled(tab)
        self._nav()
        tab.view.grab_focus()

    def close_tab(self, tab):
        if tab is None or tab not in self.tabs:
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)
        self.tabbar.remove(tab.button)
        self.pages.remove(tab.view)
        tab.view.try_close()
        if not self.tabs:
            self.destroy()
            return
        self._tabbar()
        self.select(self.tabs[min(i, len(self.tabs) - 1)])

    def cycle(self, step):
        if self.current and len(self.tabs) > 1:
            self.select(self.tabs[(self.tabs.index(self.current) + step) % len(self.tabs)])

    def _tabbar(self):
        if len(self.tabs) > 1:
            self.tabbar.show()
            for t in self.tabs:
                t.button.show_all()
        else:
            self.tabbar.hide()

    # -- loading -------------------------------------------------------------------
    def go(self, text):
        if self.view() is None:
            self.new_tab(None)
        self.load(self.view(), to_url(text))
        self.view().grab_focus()

    def load(self, view, url):
        if url == HOME:
            view.load_html(start_page(self.app.cfg["bookmarks"], self.app.dark() or self.private,
                                      self.private), "slope:start")
        else:
            view.load_uri(url)

    def reload_or_stop(self):
        v = self.view()
        if v is None:
            return
        if v.is_loading():
            v.stop_loading()
        else:
            v.reload()

    def _titled(self, tab):
        t = tab.view.get_title() or self._short(tab.view.get_uri()) or "New Tab"
        if (tab.view.get_uri() or "").startswith("slope:start"):
            t = "Start Page"
        tab.label.set_text(t)
        tab.button.set_tooltip_text(t)
        if tab is self.current:
            self.set_title(t)

    @staticmethod
    def _short(uri):
        if not uri:
            return ""
        return urllib.parse.urlparse(uri).netloc or uri

    def _uri(self, tab):
        if tab is not self.current or self.address.has_focus():
            return
        uri = tab.view.get_uri() or ""
        self.address.set_text("" if uri.startswith("slope:start") else uri)
        self._nav()

    def _progress(self, tab):
        if tab is self.current:
            p = tab.view.get_estimated_load_progress()
            self.address.set_progress_fraction(0 if p >= 1.0 else p)

    def _load_changed(self, tab, event):
        if tab is self.current:
            loading = tab.view.is_loading()
            img = "process-stop-symbolic" if loading else "view-refresh-symbolic"
            self.reload_btn.set_image(Gtk.Image.new_from_icon_name(img, Gtk.IconSize.BUTTON))
            self.reload_btn.set_tooltip_text("Stop" if loading else "Reload")
            self._nav()
        if event == WebKit2.LoadEvent.FINISHED:
            if tab is self.current:
                self.address.set_progress_fraction(0)
            uri = tab.view.get_uri() or ""
            if not self.private and uri.startswith("http"):
                self.app.remember(uri, tab.view.get_title())

    def _nav(self):
        v = self.view()
        self.back.set_sensitive(bool(v and v.can_go_back()))
        self.fwd.set_sensitive(bool(v and v.can_go_forward()))

    def _policy(self, view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            if not decision.is_mime_type_supported():
                decision.download()
                return True
        return False

    def _failed(self, view, uri, err):
        if err.matches(WebKit2.NetworkError.quark(), WebKit2.NetworkError.CANCELLED) or \
                err.matches(WebKit2.PolicyError.quark(), WebKit2.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE):
            return False
        page = ("<!doctype html><meta charset=utf-8><body style='font-family:Inter,sans-serif;text-align:center;"
                "padding-top:18vh;color:#666'><h2 style='color:#333'>Slope Browser can’t open this page</h2>"
                "<p>%s</p><p style='font-size:13px'>%s</p></body>" % (html.escape(self._short(uri)),
                                                                       html.escape(err.message)))
        view.load_alternate_html(page, uri, None)
        return True

    # -- tools ----------------------------------------------------------------------
    def refresh_history(self):
        self.history_store.clear()
        seen = set()
        for b in self.app.cfg["bookmarks"]:
            if b[1] not in seen:
                seen.add(b[1])
                self.history_store.append([b[1]])
        for h in reversed(self.app.cfg["history"][-400:]):
            if h[0] not in seen:
                seen.add(h[0])
                self.history_store.append([h[0]])

    def add_bookmark(self):
        v = self.view()
        uri = v.get_uri() if v else None
        if not uri or uri.startswith("slope:"):
            return
        title = v.get_title() or self._short(uri)
        bms = self.app.cfg["bookmarks"]
        if not any(b[1] == uri for b in bms):
            bms.append([title, uri])
            self.app.save()
            self.app.rebuild_bookmarks_menu()

    def zoom(self, factor):
        v = self.view()
        if v:
            v.set_zoom_level(1.0 if factor is None else max(0.3, min(3.0, v.get_zoom_level() * factor)))

    def _edit(self, cmd):
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable):
            {"Copy": focus.copy_clipboard, "Cut": focus.cut_clipboard, "Paste": focus.paste_clipboard,
             "SelectAll": lambda: focus.select_region(0, -1)}[cmd]()
        elif self.view():
            self.view().execute_editing_command(cmd)

    def show_find(self):
        self.findbar.set_reveal_child(True)
        self.find.grab_focus()

    def hide_find(self):
        self.findbar.set_reveal_child(False)
        if self.view():
            self.view().get_find_controller().search_finish()
            self.view().grab_focus()

    def _find(self, text):
        v = self.view()
        if not v:
            return
        fc = v.get_find_controller()
        if not text:
            fc.search_finish()
            self.find_lbl.set_text("")
            return
        fc.search(text, WebKit2.FindOptions.CASE_INSENSITIVE | WebKit2.FindOptions.WRAP_AROUND, 1000)
        fc.count_matches(text, WebKit2.FindOptions.CASE_INSENSITIVE, 1000)
        if not getattr(fc, "_slope_hooked", False):
            fc.connect("counted-matches", lambda c, n: self.find_lbl.set_text("%d matches" % n if n else "Not found"))
            fc.connect("failed-to-find-text", lambda c: self.find_lbl.set_text("Not found"))
            fc._slope_hooked = True

    def _find_key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.hide_find()
            return True
        return False


class BrowserApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.cfg = load_config()
        self._private = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        os.makedirs(DATA, exist_ok=True)
        os.makedirs(CACHE, exist_ok=True)
        mgr = WebKit2.WebsiteDataManager(base_data_directory=DATA, base_cache_directory=CACHE)
        try:
            mgr.set_favicons_enabled(True)
        except Exception:
            pass
        self.context = WebKit2.WebContext.new_with_website_data_manager(mgr)
        self.context.get_cookie_manager().set_persistent_storage(
            os.path.join(DATA, "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)
        self.context.connect("download-started", self._download)
        self.settings = WebKit2.Settings()
        self.settings.set_enable_smooth_scrolling(True)
        self.settings.set_enable_developer_extras(True)
        self.settings.set_javascript_can_open_windows_automatically(False)
        self.settings.set_enable_back_forward_navigation_gestures(True)
        try:
            from .common import install_css
            install_css(CSS, DARK_CSS)
        except Exception:
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb in (("new-window", lambda *_: self.window()),
                         ("new-private-window", lambda *_: self.window(private=True)),
                         ("quit", lambda *_: self._quit())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self._menus()
        self.hold()
        try:
            from . import keepalive
            keepalive.register(APP_ID, DESKTOP)

            def poll():
                if keepalive.quit_requested() and not self.get_windows():
                    keepalive.unregister()
                    self.release()
                    return False
                return True
            GLib.timeout_add(500, poll)
        except Exception:
            pass

    def _quit(self):
        try:
            from . import keepalive
            keepalive.unregister()
        except Exception:
            pass
        self.quit()

    def dark(self):
        try:
            from .common import dark_theme
            return dark_theme()
        except Exception:
            return False

    def private_context(self):
        return WebKit2.WebContext.new_ephemeral()

    def save(self):
        save_config(self.cfg)

    def remember(self, uri, title):
        h = [x for x in self.cfg["history"] if x[0] != uri]
        h.append([uri, title or ""])
        self.cfg["history"] = h[-2000:]
        self.save()

    def _download(self, ctx, download):
        def dest(dl, suggested):
            d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
            os.makedirs(d, exist_ok=True)
            base, ext = os.path.splitext(suggested or "download")
            path, n = os.path.join(d, base + ext), 1
            while os.path.exists(path):
                path, n = os.path.join(d, "%s (%d)%s" % (base, n, ext)), n + 1
            dl.set_destination(Gio.File.new_for_path(path).get_uri())
            return True

        def finished(dl):
            path = Gio.File.new_for_uri(dl.get_destination()).get_path() if dl.get_destination() else ""
            GLib.spawn_async(["notify-send", "-i", "folder-download", "Download finished", os.path.basename(path)],
                             flags=GLib.SpawnFlags.SEARCH_PATH)
        download.connect("decide-destination", dest)
        download.connect("finished", finished)

    def _menus(self):
        def section(*items):
            m = Gio.Menu()
            for label, action in items:
                m.append(label, action)
            return m

        def menu(*sections):
            m = Gio.Menu()
            for s in sections:
                m.append_section(None, s)
            return m
        bar = Gio.Menu()
        bar.append_submenu("File", menu(
            section(("New Window", "app.new-window"), ("New Private Window", "app.new-private-window"),
                    ("New Tab", "win.new-tab")),
            section(("Open Location…", "win.open-location")),
            section(("Close Tab", "win.close-tab"))))
        bar.append_submenu("Edit", menu(section(("Cut", "win.cut"), ("Copy", "win.copy"), ("Paste", "win.paste"),
                                                ("Select All", "win.select-all")),
                                        section(("Find…", "win.find"))))
        bar.append_submenu("View", menu(section(("Reload Page", "win.reload")),
                                        section(("Zoom In", "win.zoom-in"), ("Zoom Out", "win.zoom-out"),
                                                ("Actual Size", "win.zoom-reset"))))
        bar.append_submenu("History", menu(section(("Back", "win.back"), ("Forward", "win.forward"),
                                                   ("Start Page", "win.home"))))
        self.bookmarks_menu = Gio.Menu()
        bar.append_submenu("Bookmarks", self.bookmarks_menu)
        bar.append_submenu("Window", menu(section(("Show Previous Tab", "win.previous-tab"),
                                                  ("Show Next Tab", "win.next-tab"))))
        self.set_menubar(bar)
        self.rebuild_bookmarks_menu()
        for action, keys in (("app.new-window", ["<Primary>n"]), ("app.new-private-window", ["<Primary><Shift>n"]),
                             ("win.new-tab", ["<Primary>t"]), ("win.close-tab", ["<Primary>w"]),
                             ("win.open-location", ["<Primary>l"]), ("win.reload", ["<Primary>r", "F5"]),
                             ("win.back", ["<Primary>bracketleft", "<Alt>Left"]),
                             ("win.forward", ["<Primary>bracketright", "<Alt>Right"]),
                             ("win.home", ["<Primary><Shift>h"]), ("win.find", ["<Primary>f"]),
                             ("win.zoom-in", ["<Primary>plus", "<Primary>equal"]), ("win.zoom-out", ["<Primary>minus"]),
                             ("win.zoom-reset", ["<Primary>0"]), ("win.add-bookmark", ["<Primary>d"]),
                             ("win.next-tab", ["<Primary>Tab", "<Primary>Page_Down"]),
                             ("win.previous-tab", ["<Primary><Shift>Tab", "<Primary>Page_Up"]),
                             ("app.quit", ["<Primary>q"])):
            self.set_accels_for_action(action, keys)

    def rebuild_bookmarks_menu(self):
        m = self.bookmarks_menu
        m.remove_all()
        top = Gio.Menu()
        top.append("Add Bookmark", "win.add-bookmark")
        m.append_section(None, top)
        marks = Gio.Menu()
        for title, uri in self.cfg["bookmarks"][:40]:
            item = Gio.MenuItem.new(title, None)
            item.set_action_and_target_value("win.open-bookmark", GLib.Variant("s", uri))
            marks.append_item(item)
        m.append_section(None, marks)

    def window(self, urls=None, private=False):
        w = BrowserWindow(self, private)
        for i, u in enumerate(urls or [HOME]):
            w.new_tab(to_url(u), select=(i == 0))
        w.present()
        return w

    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        private = "--private" in args
        urls = [a for a in args if not a.startswith("--")]
        urls = [Gio.File.new_for_commandline_arg_and_cwd(u, cl.get_cwd()).get_uri()
                if not u.startswith(("http:", "https:", "file:", "about:")) and os.path.exists(u) else u for u in urls]
        wins = [w for w in self.get_windows() if isinstance(w, BrowserWindow) and not w.private]
        if urls and wins and not private:
            for u in urls:
                wins[0].new_tab(to_url(u))
            wins[0].present()
        else:
            self.window(urls, private)
        return 0


def main(argv=None):
    GLib.set_prgname("slope-browser")
    GLib.set_application_name("Browser")
    return BrowserApp().run(sys.argv if argv is None else argv)
