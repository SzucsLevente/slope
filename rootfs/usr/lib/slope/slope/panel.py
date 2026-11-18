"""The top bar: Slope menu, global application menu and status items."""
import glob
import os
import time

import cairo
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from . import art
from .common import have, logout, power, run_quiet, spawn, update_setting
from .dbusmenu import DBusMenuClient, Registrar, apply_live, build_menu_items

PANEL_H = 28


def cmd_item(label, accel=None, cb=None, sensitive=True):
    """A menu item with a macOS-style shortcut hint on the right."""
    mi = Gtk.MenuItem()
    box = Gtk.Box(spacing=24)
    l1 = Gtk.Label(label=label, xalign=0)
    l1.set_hexpand(True)
    box.pack_start(l1, True, True, 0)
    if accel:
        l2 = Gtk.Label(label=accel, xalign=1)
        l2.get_style_context().add_class("accelerator")
        box.pack_end(l2, False, False, 0)
    mi.add(box)
    mi.set_sensitive(sensitive)
    if cb is not None:
        mi.connect("activate", lambda *_: cb())
    return mi


def sep():
    return Gtk.SeparatorMenuItem()


def submenu_item(label, items, markup=False):
    mi = Gtk.MenuItem()
    lbl = Gtk.Label()
    if markup:
        lbl.set_markup(label)
    else:
        lbl.set_text_with_mnemonic(label) if "_" in label else lbl.set_text(label)
    mi.add(lbl)
    menu = Gtk.Menu()
    for it in items:
        menu.append(it)
    menu.show_all()
    mi.set_submenu(menu)
    return mi


def send_keys(combo):
    """Deliver a keyboard shortcut to the focused window after the menu closes."""
    def go():
        spawn(["xdotool", "key", "--clearmodifiers", combo])
        return False
    GLib.timeout_add(160, go)


def mark_image(size=16, rgba=(0.08, 0.08, 0.1, 1.0)):
    scale = 2
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * scale, size * scale)
    cr = cairo.Context(surf)
    art.draw_mark(cr, 0, 0, size * scale, rgba)
    pb = Gdk.pixbuf_get_from_surface(surf, 0, 0, size * scale, size * scale)
    pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)
    return Gtk.Image.new_from_pixbuf(pb)


class Panel(Gtk.Window):
    def __init__(self, shell):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.shell = shell
        self.apps = shell.apps
        self.set_wmclass("slope-panel", "Slope")
        self.set_title("slope-panel")
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
        self.get_style_context().add_class("slope-panel")
        self.connect("draw", self._draw)

        box = Gtk.Box()
        self.add(box)
        self.menubar = Gtk.MenuBar()
        self.menubar.get_style_context().add_class("slope-globalmenu")
        box.pack_start(self.menubar, False, False, 0)
        box.pack_start(Gtk.Box(), True, True, 0)
        self.status = Gtk.MenuBar()
        self.status.get_style_context().add_class("slope-status")
        box.pack_end(self.status, False, False, 4)

        self._client = None
        self._client_key = None
        self._gtk_key = None
        self._gmodel = None
        self._watched_models = set()
        self._rebuild_id = None
        self._current = None

        self.registrar = Registrar(lambda xid: self.queue_rebuild())
        self.apps.connect("active-changed", lambda *_: self.queue_rebuild())
        self.apps.connect("changed", lambda *_: self.queue_rebuild())

        self._build_status()
        self.rebuild()
        self.place()

    # -- geometry --------------------------------------------------------
    def place(self):
        geo = self.shell.monitor_geometry()
        self.move(geo.x, geo.y)
        self.set_size_request(geo.width, PANEL_H)
        self.resize(geo.width, PANEL_H)
        self.show_all()
        self.shell.set_strut(self, top=geo.y + PANEL_H, top_start=geo.x, top_end=geo.x + geo.width - 1)

    def _draw(self, w, cr):
        alloc = self.get_allocation()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        g = cairo.LinearGradient(0, 0, 0, alloc.height)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.82)
        g.add_color_stop_rgba(0.5, 0.96, 0.97, 0.99, 0.74)
        g.add_color_stop_rgba(0.51, 0.92, 0.94, 0.97, 0.70)
        g.add_color_stop_rgba(1, 0.95, 0.96, 0.99, 0.74)
        cr.set_source(g)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.rectangle(0, 0, alloc.width, 1)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.rectangle(0, alloc.height - 1, alloc.width, 1)
        cr.fill()
        return False

    # -- global menu -----------------------------------------------------
    def queue_rebuild(self):
        if self._rebuild_id is None:
            self._rebuild_id = GLib.timeout_add(60, self._do_rebuild)

    def _do_rebuild(self):
        # never yank a menu out from under the user's pointer
        if self.menubar.get_selected_item() is not None:
            return True
        self._rebuild_id = None
        self.rebuild()
        return False

    def _menu_updated(self, client):
        if self.menubar.get_selected_item() is not None:
            apply_live(client)      # a menu is open: refresh it in place
        self.queue_rebuild()

    def _menu_client_for(self, group):
        if group is None:
            return None
        cands = list(reversed(group.windows))
        active = self.apps.screen.get_active_window()
        if active in cands:
            cands.remove(active)
            cands.insert(0, active)
        for w in cands:
            reg = self.registrar.lookup(w.get_xid())
            if reg:
                return reg
        return None

    def _gtk_menus_for(self, group):
        """GtkApplication menubars exported over org.gtk.Menus."""
        if group is None:
            return None
        x = self.apps.x
        for w in reversed(group.windows):
            xid = w.get_xid()
            path = x.prop_str(xid, "_GTK_MENUBAR_OBJECT_PATH")
            bus = x.prop_str(xid, "_GTK_UNIQUE_BUS_NAME")
            if path and bus:
                return (bus, path, x.prop_str(xid, "_GTK_APPLICATION_OBJECT_PATH"),
                        x.prop_str(xid, "_GTK_WINDOW_OBJECT_PATH"), x.prop_str(xid, "_UNITY_OBJECT_PATH"))
        return None

    def _set_gtk_model(self, key):
        self._gtk_key = key
        self._gmodel = None
        self._watched_models = set()
        conn = self.registrar.bus
        if key is None:
            for prefix in ("app", "win", "unity"):
                self.menubar.insert_action_group(prefix, None)
            return
        bus, path, app_path, win_path, unity_path = key
        self._gmodel = Gio.DBusMenuModel.get(conn, bus, path)
        # appmenu-gtk-module exports classic GtkMenuBar actions under "unity."
        for prefix, p in (("app", app_path), ("win", win_path), ("unity", unity_path)):
            self.menubar.insert_action_group(prefix, Gio.DBusActionGroup.get(conn, bus, p) if p else None)

    def _watch_model(self, model):
        if id(model) not in self._watched_models:
            self._watched_models.add(id(model))
            model.connect("items-changed", lambda *_: self.queue_rebuild())

    def _gmenu_items(self, model):
        self._watch_model(model)
        out = []
        for i in range(model.get_n_items()):
            sec = model.get_item_link(i, Gio.MENU_LINK_SECTION)
            if sec is not None:
                out += self._gmenu_items(sec)
                continue
            label = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_LABEL, GLib.VariantType("s"))
            sub = model.get_item_link(i, Gio.MENU_LINK_SUBMENU)
            if sub is None:
                continue
            mi = Gtk.MenuItem.new_with_mnemonic(label.get_string() if label else "")
            menu = Gtk.Menu.new_from_model(sub)
            mi.set_submenu(menu)
            out.append(mi)
        return out

    def rebuild(self):
        group = self.apps.active_group()
        gkey = self._gtk_menus_for(group)
        if gkey != self._gtk_key:
            self._set_gtk_model(gkey)
        reg = None if gkey else self._menu_client_for(group)
        if reg != self._client_key:
            if self._client is not None:
                self._client.close()
            self._client = DBusMenuClient(self.registrar.bus, reg[0], reg[1], self._menu_updated) \
                if reg else None
            self._client_key = reg

        for child in self.menubar.get_children():
            self.menubar.remove(child)
            child.destroy()

        logo = Gtk.MenuItem()
        logo.add(mark_image(15))
        logo.get_style_context().add_class("slope-logo")
        logo.set_submenu(self._slope_menu())
        self.menubar.append(logo)

        name = group.name if group else "Files"
        appmenu = submenu_item("<b>%s</b>" % GLib.markup_escape_text(name),
                               self._app_menu(group), markup=True)
        appmenu.get_style_context().add_class("slope-appname")
        self.menubar.append(appmenu)

        gitems = self._gmenu_items(self._gmodel) if self._gmodel is not None else []
        if not gitems and self._client is not None and self._client.layout is not None:
            _id, _props, children = self._client.layout
            self._client.widgets = {}
            gitems = [mi for mi in build_menu_items(self._client, children)
                      if not isinstance(mi, Gtk.SeparatorMenuItem)]
        if gitems:
            # macOS order: ... Window, Help
            labels = [(mi.get_label() or "").replace("_", "").strip().lower() for mi in gitems]
            win = self._window_menu(group)
            if "window" in labels:
                win = None
            pos = labels.index("help") if "help" in labels else len(gitems)
            if win is not None:
                gitems.insert(pos, win)
            for mi in gitems:
                self.menubar.append(mi)
        elif group is None:
            for mi in self._finder_menus():
                self.menubar.append(mi)
        else:
            for mi in self._generic_menus(group):
                self.menubar.append(mi)
        self.menubar.show_all()

    def _ts(self):
        return self.apps.now(self.get_window())

    def _slope_menu(self):
        menu = Gtk.Menu()
        for it in (
            cmd_item("About This Computer", cb=lambda: spawn(["slope-settings", "--about"])),
            sep(),
            cmd_item("System Settings…", cb=lambda: spawn(["slope-settings"])),
            cmd_item("Spotlight Search", "⌘Space", cb=lambda: spawn(["slope-spotlight"])),
            sep(),
            cmd_item("Force Quit…", "⌥⌘Esc", cb=lambda: spawn(["slope-settings", "--force-quit"])),
            sep(),
            cmd_item("Sleep", cb=lambda: power("suspend")),
            cmd_item("Restart…", cb=lambda: self.shell.confirm("restart")),
            cmd_item("Shut Down…", cb=lambda: self.shell.confirm("poweroff")),
            sep(),
            cmd_item("Log Out %s…" % (GLib.get_real_name() or GLib.get_user_name()), "⇧⌘Q",
                     cb=lambda: self.shell.confirm("logout")),
        ):
            menu.append(it)
        menu.show_all()
        return menu

    def _app_menu(self, group):
        if group is None:
            return [
                cmd_item("About Slope", cb=lambda: spawn(["slope-settings", "--about"])),
                sep(),
                cmd_item("Settings…", "⌘,", cb=lambda: spawn(["slope-settings"])),
                sep(),
                cmd_item("Empty Trash…", cb=self._empty_trash),
                sep(),
                cmd_item("Show All", cb=lambda: self.apps.unhide_all(self._ts())),
            ]
        name = group.name
        items = [
            cmd_item("About %s" % name, cb=lambda: self.shell.about_app(group)),
            sep(),
            cmd_item("Hide %s" % name, "⌘H", cb=lambda: self.apps.hide_group(group), sensitive=group.running),
            cmd_item("Hide Others", "⌥⌘H", cb=lambda: self._hide_others(group)),
            cmd_item("Show All", cb=lambda: self.apps.unhide_all(self._ts())),
            sep(),
        ]
        dock_item = Gtk.CheckMenuItem(label="Keep in Dock")
        dock_item.set_active(group.pinned)
        dock_item.set_sensitive(group.appinfo is not None)
        dock_item.connect("toggled", lambda m: self.apps.pin(group, m.get_active()))
        items += [dock_item, sep(),
                  cmd_item("Quit %s" % name, "⌘Q", cb=lambda: self.apps.quit_group(group),
                           sensitive=group.running)]
        return items

    def _hide_others(self, group):
        for g in self.apps.groups():
            if g.key != group.key and g.running:
                self.apps.hide_group(g)

    def _window_menu(self, group):
        items = [
            cmd_item("Minimize", "⌘M", cb=lambda: spawn(["slope-ctl", "minimize"])),
            cmd_item("Zoom", cb=lambda: spawn(["wmctrl", "-r", ":ACTIVE:", "-b", "toggle,maximized_vert,maximized_horz"])),
            cmd_item("Enter Full Screen", "⌃⌘F", cb=lambda: spawn(["wmctrl", "-r", ":ACTIVE:", "-b", "toggle,fullscreen"])),
            sep(),
            cmd_item("Bring All to Front", cb=lambda: group and self.apps.activate_group(group, self._ts(), False)),
        ]
        if group is not None and group.windows:
            items.append(sep())
            active = self.apps.screen.get_active_window()
            for w in group.windows:
                title = w.get_name() or group.name
                if len(title) > 60:
                    title = title[:57] + "…"
                mi = Gtk.CheckMenuItem(label=title)
                mi.set_draw_as_radio(True)
                mi.set_active(w == active)
                mi.connect("activate", lambda m, win=w: self.apps.show_window(win, self._ts()))
                items.append(mi)
        return submenu_item("Window", items)

    def _generic_menus(self, group):
        """For apps that do not export a menu bar: standard menus that send shortcuts."""
        file_m = submenu_item("File", [
            cmd_item("New Window", "⌃N", cb=lambda: send_keys("ctrl+n")),
            cmd_item("New Tab", "⌃T", cb=lambda: send_keys("ctrl+t")),
            cmd_item("Open…", "⌃O", cb=lambda: send_keys("ctrl+o")),
            sep(),
            cmd_item("Close Window", "⌘W", cb=lambda: spawn(["slope-ctl", "close-window"])),
            cmd_item("Save", "⌃S", cb=lambda: send_keys("ctrl+s")),
            cmd_item("Print…", "⌃P", cb=lambda: send_keys("ctrl+p")),
        ])
        edit_m = submenu_item("Edit", [
            cmd_item("Undo", "⌃Z", cb=lambda: send_keys("ctrl+z")),
            cmd_item("Redo", "⇧⌃Z", cb=lambda: send_keys("ctrl+shift+z")),
            sep(),
            cmd_item("Cut", "⌃X", cb=lambda: send_keys("ctrl+x")),
            cmd_item("Copy", "⌃C", cb=lambda: send_keys("ctrl+c")),
            cmd_item("Paste", "⌃V", cb=lambda: send_keys("ctrl+v")),
            cmd_item("Select All", "⌃A", cb=lambda: send_keys("ctrl+a")),
            sep(),
            cmd_item("Find…", "⌃F", cb=lambda: send_keys("ctrl+f")),
        ])
        view_m = submenu_item("View", [
            cmd_item("Zoom In", "⌃+", cb=lambda: send_keys("ctrl+plus")),
            cmd_item("Zoom Out", "⌃−", cb=lambda: send_keys("ctrl+minus")),
            cmd_item("Actual Size", "⌃0", cb=lambda: send_keys("ctrl+0")),
            sep(),
            cmd_item("Enter Full Screen", "⌃⌘F", cb=lambda: spawn(["wmctrl", "-r", ":ACTIVE:", "-b", "toggle,fullscreen"])),
        ])
        return [file_m, edit_m, view_m, self._window_menu(group), self._help_menu()]

    def _finder_menus(self):
        home = GLib.get_home_dir()

        def go(path):
            return lambda: spawn(["thunar", path])

        def xdg(kind, fallback):
            p = GLib.get_user_special_dir(kind)
            return p or os.path.join(home, fallback)

        file_m = submenu_item("File", [
            cmd_item("New Window", "⌘N", cb=go(home)),
            cmd_item("New Terminal", "⌘T", cb=lambda: spawn(["xfce4-terminal"])),
            sep(),
            cmd_item("Find…", "⌘Space", cb=lambda: spawn(["slope-spotlight"])),
        ])
        go_m = submenu_item("Go", [
            cmd_item("Home", "⇧⌘H", cb=go(home)),
            cmd_item("Desktop", cb=go(xdg(GLib.UserDirectory.DIRECTORY_DESKTOP, "Desktop"))),
            cmd_item("Documents", cb=go(xdg(GLib.UserDirectory.DIRECTORY_DOCUMENTS, "Documents"))),
            cmd_item("Downloads", cb=go(xdg(GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads"))),
            cmd_item("Pictures", cb=go(xdg(GLib.UserDirectory.DIRECTORY_PICTURES, "Pictures"))),
            cmd_item("Music", cb=go(xdg(GLib.UserDirectory.DIRECTORY_MUSIC, "Music"))),
            sep(),
            cmd_item("Computer", cb=go("/")),
            cmd_item("Network", cb=go("network:///")),
            cmd_item("Trash", cb=go("trash:///")),
        ])
        return [file_m, go_m, self._window_menu(None), self._help_menu()]

    def _help_menu(self):
        return submenu_item("Help", [
            cmd_item("Keyboard Shortcuts", cb=lambda: spawn(["slope-settings", "--page", "shortcuts"])),
            cmd_item("About Slope", cb=lambda: spawn(["slope-settings", "--about"])),
        ])

    def _empty_trash(self):
        if self.shell.ask("Are you sure you want to permanently erase the items in the Trash?",
                          "You can’t undo this action.", "Empty Trash"):
            spawn(["gio", "trash", "--empty"])

    # -- status area -----------------------------------------------------
    def _build_status(self):
        # Spotlight
        sp = Gtk.MenuItem()
        sp.add(Gtk.Image.new_from_icon_name("system-search-symbolic", Gtk.IconSize.MENU))
        sp.connect("activate", lambda *_: spawn(["slope-spotlight"]))
        sp.set_tooltip_text("Spotlight")
        self.status.append(sp)

        # Network
        self.net_img = Gtk.Image.new_from_icon_name("network-offline-symbolic", Gtk.IconSize.MENU)
        self.net_item = Gtk.MenuItem()
        self.net_item.add(self.net_img)
        self.net_menu = Gtk.Menu()
        self.net_menu.connect("show", lambda *_: self._fill_net_menu())
        self.net_item.set_submenu(self.net_menu)
        self.status.append(self.net_item)
        self._nm = None
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                                  "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager",
                                  "org.freedesktop.NetworkManager", None, self._nm_ready)

        # Volume
        self.vol_img = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic", Gtk.IconSize.MENU)
        vol = Gtk.MenuItem()
        vol.add(self.vol_img)
        vmenu = Gtk.Menu()
        self.vol_label = Gtk.MenuItem(label="Sound")
        self.vol_label.set_sensitive(False)
        vmenu.append(self.vol_label)
        scale_item = Gtk.MenuItem()
        scale_item.get_style_context().add_class("slope-slider-item")
        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol_scale.set_draw_value(False)
        self.vol_scale.set_size_request(200, -1)
        self._vol_handler = self.vol_scale.connect("value-changed", self._vol_changed)
        scale_item.add(self.vol_scale)
        vmenu.append(scale_item)
        self.mute_item = Gtk.CheckMenuItem(label="Mute")
        self._mute_handler = self.mute_item.connect("toggled", lambda m: spawn(
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if m.get_active() else "0"]))
        vmenu.append(self.mute_item)
        vmenu.append(sep())
        vmenu.append(cmd_item("Sound Settings…", cb=lambda: spawn(["pavucontrol"])))
        vmenu.connect("show", lambda *_: self._refresh_volume())
        vmenu.show_all()
        vol.set_submenu(vmenu)
        self.status.append(vol)

        # Battery
        self.bat_item = Gtk.MenuItem()
        bb = Gtk.Box(spacing=4)
        self.bat_img = Gtk.Image.new_from_icon_name("battery-full-symbolic", Gtk.IconSize.MENU)
        self.bat_lbl = Gtk.Label()
        bb.pack_start(self.bat_lbl, False, False, 0)
        bb.pack_start(self.bat_img, False, False, 0)
        self.bat_item.add(bb)
        bm = Gtk.Menu()
        bm.append(cmd_item("Power Settings…", cb=lambda: spawn(["slope-settings", "--page", "about"])))
        bm.show_all()
        self.bat_item.set_submenu(bm)
        self.status.append(self.bat_item)

        # Clock
        self.clock = Gtk.MenuItem()
        self.clock_lbl = Gtk.Label()
        self.clock.add(self.clock_lbl)
        cm = Gtk.Menu()
        self.date_item = Gtk.MenuItem(label="")
        self.date_item.set_sensitive(False)
        cm.append(self.date_item)
        cal_item = Gtk.MenuItem()
        cal_item.get_style_context().add_class("slope-slider-item")
        cal = Gtk.Calendar()
        cal_item.add(cal)
        cm.append(cal_item)
        cm.append(sep())
        t24 = Gtk.CheckMenuItem(label="Use 24-Hour Clock")
        t24.set_active(self.shell.settings.get("clock_24h", True))
        t24.connect("toggled", lambda m: update_setting("clock_24h", m.get_active()))
        cm.append(t24)
        cm.append(cmd_item("Date & Time Settings…", cb=lambda: spawn(["slope-settings", "--page", "clock"])))
        def today(*_):
            now = time.localtime()
            cal.select_month(now.tm_mon - 1, now.tm_year)
            cal.select_day(now.tm_mday)
        cm.connect("show", today)
        cm.show_all()
        self.clock.set_submenu(cm)
        self.status.append(self.clock)

        self._tick()
        GLib.timeout_add_seconds(1, self._tick)
        self._refresh_battery()
        GLib.timeout_add_seconds(30, self._refresh_battery)
        self._refresh_volume()
        GLib.timeout_add_seconds(5, self._refresh_volume_icon)

    def _tick(self):
        s = self.shell.settings
        fmt = ""
        if s.get("clock_show_date", True):
            fmt += "%a %-d %b  "
        if s.get("clock_24h", True):
            fmt += "%H:%M:%S" if s.get("clock_seconds") else "%H:%M"
        else:
            fmt += "%-I:%M:%S %p" if s.get("clock_seconds") else "%-I:%M %p"
        self.clock_lbl.set_text(time.strftime(fmt))
        self.date_item.set_label(time.strftime("%A, %-d %B %Y"))
        return True

    # battery
    def _refresh_battery(self):
        bats = glob.glob("/sys/class/power_supply/BAT*")
        if not bats:
            self.bat_item.hide()
            self.bat_item.set_no_show_all(True)
            return True
        try:
            cap = int(open(os.path.join(bats[0], "capacity")).read().strip())
            status = open(os.path.join(bats[0], "status")).read().strip()
        except (OSError, ValueError):
            return True
        level = "full" if cap > 80 else "good" if cap > 50 else "low" if cap > 20 else "caution"
        icon = "battery-%s%s-symbolic" % (level, "-charging" if status == "Charging" else "")
        self.bat_img.set_from_icon_name(icon, Gtk.IconSize.MENU)
        self.bat_lbl.set_text("%d%%" % cap)
        self.bat_item.set_no_show_all(False)
        self.bat_item.show_all()
        return True

    # volume
    def _read_volume(self):
        if not have("pactl"):
            return None, False
        out = run_quiet(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        vol = None
        for tok in out.split():
            if tok.endswith("%"):
                try:
                    vol = int(tok[:-1])
                    break
                except ValueError:
                    pass
        muted = "yes" in run_quiet(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        return vol, muted

    def _set_vol_icon(self, vol, muted):
        if vol is None:
            icon = "audio-volume-muted-symbolic"
        elif muted or vol == 0:
            icon = "audio-volume-muted-symbolic"
        elif vol < 34:
            icon = "audio-volume-low-symbolic"
        elif vol < 67:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.vol_img.set_from_icon_name(icon, Gtk.IconSize.MENU)

    def _refresh_volume(self):
        vol, muted = self._read_volume()
        self.vol_scale.handler_block(self._vol_handler)
        self.vol_scale.set_value(vol or 0)
        self.vol_scale.handler_unblock(self._vol_handler)
        self.mute_item.handler_block(self._mute_handler)
        self.mute_item.set_active(muted)
        self.mute_item.handler_unblock(self._mute_handler)
        self.vol_label.set_label("Sound — %s" % ("%d%%" % vol if vol is not None else "no output device"))
        self._set_vol_icon(vol, muted)

    def _refresh_volume_icon(self):
        self._set_vol_icon(*self._read_volume())
        return True

    def _vol_changed(self, scale):
        v = int(scale.get_value())
        spawn(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % v])
        self.vol_label.set_label("Sound — %d%%" % v)
        self._set_vol_icon(v, False)

    # network
    def _nm_ready(self, src, res):
        try:
            self._nm = Gio.DBusProxy.new_for_bus_finish(res)
        except GLib.Error:
            self._nm = None
            return
        self._nm.connect("g-properties-changed", lambda *_: self._refresh_net())
        self._refresh_net()

    def _nm_prop(self, name):
        if self._nm is None:
            return None
        v = self._nm.get_cached_property(name)
        return v.unpack() if v is not None else None

    def _refresh_net(self):
        state = self._nm_prop("State") or 0
        ctype = self._nm_prop("PrimaryConnectionType") or ""
        if state >= 70:
            icon = "network-wireless-signal-excellent-symbolic" if "wireless" in ctype else "network-wired-symbolic"
        elif state >= 40:
            icon = "network-wireless-acquiring-symbolic" if "wireless" in ctype else "network-wired-acquiring-symbolic"
        else:
            icon = "network-offline-symbolic"
        self.net_img.set_from_icon_name(icon, Gtk.IconSize.MENU)

    def _fill_net_menu(self):
        for c in self.net_menu.get_children():
            self.net_menu.remove(c)
            c.destroy()
        state = self._nm_prop("State") or 0
        status = {70: "Connected", 60: "Connected (site only)", 50: "Connected (local only)",
                  40: "Connecting…", 30: "Disconnecting…", 20: "Disconnected", 10: "Networking off"}
        hdr = Gtk.MenuItem(label="Network: %s" % status.get(state, "Unavailable"))
        hdr.set_sensitive(False)
        self.net_menu.append(hdr)
        out = run_quiet(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"], timeout=4) \
            if have("nmcli") else ""
        nets = []
        seen = set()
        for line in out.splitlines():
            parts = line.replace("\\:", "\x00").split(":")
            if len(parts) < 4:
                continue
            inuse, ssid, sig, sec = [p.replace("\x00", ":") for p in parts[:4]]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            nets.append((inuse == "*", ssid, int(sig or 0), sec))
        if nets:
            self.net_menu.append(sep())
            t = Gtk.MenuItem(label="Wi-Fi Networks")
            t.set_sensitive(False)
            self.net_menu.append(t)
            for inuse, ssid, sig, sec in nets[:12]:
                lbl = "%s%s   %s" % ("✓ " if inuse else "    ", ssid, "🔒" if sec and sec != "--" else "")
                mi = Gtk.MenuItem(label=lbl)
                mi.connect("activate", lambda m, s=ssid, x=sec: self.shell.wifi_connect(s, x))
                self.net_menu.append(mi)
        self.net_menu.append(sep())
        self.net_menu.append(cmd_item("Network Settings…", cb=lambda: spawn(["nm-connection-editor"])))
        self.net_menu.show_all()
