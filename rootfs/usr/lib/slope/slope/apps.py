"""Tracks running applications and their windows (via libwnck)."""
import gi

gi.require_version("Wnck", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import GdkX11, Gio, GLib, GObject, Wnck  # noqa: E402

from . import keepalive  # noqa: E402
from .xutil import X11  # noqa: E402
from .common import SHELL_CLASS, AppIndex, desktop_key, friendly_name, launch  # noqa: E402

_GRAVEYARD = []

APP_TYPES = (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG, Wnck.WindowType.UTILITY)


def is_live():
    """True when running from the live USB/DVD (casper)."""
    try:
        with open("/proc/cmdline") as f:
            return "boot=casper" in f.read()
    except OSError:
        return False


class AppGroup:
    def __init__(self, key, appinfo=None, name=None, pinned=False):
        self.key = key
        self.appinfo = appinfo
        self.pinned = pinned
        self.windows = []
        self.kept_pids = set()     # processes that run on with no windows
        self._name = name

    @property
    def name(self):
        if self.appinfo is not None:
            return friendly_name(self.appinfo)
        return friendly_name(None, self._name or self.key)

    @property
    def running(self):
        return bool(self.windows) or bool(self.kept_pids)


class Apps(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "active-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "app-launched": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.index = AppIndex()
        self.x = X11()
        Wnck.set_client_type(Wnck.ClientType.PAGER)
        try:
            Wnck.set_default_icon_size(96)
        except AttributeError:
            pass
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        self._keycache = {}
        self.last_app_key = None
        self._queued = None
        self.screen.connect("window-opened", self._on_opened)
        self.screen.connect("window-closed", self._on_closed)
        self.screen.connect("active-window-changed", self._on_active)
        for w in self.screen.get_windows():
            self._watch(w)
        # apps that keep running with no windows (keepalive.py)
        self._kept = frozenset()
        self._seen = []
        self._kept_info = {}
        try:
            os_dir = Gio.File.new_for_path(keepalive.DIR)
            os_dir.make_directory_with_parents(None)
        except GLib.Error:
            pass
        try:
            self._kept_mon = Gio.File.new_for_path(keepalive.DIR).monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            self._kept_mon.connect("changed", lambda *_: self._check_kept())
        except GLib.Error:
            self._kept_mon = None
        GLib.timeout_add_seconds(3, self._check_kept)    # also catches crashed apps
        self._check_kept()

    # -- window bookkeeping ------------------------------------------------
    def _watch(self, w):
        w.connect("state-changed", self._on_state)
        w.connect("class-changed", lambda *_: self._forget(w))

    def _on_state(self, w, changed, new):
        # A "closed" (hidden) window that comes back by any other route than
        # the Dock -- the app opening it again itself, a second launch of a
        # single-instance app, Alt+Tab... -- is a normal window again.
        if changed & Wnck.WindowState.MINIMIZED and not new & Wnck.WindowState.MINIMIZED:
            xid = w.get_xid()
            try:
                if self.is_hidden(w):
                    self.x.unhide(xid)
                    self.x.flush()
                if self.x.is_closed(xid):
                    # it has just reopened with the "open" animation; from now
                    # on the yellow button minimizes it into the Dock again
                    GLib.timeout_add(900, self._clear_closed, xid)
            except Exception:
                pass
        self._queue()

    def _clear_closed(self, xid):
        try:
            self.x.clear_closed(xid)
            self.x.flush()
        except Exception:
            pass
        return False

    def _forget(self, w):
        self._keycache.pop(w.get_xid(), None)
        self._queue()

    def _on_opened(self, screen, w):
        self._watch(w)
        self._keycache.pop(w.get_xid(), None)
        self._queue()

    def _on_closed(self, screen, w):
        # libwnck crashes if a closed window's object is finalised from Python
        # later on (e.g. when a menu that referenced it is garbage-collected).
        # Keep closed windows alive for the life of the shell; they are small.
        _GRAVEYARD.append(w)
        self._keycache.pop(w.get_xid(), None)
        self._queue()

    def _check_kept(self):
        info = keepalive.apps()
        if frozenset(info) != self._kept:
            self._kept = frozenset(info)
            self._kept_info = info
            self._queue()
        return True

    def _kept_appinfo(self, info):
        d = info.get("desktop")
        if d:
            try:
                a = Gio.DesktopAppInfo.new_from_filename(d)
                if a is not None:
                    hit = self.index.get(a.get_id())
                    if hit is not None:
                        return hit
            except TypeError:
                pass
        app_id = info.get("id") or ""
        hit = self.index.get(app_id + ".desktop") if app_id else None
        if hit is None:
            exe = (info.get("exe") or "").rsplit("/", 1)[-1]
            hit = self.index.match(app_id, exe)
        return hit

    def _queue(self):
        if self._queued is None:
            self._queued = GLib.timeout_add(40, self._emit_changed)

    def _emit_changed(self):
        self._queued = None
        self.emit("changed")
        return False

    def _on_active(self, screen, prev):
        w = screen.get_active_window()
        if w is not None and self.is_app_window(w):
            self.last_app_key = self.key_for(w)[0]
        self.emit("active-changed")

    def is_app_window(self, w):
        if w.get_window_type() not in APP_TYPES:
            return False
        cls = (w.get_class_group_name() or "")
        if cls == SHELL_CLASS:
            return False
        if w.is_skip_tasklist() and not self.is_hidden(w):
            return False
        return True

    def is_hidden(self, w):
        return self.x.is_hidden(w.get_xid())

    def key_for(self, w):
        xid = w.get_xid()
        if xid in self._keycache:
            return self._keycache[xid]
        info = self.index.match(w.get_class_group_name(), w.get_class_instance_name())
        if info is not None:
            res = (info.get_id(), info)
        else:
            cls = (w.get_class_group_name() or w.get_class_instance_name() or "pid%d" % w.get_pid())
            res = ("wm:" + cls.lower(), None)
        self._keycache[xid] = res
        return res

    # -- groups --------------------------------------------------------------
    def groups(self):
        pinned_ids = list(self.settings.get("dock_apps", []))
        if is_live() and "slope-install.desktop" not in pinned_ids:
            pinned_ids.insert(0, "slope-install.desktop")
        groups = {}
        order = []
        for app_id in pinned_ids:
            info = self.index.get(app_id)
            if info is None:
                continue
            g = AppGroup(info.get_id(), info, pinned=True)
            groups[g.key] = g
            order.append(g)
        for w in self.screen.get_windows_stacked():
            if not self.is_app_window(w):
                continue
            key, info = self.key_for(w)
            g = groups.get(key)
            if g is None:
                g = AppGroup(key, info, name=w.get_class_group_name())
                g.icon_window = w
                groups[key] = g
                order.append(g)
            g.windows.append(w)
        # kept-alive apps whose windows are all closed are still running
        window_pids = {w.get_pid() for g in order for w in g.windows}
        for pid, info in self._kept_info.items():
            if pid in window_pids:
                for g in order:
                    if any(w.get_pid() == pid for w in g.windows):
                        g.kept_pids.add(pid)
                continue
            appinfo = self._kept_appinfo(info)
            if appinfo is None or not appinfo.should_show():
                continue        # background services, not apps
            key = appinfo.get_id() if appinfo is not None else "kept:" + (info.get("id") or str(pid))
            g = groups.get(key)
            if g is None:
                g = AppGroup(key, appinfo, name=info.get("id"))
                groups[key] = g
                order.append(g)
            g.kept_pids.add(pid)
            g.kept_info = info
        # running (unpinned) apps keep their Dock place, in launch order, even
        # while they have no windows -- icons must not jump around
        running = [g.key for g in order if not g.pinned]
        self._seen = [k for k in self._seen if k in running]
        self._seen += [k for k in running if k not in self._seen]
        rank = {k: i for i, k in enumerate(self._seen)}
        pinned = [g for g in order if g.pinned]
        return pinned + sorted((g for g in order if not g.pinned), key=lambda g: rank[g.key])

    def group_for_window(self, w):
        if w is None:
            return None
        key = self.key_for(w)[0]
        for g in self.groups():
            if g.key == key:
                return g
        return None

    def group_by_key(self, key):
        for g in self.groups():
            if g.key == key:
                return g
        return None

    def active_group(self):
        w = self.screen.get_active_window()
        if w is not None and self.is_app_window(w):
            self.last_app_key = self.key_for(w)[0]
            return self.group_for_window(w)
        # macOS keeps the last app frontmost even when its last window closed
        if self.last_app_key:
            g = self.group_by_key(self.last_app_key)
            if g is not None and g.running:
                return g
        return None

    # -- actions -------------------------------------------------------------
    def now(self, gdk_window=None):
        try:
            if gdk_window is not None:
                return GdkX11.x11_get_server_time(gdk_window)
        except Exception:
            pass
        return GLib.get_monotonic_time() // 1000 & 0xFFFFFFFF

    def visible_windows(self, g):
        return [w for w in g.windows if not w.is_minimized()]

    def activate_group(self, g, ts, launch_if_empty=True):
        """Dock click: bring the app forward, un-hiding closed windows."""
        if not g.windows:
            if g.kept_pids:
                self.reopen(g)          # running with no windows: new window
            elif launch_if_empty and g.appinfo is not None:
                self.launch(g)
            return
        visible = self.visible_windows(g)
        if not visible:
            hidden = [w for w in g.windows if self.is_hidden(w)]
            targets = hidden or [g.windows[-1]]
            for w in targets:
                self.x.unhide(w.get_xid())
            self.x.flush()
            for w in targets:
                w.activate(ts)
            return
        # focus the app's topmost window (windows are in stacking order)
        visible[-1].activate(ts)

    def show_window(self, w, ts):
        if self.is_hidden(w):
            self.x.unhide(w.get_xid())
            self.x.flush()
        w.activate(ts)

    def reopen(self, g):
        """Open a new window of an app that runs with no windows (Mac-style).
        Starting it again does it: GLib hands that over to the running copy."""
        if g.appinfo is not None and launch(g.appinfo):
            return True
        info = getattr(g, "kept_info", {})
        if info.get("id") and info.get("path"):
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call(info["id"], info["path"], "org.freedesktop.Application", "Activate",
                         GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NONE, -1, None, None)
                return True
            except GLib.Error:
                pass
        return False

    def quit_group(self, g):
        pids = set(g.kept_pids) | {w.get_pid() for w in g.windows}
        for pid in pids:
            if keepalive.is_kept(pid):
                keepalive.request_quit(pid)
        for w in list(g.windows):
            self.x.close(w.get_xid())
        self.x.flush()
        if self.last_app_key == g.key:
            self.last_app_key = None

    def hide_group(self, g):
        for w in g.windows:
            if w.get_window_type() == Wnck.WindowType.NORMAL:
                self.x.hide(w.get_xid())
        self.x.flush()

    def unhide_all(self, ts):
        for w in self.screen.get_windows():
            if self.is_hidden(w):
                self.x.unhide(w.get_xid())
                w.unminimize(ts)
        self.x.flush()

    def launch(self, g):
        if g.appinfo is not None and launch(g.appinfo):
            self.emit("app-launched", g.key)
            return True
        return False

    def pin(self, g, pinned):
        ids = list(self.settings.get("dock_apps", []))
        if pinned and g.appinfo is not None and g.appinfo.get_id() not in ids:
            ids.append(g.appinfo.get_id())
        elif not pinned and g.appinfo is not None:
            ids = [i for i in ids if desktop_key(i) != desktop_key(g.appinfo.get_id())]
        self.settings["dock_apps"] = ids
        from .common import update_setting
        update_setting("dock_apps", ids)
        self._queue()
