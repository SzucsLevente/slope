"""Slope App Store.

Browse, search, install, update and remove apps.  Snap-free: everything is a
normal .deb from Ubuntu's archive (Firefox and Thunderbird from Mozilla).

* The catalogue is Ubuntu's AppStream data (names, descriptions, categories,
  screenshots), which apt downloads together with the package lists.
* Installing / removing / updating runs /usr/lib/slope/slope-store-helper as
  root through pkexec (asks for your password once, then remembers it for a
  few minutes).  Refreshing the software list needs no password.
* Every icon is drawn on Slope's glossy tile.
"""
import hashlib
import html
import os
import re
import subprocess
import threading
import time
import urllib.request

import gi

gi.require_version("AppStream", "1.0")
from gi.repository import AppStream, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from .common import launch, letter_icon  # noqa: E402

HELPER = "/usr/lib/slope/slope-store-helper"
REFRESH = "/usr/lib/slope/slope-store-refresh"
CACHE = os.path.join(GLib.get_user_cache_dir(), "slope-store")

CATEGORIES = [
    ("Network", "Internet", "applications-internet", "#3d8ff0"),
    ("AudioVideo", "Music & Video", "applications-multimedia", "#ff5f7a"),
    ("Graphics", "Graphics & Photos", "applications-graphics", "#ff9f2e"),
    ("Office", "Productivity", "applications-office", "#28b850"),
    ("Game", "Games", "applications-games", "#a35cf0"),
    ("Development", "Developer Tools", "applications-development", "#5a6170"),
    ("Education", "Education", "applications-education", "#f5c400"),
    ("Science", "Science", "applications-science", "#18b5c9"),
    ("Utility", "Utilities", "applications-utilities", "#8a94a8"),
    ("System", "System", "applications-system", "#4b5468"),
]

# hand-picked for the Discover page (package names; only those available are shown)
FEATURED = ["firefox", "thunderbird", "gimp", "inkscape", "vlc", "libreoffice-writer", "audacity",
            "blender", "obs-studio", "kdenlive", "krita", "shotwell", "rhythmbox", "celluloid",
            "transmission-gtk", "steam-installer", "gnome-mines", "supertuxkart", "simple-scan", "gnome-calculator",
            "geany", "darktable", "handbrake", "lollypop", "foliate"]

# apps that only come from an extra repository Slope sets up (not in Ubuntu's catalogue)
EXTRAS = [
    dict(id="org.mozilla.firefox", name="Firefox", pkg="firefox", stock="firefox",
         desktop="firefox.desktop", cats=["Network", "WebBrowser"], dev="Mozilla",
         summary="Fast, private and free web browser",
         homepage="https://www.mozilla.org/firefox/",
         desc="Firefox is a fast, private and free web browser from Mozilla.\n\n"
              "This is the real Firefox .deb from Mozilla's own repository: no snap, so it "
              "follows the Slope theme and icons.",
         needs="/etc/apt/sources.list.d/mozilla.sources"),
    dict(id="org.mozilla.Thunderbird", name="Thunderbird", pkg="thunderbird", stock="thunderbird",
         desktop="thunderbird.desktop", cats=["Network", "Office", "Email"], dev="Mozilla",
         summary="Email, calendar and contacts",
         homepage="https://www.thunderbird.net/",
         desc="Thunderbird is a free email application that is easy to set up and customise, "
              "with calendar, contacts and chat.\n\nInstalled from Mozilla's own repository, no snap.",
         needs="/etc/apt/sources.list.d/mozilla.sources"),
]

CSS = b"""
.store-toolbar { padding: 8px 12px; border-bottom: 1px solid rgba(0,0,0,0.18);
  background-image: linear-gradient(to bottom, #fbfbfc, #e6e7ea); }
.store-page { background-color: #f3f4f6; }
.store-hero { border-radius: 14px; padding: 28px 32px;
  background-image: linear-gradient(135deg, #2b7cf5 0%, #5ab4ff 55%, #8fe3ff 100%);
  box-shadow: inset 0 1px rgba(255,255,255,0.6), 0 6px 18px rgba(30,80,160,0.25); }
.store-hero label { color: white; text-shadow: 0 1px 2px rgba(0,0,0,0.35); }
.store-card { background-color: white; border-radius: 14px; padding: 12px;
  border: 1px solid rgba(0,0,0,0.08); box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
.store-section { font-size: 15pt; font-weight: bold; color: #1d1f24; }
.store-summary { color: #6b707a; }
.store-cat { border-radius: 14px; padding: 18px; }
.store-cat label { color: white; font-weight: bold; font-size: 12pt;
  text-shadow: 0 1px 2px rgba(0,0,0,0.35); }
.store-pill { border-radius: 14px; padding: 2px 14px; min-height: 0; font-weight: bold; }
.store-title { font-size: 22pt; font-weight: bold; }
.store-dim { color: #6b707a; }
flowboxchild { padding: 0; }
flowboxchild:selected, flowboxchild:focus { background: none; outline: none; }
"""


def desc_text(markup):
    """AppStream's description markup (<p>, <ul><li>) as plain text."""
    if not markup:
        return ""
    s = re.sub(r"\s+", " ", markup)
    s = re.sub(r"<li>\s*", "\n  •  ", s)
    s = re.sub(r"</p>\s*|</ul>\s*|</ol>\s*", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def snap_blocked():
    """Packages Slope never installs (snap wrappers), from the apt pin file."""
    out = set()
    if not os.path.exists("/etc/apt/sources.list.d/mozilla.sources"):
        out.update(("firefox", "thunderbird"))      # Ubuntu's are snap wrappers
    try:
        with open("/etc/apt/preferences.d/no-snap.pref") as f:
            for line in f:
                if line.startswith("Package:"):
                    out.update(line.split(":", 1)[1].split())
    except OSError:
        pass
    return out


class App:
    def __init__(self, comp=None, **kw):
        self.comp = comp
        if comp is not None:
            self.id = comp.get_id()
            self.name = comp.get_name() or self.id
            self.summary = comp.get_summary() or ""
            self.pkg = comp.get_pkgname()
            self.cats = list(comp.get_categories() or [])
            self.stock = None
            self.remote = None
            best = 0
            for ic in comp.get_icons() or []:
                k = ic.get_kind()
                if k == AppStream.IconKind.STOCK and not self.stock:
                    self.stock = ic.get_name()
                elif k == AppStream.IconKind.REMOTE and ic.get_width() >= best:
                    best, self.remote = ic.get_width(), ic.get_url()
            self.desktop = None
            for lc in comp.get_launchables() or []:
                if lc.get_kind() == AppStream.LaunchableKind.DESKTOP_ID and lc.get_entries():
                    self.desktop = lc.get_entries()[0]
                    break
        else:
            self.id = kw["id"]
            self.name = kw["name"]
            self.summary = kw["summary"]
            self.pkg = kw["pkg"]
            self.cats = kw["cats"]
            self.stock = kw.get("stock")
            self.remote = None
            self.desktop = kw.get("desktop")
            self._extra = kw

    # details, looked up only when the app's page is opened
    def description(self):
        if self.comp is None:
            return self._extra.get("desc", "")
        return desc_text(self.comp.get_description())

    def developer(self):
        if self.comp is None:
            return self._extra.get("dev", "")
        dev = self.comp.get_developer()
        return dev.get_name() if dev is not None and dev.get_name() else ""

    def homepage(self):
        if self.comp is None:
            return self._extra.get("homepage")
        return self.comp.get_url(AppStream.UrlKind.HOMEPAGE)

    def screenshot_url(self):
        if self.comp is None:
            return None
        shots = self.comp.get_screenshots_all() or []
        for shot in shots:
            imgs = shot.get_images_all() if hasattr(shot, "get_images_all") else shot.get_images()
            best = None
            for im in imgs or []:
                w = im.get_width()
                if w and w <= 1300 and (best is None or w > best.get_width()):
                    best = im
            if best is not None:
                return best.get_url()
        return None

    def score(self):
        """Rough "nice to show first" score (the catalogue has no popularity data)."""
        s = 0
        if self.remote or self.stock:
            s += 2
        if self.comp is not None and self.comp.get_screenshots_all():
            s += 2
        if self.comp is not None and len(self.comp.get_description() or "") > 200:
            s += 1
        return s


class Catalog:
    def __init__(self):
        self.apps = []
        self.by_id = {}
        self.by_pkg = {}
        self.pool = None
        self.installed = {}      # pkg -> version
        self.updates = []        # (pkg, old, new)

    def load(self):
        pool = AppStream.Pool()
        try:
            pool.load()
        except GLib.Error as e:
            print("slope-store: catalogue:", e.message)
        blocked = snap_blocked()
        apps, by_id, by_pkg = [], {}, {}
        for extra in EXTRAS:
            if extra.get("needs") and not os.path.exists(extra["needs"]):
                continue
            a = App(**extra)
            apps.append(a)
            by_id[a.id] = a
            by_pkg[a.pkg] = a
        box = pool.get_components_by_kind(AppStream.ComponentKind.DESKTOP_APP)
        for i in range(box.get_size()):
            c = box.index_safe(i)
            pkg = c.get_pkgname()
            if not pkg or pkg in blocked or pkg in by_pkg:
                continue
            a = App(c)
            apps.append(a)
            by_id[a.id] = a
            by_pkg[pkg] = a
        apps.sort(key=lambda a: a.name.lower())
        self.pool, self.apps, self.by_id, self.by_pkg = pool, apps, by_id, by_pkg
        self.load_installed()
        self.load_updates()

    def load_installed(self):
        inst = {}
        try:
            out = subprocess.run(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\t${Version}\n"],
                                 capture_output=True, text=True, timeout=20).stdout
            for line in out.splitlines():
                p = line.split("\t")
                if len(p) == 3 and p[1].startswith("ii"):
                    inst[p[0]] = p[2]
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.installed = inst

    def load_updates(self):
        ups = []
        try:
            out = subprocess.run(["apt", "list", "--upgradable"], capture_output=True, text=True,
                                 timeout=30, env=dict(os.environ, LC_ALL="C")).stdout
            for line in out.splitlines():
                m = re.match(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
                if m:
                    ups.append((m.group(1), m.group(3), m.group(2)))
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.updates = ups

    def search(self, text):
        text = text.strip()
        if not text:
            return []
        found = []
        seen = set()
        low = text.lower()
        for a in self.apps:          # name matches first
            if low in a.name.lower():
                found.append(a)
                seen.add(a.id)
        if self.pool is not None:
            res = self.pool.search(text)
            for i in range(res.get_size()):
                a = self.by_id.get(res.index_safe(i).get_id())
                if a is not None and a.id not in seen:
                    found.append(a)
                    seen.add(a.id)
        return found

    def in_category(self, cat):
        return sorted((a for a in self.apps if cat in a.cats), key=lambda a: (-a.score(), a.name.lower()))

    def featured(self):
        return [self.by_pkg[p] for p in FEATURED if p in self.by_pkg]

    def is_installed(self, app):
        return app.pkg in self.installed


class Icons:
    """Glossy app icons: from the icon theme, else downloaded, else initials."""

    def __init__(self):
        self.theme = Gtk.IconTheme.get_default()
        self.mem = {}
        self.waiting = {}            # key -> [Gtk.Image]
        os.makedirs(os.path.join(CACHE, "icons"), exist_ok=True)

    def _gloss(self, pb, size):
        from .icons import glossify
        return Gdk.pixbuf_get_from_surface(glossify(pb, size), 0, 0, size, size)

    def image(self, app, size):
        img = Gtk.Image()
        self.set(img, app, size)
        return img

    def set(self, img, app, size):
        key = (app.id, size)
        if key in self.mem:
            img.set_from_pixbuf(self.mem[key])
            return
        pb = None
        if app.stock:
            info = self.theme.lookup_icon(app.stock, 128, 0)
            if info is not None:
                try:
                    src = info.load_icon()
                    fn = info.get_filename() or ""
                    if "/icons/Slope/" in fn:          # already a Slope tile
                        pb = src.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    else:
                        pb = self._gloss(src, size)
                except GLib.Error:
                    pb = None
        if pb is None and app.remote:
            path = os.path.join(CACHE, "icons", hashlib.sha1(app.remote.encode()).hexdigest() + ".png")
            if os.path.exists(path):
                try:
                    pb = self._gloss(GdkPixbuf.Pixbuf.new_from_file(path), size)
                except GLib.Error:
                    pb = None
            else:
                img.set_from_pixbuf(letter_icon(app.name, size))
                self.waiting.setdefault(key, []).append(img)
                if len(self.waiting[key]) == 1:
                    threading.Thread(target=self._fetch, args=(app, size, path), daemon=True).start()
                return
        if pb is None:
            pb = letter_icon(app.name, size)
        self.mem[key] = pb
        img.set_from_pixbuf(pb)

    def _fetch(self, app, size, path):
        try:
            with urllib.request.urlopen(app.remote, timeout=15) as r:
                data = r.read()
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
        except Exception:
            pass
        GLib.idle_add(self._fetched, app, size, path)

    def _fetched(self, app, size, path):
        key = (app.id, size)
        pb = None
        if os.path.exists(path):
            try:
                pb = self._gloss(GdkPixbuf.Pixbuf.new_from_file(path), size)
            except GLib.Error:
                pb = None
        pb = pb or letter_icon(app.name, size)
        self.mem[key] = pb
        for img in self.waiting.pop(key, []):
            img.set_from_pixbuf(pb)
        return False


class Operations:
    """Runs one helper command at a time, reporting progress."""

    def __init__(self, store):
        self.store = store
        self.queue = []
        self.current = None
        self.progress = {}           # pkg -> (percent, text)

    def busy(self, pkg):
        if self.current and pkg in self.current["pkgs"]:
            return True
        return any(pkg in op["pkgs"] for op in self.queue)

    def add(self, kind, pkgs=(), done=None):
        op = dict(kind=kind, pkgs=list(pkgs), done=done, error=None)
        self.queue.append(op)
        for p in op["pkgs"]:
            self.progress[p] = (0.0, "Waiting…")
        self.store.op_changed(op)
        if self.current is None:
            self._next()

    def _next(self):
        if not self.queue:
            self.current = None
            return
        op = self.current = self.queue.pop(0)
        argv = ["pkexec", REFRESH] if op["kind"] == "refresh" else ["pkexec", HELPER, op["kind"]] + op["pkgs"]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            op["error"] = e.message
            self._finish(op, None)
            return
        op["proc"] = proc
        stream = Gio.DataInputStream.new(proc.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, op, stream)

    def _line(self, stream, res, op, _stream):
        try:
            line, _len = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            op["proc"].wait_async(None, lambda p, r: self._finish(op, p))
            return
        import json
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if msg:
            if msg.get("type") == "progress":
                for p in op["pkgs"] or ["*"]:
                    self.progress[p] = (msg.get("percent", 0), msg.get("text", ""))
                self.store.op_changed(op)
            elif msg.get("type") == "error":
                op["error"] = msg.get("text")
            elif msg.get("type") == "done":
                op["ok"] = True
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, op, stream)

    def _finish(self, op, proc):
        if proc is not None and not op.get("ok") and not op.get("error"):
            status = proc.get_exit_status() if proc.get_if_exited() else -1
            op["error"] = ("You need to enter an administrator password to do this."
                           if status in (126, 127) else "Something went wrong (%d)." % status)
        for p in op["pkgs"]:
            self.progress.pop(p, None)
        self.progress.pop("*", None)
        self.current = None
        self.store.op_finished(op)
        if op.get("done"):
            op["done"](op)
        self._next()


class Store(Gtk.Window):
    def __init__(self):
        super().__init__(title="App Store")
        self.set_wmclass("slope-store", "Slope-store")
        self.set_icon_name("slope-store")
        self.set_default_size(1100, 740)
        self.set_position(Gtk.WindowPosition.CENTER)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.cat = Catalog()
        self.icons = Icons()
        self.ops = Operations(self)
        self.pills = {}              # pkg -> [(button, kind)]
        self.card_progress = {}      # pkg -> [(bar, status label, summary label)]
        self.history = []
        self.detail_app = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)
        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class("store-toolbar")
        box.pack_start(bar, False, False, 0)
        self.back = Gtk.Button(label="‹ Back")
        self.back.connect("clicked", lambda *_: self.go_back())
        self.back.set_no_show_all(True)
        bar.pack_start(self.back, False, False, 0)
        seg = Gtk.Box()
        seg.get_style_context().add_class("linked")
        self.tabs = {}
        group = None
        for name, label in (("discover", "Discover"), ("categories", "Categories"),
                            ("installed", "Installed"), ("updates", "Updates")):
            b = Gtk.RadioButton.new_with_label_from_widget(group, label)
            b.set_mode(False)
            group = group or b
            b.connect("toggled", self._tab_toggled, name)
            seg.pack_start(b, False, False, 0)
            self.tabs[name] = b
        bar.set_center_widget(seg)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search apps")
        self.search.set_size_request(240, -1)
        self.search.connect("search-changed", self._search_changed)
        bar.pack_end(self.search, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(160)
        box.pack_start(self.stack, True, True, 0)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        sp = Gtk.Spinner()
        sp.start()
        sp.set_size_request(32, 32)
        loading.pack_start(sp, False, False, 0)
        loading.pack_start(Gtk.Label(label="Loading the app catalogue…"), False, False, 0)
        self.stack.add_named(loading, "loading")
        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self._key)
        self.show_all()
        threading.Thread(target=self._load, daemon=True).start()

    # -- loading ---------------------------------------------------------------
    def _load(self):
        self.cat.load()
        GLib.idle_add(self._loaded)

    def _loaded(self):
        for name in ("discover", "categories", "installed", "updates", "list", "details"):
            old = self.stack.get_child_by_name(name)
            if old is not None:
                old.destroy()
        self.pills = {}
        self.card_progress = {}
        self.stack.add_named(self._page(self._discover()), "discover")
        self.stack.add_named(self._page(self._categories()), "categories")
        self.stack.add_named(self._page(Gtk.Box()), "installed")
        self.stack.add_named(self._page(Gtk.Box()), "updates")
        self.stack.add_named(self._page(Gtk.Box()), "list")
        self.stack.add_named(self._page(Gtk.Box()), "details")
        self.stack.show_all()
        self._update_badge()
        current = [n for n, b in self.tabs.items() if b.get_active()][0]
        if self.detail_app is not None:
            self.show_details(self.detail_app, push=False)
        else:
            self._show_tab(current)
        if not self.cat.apps:
            self._empty_catalogue()
        if not getattr(self, "_refreshed", False):
            self._refreshed = True
            if self._lists_stale():
                self.ops.add("refresh", done=lambda op: self.reload())
        return False

    def reload(self):
        threading.Thread(target=self._load, daemon=True).start()

    @staticmethod
    def _lists_stale():
        d = "/var/lib/apt/lists"
        try:
            pk = [os.path.join(d, n) for n in os.listdir(d) if "_Packages" in n]
        except OSError:
            return True
        if not pk:
            return True
        return time.time() - max(os.path.getmtime(p) for p in pk) > 6 * 3600

    def _empty_catalogue(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        lab = Gtk.Label()
        lab.set_markup("<span size='x-large' weight='bold'>Getting the app catalogue…</span>\n"
                       "This needs an internet connection the first time.")
        lab.set_justify(Gtk.Justification.CENTER)
        v.pack_start(lab, False, False, 0)
        self._set_page("discover", v)

    # -- page helpers -------------------------------------------------------------
    def _page(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.get_style_context().add_class("store-page")
        sw.add(child)
        child.set_margin_start(28)
        child.set_margin_end(28)
        child.set_margin_top(22)
        child.set_margin_bottom(28)
        return sw

    def _set_page(self, name, child):
        sw = self.stack.get_child_by_name(name)
        if sw is None:
            return
        for c in sw.get_children():
            sw.remove(c)
        child.set_margin_start(28)
        child.set_margin_end(28)
        child.set_margin_top(22)
        child.set_margin_bottom(28)
        sw.add(child)
        sw.show_all()
        sw.get_vadjustment().set_value(0)

    def _section(self, text):
        lab = Gtk.Label(label=text, xalign=0)
        lab.get_style_context().add_class("store-section")
        lab.set_margin_top(18)
        lab.set_margin_bottom(6)
        return lab

    def _grid(self, apps, limit=None):
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(4)
        flow.set_column_spacing(14)
        flow.set_row_spacing(14)
        for a in apps[:limit] if limit else apps:
            flow.add(self._card(a))
        flow.connect("child-activated", lambda f, child: self.show_details(child.app))
        return flow

    def _card(self, app):
        child = Gtk.FlowBoxChild()
        child.app = app
        ev = Gtk.EventBox()
        ev.connect("button-release-event", self._card_clicked, app)
        card = Gtk.Box(spacing=12)
        card.get_style_context().add_class("store-card")
        card.pack_start(self.icons.image(app, 64), False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        name = Gtk.Label(xalign=0)
        name.set_markup("<b>%s</b>" % GLib.markup_escape_text(app.name))
        name.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(name, False, False, 0)
        summ = Gtk.Label(label=app.summary, xalign=0)
        summ.set_line_wrap(True)
        summ.set_lines(2)
        summ.set_max_width_chars(26)
        summ.set_ellipsize(Pango.EllipsizeMode.END)
        summ.get_style_context().add_class("store-summary")
        text.pack_start(summ, False, False, 0)
        # progress while this app installs: a bar and what is happening
        bar = Gtk.ProgressBar()
        bar.set_no_show_all(True)
        status = Gtk.Label(xalign=0)
        status.set_no_show_all(True)
        status.set_ellipsize(Pango.EllipsizeMode.END)
        status.get_style_context().add_class("store-summary")
        text.pack_start(bar, False, False, 2)
        text.pack_start(status, False, False, 0)
        self.card_progress.setdefault(app.pkg, []).append((bar, status, summ))
        card.pack_start(text, True, True, 0)
        card.pack_end(self._pill(app), False, False, 0)
        ev.add(card)
        child.add(ev)
        return child

    def _card_clicked(self, ev, event, app):
        # a click on the GET / OPEN button is that button's, not the card's
        if isinstance(Gtk.get_event_widget(event), Gtk.Button):
            return False
        self.show_details(app)
        return True

    def _pill(self, app):
        b = Gtk.Button()
        b.get_style_context().add_class("store-pill")
        b.set_valign(Gtk.Align.CENTER)
        b.connect("clicked", lambda w: self._pill_clicked(app))
        self.pills.setdefault(app.pkg, []).append(b)
        self._update_pill(b, app)
        return b

    def _update_pill(self, b, app):
        ctx = b.get_style_context()
        ctx.remove_class("suggested-action")
        if app.pkg in self.ops.progress:
            pct, _t = self.ops.progress[app.pkg]
            b.set_label("%d%%" % pct if pct else "…")
            b.set_sensitive(False)
        elif self.cat.is_installed(app):
            b.set_label("OPEN")
            b.set_sensitive(True)
        else:
            b.set_label("GET")
            b.set_sensitive(True)
            ctx.add_class("suggested-action")

    def _refresh_pills(self, pkg=None):
        for p, buttons in self.pills.items():
            if pkg is not None and p != pkg:
                continue
            a = self.cat.by_pkg.get(p)
            if a is None:
                continue
            for b in buttons:
                self._update_pill(b, a)
        for p, rows in self.card_progress.items():
            if pkg is not None and p != pkg:
                continue
            prog = self.ops.progress.get(p)
            for bar, status, summ in rows:
                if prog is None:
                    bar.hide()
                    status.hide()
                    summ.show()
                    continue
                pct, text = prog
                bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
                status.set_text("%s  ·  %d%%" % (text or "Waiting…", pct) if pct else (text or "Waiting…"))
                summ.hide()
                bar.show()
                status.show()

    def _pill_clicked(self, app):
        if self.cat.is_installed(app):
            self.open_app(app)
        else:
            self.install(app)

    # -- pages ------------------------------------------------------------------------
    def _discover(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hero.get_style_context().add_class("store-hero")
        t = Gtk.Label(xalign=0)
        t.set_markup("<span size='xx-large' weight='bold'>Discover great apps</span>")
        hero.pack_start(t, False, False, 0)
        s = Gtk.Label(xalign=0)
        s.set_markup("<span size='large'>%d apps, all free, no snaps. One click to install.</span>"
                     % len(self.cat.apps))
        hero.pack_start(s, False, False, 0)
        v.pack_start(hero, False, False, 0)
        feat = self.cat.featured()
        if feat:
            v.pack_start(self._section("Editor's Choice"), False, False, 0)
            v.pack_start(self._grid(feat, 12), False, False, 0)
        for cat, label, _icon, _c in CATEGORIES[:6]:
            apps = [a for a in self.cat.in_category(cat) if a not in feat][:8]
            if not apps:
                continue
            head = Gtk.Box()
            head.pack_start(self._section("Top in %s" % label), False, False, 0)
            more = Gtk.LinkButton(label="See all")
            more.connect("activate-link", lambda w, c=cat, l=label: (self.show_category(c, l), True)[1])
            head.pack_end(more, False, False, 0)
            v.pack_start(head, False, False, 0)
            v.pack_start(self._grid(apps), False, False, 0)
        return v

    def _categories(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(self._section("Categories"), False, False, 0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_max_children_per_line(4)
        flow.set_column_spacing(14)
        flow.set_row_spacing(14)
        for cat, label, icon, color in CATEGORIES:
            n = len([a for a in self.cat.apps if cat in a.cats])
            if not n:
                continue
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            inner = Gtk.Box(spacing=12)
            inner.get_style_context().add_class("store-cat")
            prov = Gtk.CssProvider()
            prov.load_from_data((".store-cat { background-image: linear-gradient(135deg, %s, shade(%s, 0.75)); }"
                                 % (color, color)).encode())
            inner.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
            inner.pack_start(img, False, False, 0)
            lab = Gtk.Label(xalign=0)
            lab.set_markup("%s\n<small>%d apps</small>" % (GLib.markup_escape_text(label), n))
            inner.pack_start(lab, True, True, 0)
            b.add(inner)
            b.connect("clicked", lambda w, c=cat, l=label: self.show_category(c, l))
            flow.add(b)
        v.pack_start(flow, False, False, 0)
        return v

    def show_list(self, title, apps, push=True):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(self._section(title), False, False, 0)
        if not apps:
            v.pack_start(Gtk.Label(label="Nothing found.", xalign=0), False, False, 0)
        step = 60
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(holder, False, False, 0)

        def add_more(start):
            holder.pack_start(self._grid(apps[start:start + step]), False, False, 0)
            if start + step < len(apps):
                more = Gtk.Button(label="Show more (%d left)" % (len(apps) - start - step))
                more.set_halign(Gtk.Align.CENTER)

                def clicked(w):
                    w.destroy()
                    add_more(start + step)
                    holder.show_all()
                more.connect("clicked", clicked)
                holder.pack_start(more, False, False, 0)
        add_more(0)
        self._navigate("list", v, push)

    def show_category(self, cat, label):
        self.show_list(label, self.cat.in_category(cat))

    def _installed_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        v.pack_start(self._section("Installed apps"), False, False, 0)
        apps = [a for a in self.cat.apps if self.cat.is_installed(a)]
        if not apps:
            v.pack_start(Gtk.Label(label="No apps from the store yet.", xalign=0), False, False, 0)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("store-card")
        for a in apps:
            row = Gtk.Box(spacing=12)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            row.pack_start(self.icons.image(a, 48), False, False, 0)
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<b>%s</b>\n<small>%s</small>" % (GLib.markup_escape_text(a.name),
                                                             GLib.markup_escape_text(a.summary)))
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(lab, True, True, 0)
            rm = Gtk.Button(label="Remove")
            rm.connect("clicked", lambda w, a=a: self.remove(a))
            row.pack_end(rm, False, False, 0)
            op = Gtk.Button(label="Open")
            op.connect("clicked", lambda w, a=a: self.open_app(a))
            row.pack_end(op, False, False, 0)
            ev = Gtk.EventBox()
            ev.add(row)
            ev.connect("button-release-event", lambda w, e, a=a: (self.show_details(a), False)[1])
            lb.add(ev)
        if apps:
            v.pack_start(lb, False, False, 0)
        return v

    def _updates_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box(spacing=12)
        head.pack_start(self._section("Updates"), False, False, 0)
        ups = self.cat.updates
        busy = self.ops.current is not None and self.ops.current["kind"] in ("refresh", "upgrade")
        if busy:
            pct, text = self.ops.progress.get("*", (0, "Working…"))
            bar = Gtk.ProgressBar()
            bar.set_fraction(pct / 100.0)
            bar.set_text(text)
            bar.set_show_text(True)
            bar.set_size_request(320, -1)
            bar.set_valign(Gtk.Align.CENTER)
            head.pack_end(bar, False, False, 0)
        elif ups:
            b = Gtk.Button(label="Update All")
            b.get_style_context().add_class("suggested-action")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda w: self.ops.add("upgrade", done=lambda op: self.reload()))
            head.pack_end(b, False, False, 0)
        else:
            b = Gtk.Button(label="Check for Updates")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda w: self.ops.add("refresh", done=lambda op: self.reload()))
            head.pack_end(b, False, False, 0)
        v.pack_start(head, False, False, 0)
        if not ups:
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<span size='large'>Everything is up to date.</span>")
            lab.get_style_context().add_class("store-dim")
            v.pack_start(lab, False, False, 0)
            return v
        apps = [self.cat.by_pkg[p] for p, _o, _n in ups if p in self.cat.by_pkg]
        others = len(ups) - len(apps)
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("store-card")
        for a in apps:
            row = Gtk.Box(spacing=12)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            row.pack_start(self.icons.image(a, 48), False, False, 0)
            new = [n for p, _o, n in ups if p == a.pkg][0]
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<b>%s</b>\n<small>Version %s</small>" % (GLib.markup_escape_text(a.name),
                                                                     GLib.markup_escape_text(new)))
            row.pack_start(lab, True, True, 0)
            lb.add(row)
        if others:
            row = Gtk.Box(spacing=12)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            img = Gtk.Image.new_from_icon_name("slope-settings", Gtk.IconSize.DIALOG)
            row.pack_start(img, False, False, 0)
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<b>System updates</b>\n<small>%d system component%s (security fixes and "
                           "improvements)</small>" % (others, "" if others == 1 else "s"))
            row.pack_start(lab, True, True, 0)
            lb.add(row)
        v.pack_start(lb, False, False, 0)
        return v

    def show_details(self, app, push=True):
        self.detail_app = app
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        top = Gtk.Box(spacing=22)
        top.pack_start(self.icons.image(app, 128), False, False, 0)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=app.name, xalign=0)
        name.get_style_context().add_class("store-title")
        info.pack_start(name, False, False, 0)
        summ = Gtk.Label(label=app.summary, xalign=0)
        summ.set_line_wrap(True)
        info.pack_start(summ, False, False, 0)
        dev = app.developer()
        if dev:
            d = Gtk.Label(label=dev, xalign=0)
            d.get_style_context().add_class("store-dim")
            info.pack_start(d, False, False, 0)
        buttons = Gtk.Box(spacing=8)
        buttons.set_margin_top(8)
        self.detail_buttons = buttons
        info.pack_start(buttons, False, False, 0)
        self.detail_bar = Gtk.ProgressBar()
        self.detail_bar.set_show_text(True)
        self.detail_bar.set_no_show_all(True)
        info.pack_start(self.detail_bar, False, False, 0)
        top.pack_start(info, True, True, 0)
        v.pack_start(top, False, False, 0)
        self._fill_detail_buttons()

        shot_url = app.screenshot_url()
        if shot_url:
            shot = Gtk.Image()
            shot.set_halign(Gtk.Align.START)
            spinner = Gtk.Spinner()
            spinner.start()
            holder = Gtk.Box()
            holder.pack_start(spinner, False, False, 0)
            holder.pack_start(shot, False, False, 0)
            v.pack_start(holder, False, False, 0)
            threading.Thread(target=self._fetch_shot, args=(shot_url, shot, spinner), daemon=True).start()
        desc = app.description()
        if desc:
            d = Gtk.Label(label=desc, xalign=0)
            d.set_line_wrap(True)
            d.set_max_width_chars(90)
            d.set_selectable(True)
            v.pack_start(d, False, False, 0)
        facts = Gtk.Label(xalign=0)
        facts.get_style_context().add_class("store-dim")
        v.pack_start(facts, False, False, 0)
        threading.Thread(target=self._fetch_facts, args=(app, facts), daemon=True).start()
        home = app.homepage()
        if home:
            link = Gtk.LinkButton.new_with_label(home, "Website")
            link.set_halign(Gtk.Align.START)
            v.pack_start(link, False, False, 0)
        self._navigate("details", v, push)

    def _fill_detail_buttons(self):
        app = self.detail_app
        box = self.detail_buttons
        for c in box.get_children():
            c.destroy()
        prog = self.ops.progress.get(app.pkg)
        if prog is not None:
            self.detail_bar.show()
            self.detail_bar.set_fraction(prog[0] / 100.0)
            self.detail_bar.set_text(prog[1] or "Waiting…")
        else:
            self.detail_bar.hide()
            if self.cat.is_installed(app):
                b = Gtk.Button(label="Open")
                b.get_style_context().add_class("suggested-action")
                b.connect("clicked", lambda w: self.open_app(app))
                box.pack_start(b, False, False, 0)
                r = Gtk.Button(label="Remove")
                r.connect("clicked", lambda w: self.remove(app))
                box.pack_start(r, False, False, 0)
            else:
                b = Gtk.Button(label="Install")
                b.get_style_context().add_class("suggested-action")
                b.connect("clicked", lambda w: self.install(app))
                box.pack_start(b, False, False, 0)
        box.show_all()

    def _fetch_shot(self, url, img, spinner):
        path = os.path.join(CACHE, "shots", hashlib.sha1(url.encode()).hexdigest())
        pb = None
        try:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with urllib.request.urlopen(url, timeout=20) as r:
                    data = r.read()
                with open(path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(path + ".tmp", path)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 760, 480, True)
        except Exception:
            pb = None

        def show():
            spinner.destroy()
            if pb is not None:
                img.set_from_pixbuf(pb)
            return False
        GLib.idle_add(show)

    def _fetch_facts(self, app, label):
        bits = []
        try:
            out = subprocess.run(["apt-cache", "show", "--no-all-versions", app.pkg], capture_output=True,
                                 text=True, timeout=15, env=dict(os.environ, LC_ALL="C")).stdout
            ver = re.search(r"^Version: (.+)$", out, re.M)
            size = re.search(r"^Installed-Size: (\d+)", out, re.M)
            if ver:
                bits.append("Version %s" % ver.group(1))
            if size:
                kb = int(size.group(1))
                bits.append("%.0f MB" % (kb / 1024.0) if kb > 1024 else "%d KB" % kb)
        except (OSError, subprocess.TimeoutExpired):
            pass
        bits.append("Package: %s" % app.pkg)
        GLib.idle_add(lambda: (label.set_text("   ·   ".join(bits)), False)[1])

    # -- navigation -----------------------------------------------------------------
    def _navigate(self, name, child, push):
        cur = self.stack.get_visible_child_name()
        if push and cur and cur != "loading":
            self.history.append((cur, self.detail_app if cur == "details" else None,
                                 getattr(self, "_list_state", None)))
        self._set_page(name, child)
        self.stack.set_visible_child_name(name)
        self.back.set_visible(bool(self.history))

    def go_back(self):
        if not self.history:
            return
        name, app, _state = self.history.pop()
        if name == "details" and app is not None:
            self.show_details(app, push=False)
        else:
            self.stack.set_visible_child_name(name)
        self.back.set_visible(bool(self.history))

    def _tab_toggled(self, button, name):
        if button.get_active():
            self.history = []
            self.back.hide()
            self.detail_app = None
            self._show_tab(name)

    def _show_tab(self, name):
        if self.stack.get_child_by_name(name) is None:
            return
        if name == "installed":
            self._set_page("installed", self._installed_page())
        elif name == "updates":
            self._set_page("updates", self._updates_page())
        self.stack.set_visible_child_name(name)

    def _search_changed(self, entry):
        text = entry.get_text()
        if getattr(self, "_search_id", None):
            GLib.source_remove(self._search_id)
        self._search_id = GLib.timeout_add(250, self._do_search, text)

    def _do_search(self, text):
        self._search_id = None
        if text.strip():
            self.show_list("Results for “%s”" % text.strip(), self.cat.search(text),
                           push=self.stack.get_visible_child_name() != "list")
        else:
            self.go_back()
        return False

    def _key(self, w, ev):
        if ev.keyval == Gdk.KEY_Escape and self.history:
            self.go_back()
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.search.grab_focus()
            return True
        return False

    # -- actions ----------------------------------------------------------------------
    def install(self, app):
        if self.ops.busy(app.pkg):
            return
        self.ops.add("install", [app.pkg])

    def remove(self, app):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text="Remove %s?" % app.name)
        dlg.format_secondary_text("The app is removed from this computer. Your own files are kept.")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Remove", Gtk.ResponseType.OK)
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        if ok and not self.ops.busy(app.pkg):
            self.ops.add("remove", [app.pkg])

    def open_app(self, app):
        ids = [app.desktop, app.id, app.id + ".desktop", app.pkg + ".desktop"]
        for i in ids:
            if not i:
                continue
            try:
                info = Gio.DesktopAppInfo.new(i)
            except TypeError:
                info = None
            if info is not None:
                launch(info)
                return
        # last resort: any .desktop file the package installed
        try:
            out = subprocess.run(["dpkg", "-L", app.pkg], capture_output=True, text=True, timeout=10).stdout
            for path in out.splitlines():
                if path.startswith("/usr/share/applications/") and path.endswith(".desktop"):
                    info = Gio.DesktopAppInfo.new_from_filename(path)
                    if info is not None:
                        launch(info)
                        return
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- operation callbacks ------------------------------------------------------------
    def op_changed(self, op):
        for p in op["pkgs"]:
            self._refresh_pills(p)
        if self.detail_app is not None and self.detail_app.pkg in op["pkgs"] \
                and self.stack.get_visible_child_name() == "details":
            self._fill_detail_buttons()
        if op["kind"] in ("refresh", "upgrade") and self.stack.get_visible_child_name() == "updates":
            self._set_page("updates", self._updates_page())

    def op_finished(self, op):
        self.cat.load_installed()
        if op["kind"] in ("upgrade", "install", "remove"):
            self.cat.load_updates()
        for p in op["pkgs"]:
            self._refresh_pills(p)
        name = self.stack.get_visible_child_name()
        if name == "details" and self.detail_app is not None:
            self._fill_detail_buttons()
        elif name in ("installed", "updates"):
            self._show_tab(name)
        self._update_badge()
        if op.get("error") and op["kind"] != "refresh":
            what = {"install": "install", "remove": "remove", "upgrade": "update"}[op["kind"]]
            app = self.cat.by_pkg.get(op["pkgs"][0]) if op["pkgs"] else None
            dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.OK,
                                    text="Could not %s %s" % (what, app.name if app else "the updates"))
            dlg.format_secondary_text(op["error"][-600:])
            dlg.run()
            dlg.destroy()

    def _update_badge(self):
        n = len(self.cat.updates)
        self.tabs["updates"].set_label("Updates (%d)" % n if n else "Updates")


def main(argv=None):
    import sys
    GLib.set_prgname("slope-store")
    app = Gio.Application(application_id="org.slope.Store", flags=Gio.ApplicationFlags.HANDLES_OPEN)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        app.activate()
        return 0
    win = Store()
    from . import keepalive
    keepalive.keep_window(win, "org.slope.Store", "/usr/share/applications/slope-store.desktop")
    app.connect("activate", lambda *_: win.present())
    Gtk.main()
    keepalive.unregister()
    return 0
