"""Global menu support.

Implements com.canonical.AppMenu.Registrar so that GTK (through
appmenu-gtk-module) and Qt applications export their menu bars to us over
D-Bus, plus a small com.canonical.dbusmenu client that turns those menus into
Gtk.Menu objects shown in the top bar.
"""
import time

from gi.repository import Gio, GLib, Gtk

REGISTRAR_XML = """
<node>
  <interface name="com.canonical.AppMenu.Registrar">
    <method name="RegisterWindow">
      <arg type="u" name="windowId" direction="in"/>
      <arg type="o" name="menuObjectPath" direction="in"/>
    </method>
    <method name="UnregisterWindow">
      <arg type="u" name="windowId" direction="in"/>
    </method>
    <method name="GetMenuForWindow">
      <arg type="u" name="windowId" direction="in"/>
      <arg type="s" name="service" direction="out"/>
      <arg type="o" name="menuObjectPath" direction="out"/>
    </method>
    <signal name="WindowRegistered">
      <arg type="u" name="windowId"/>
      <arg type="s" name="service"/>
      <arg type="o" name="menuObjectPath"/>
    </signal>
    <signal name="WindowUnregistered">
      <arg type="u" name="windowId"/>
    </signal>
  </interface>
</node>
"""

REG_PATH = "/com/canonical/AppMenu/Registrar"
REG_NAME = "com.canonical.AppMenu.Registrar"


class Registrar:
    def __init__(self, on_change):
        self.on_change = on_change
        self.menus = {}       # xid -> (bus name, object path)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(REGISTRAR_XML)
        self.bus.register_object(REG_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(self.bus, REG_NAME, Gio.BusNameOwnerFlags.REPLACE, None, None)
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                                  self._on_name_owner_changed)

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        if method == "RegisterWindow":
            xid, obj = params.unpack()
            self.menus[xid] = (sender, obj)
            inv.return_value(None)
            conn.emit_signal(None, REG_PATH, REG_NAME, "WindowRegistered",
                             GLib.Variant("(uso)", (xid, sender, obj)))
            self.on_change(xid)
        elif method == "UnregisterWindow":
            (xid,) = params.unpack()
            self.menus.pop(xid, None)
            inv.return_value(None)
            self.on_change(xid)
        elif method == "GetMenuForWindow":
            (xid,) = params.unpack()
            svc, obj = self.menus.get(xid, ("", "/"))
            inv.return_value(GLib.Variant("(so)", (svc, obj)))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _on_name_owner_changed(self, conn, sender, path, iface, signal, params):
        name, old, new = params.unpack()
        if new == "" and old:
            gone = [x for x, (svc, _) in self.menus.items() if svc == old]
            for x in gone:
                del self.menus[x]
            if gone:
                self.on_change(None)

    def lookup(self, xid):
        return self.menus.get(xid)


class DBusMenuClient:
    """Fetches a dbusmenu tree and keeps it fresh."""

    IFACE = "com.canonical.dbusmenu"

    def __init__(self, bus, service, path, on_update):
        self.bus, self.service, self.path = bus, service, path
        self.on_update = on_update
        self.layout = None
        self.widgets = {}     # dbusmenu id -> Gtk.MenuItem currently shown
        self._pending = None
        self._subs = [
            bus.signal_subscribe(service, self.IFACE, sig, path, None, Gio.DBusSignalFlags.NONE,
                                 self._on_signal)
            for sig in ("LayoutUpdated", "ItemsPropertiesUpdated")
        ]
        self.refresh()

    def close(self):
        for s in self._subs:
            self.bus.signal_unsubscribe(s)
        self._subs = []
        self.on_update = None

    def _on_signal(self, *args):
        if self._pending is None:
            self._pending = GLib.timeout_add(120, self._do_refresh)

    def _do_refresh(self):
        self._pending = None
        self.refresh()
        return False

    def refresh(self):
        self.bus.call(self.service, self.path, self.IFACE, "GetLayout",
                      GLib.Variant("(iias)", (0, -1, [])), GLib.VariantType("(u(ia{sv}av))"),
                      Gio.DBusCallFlags.NONE, 2000, None, self._on_layout)

    def _on_layout(self, bus, res):
        try:
            reply = bus.call_finish(res)
        except GLib.Error:
            return
        _rev, layout = reply.unpack()
        self.layout = layout
        if self.on_update:
            self.on_update(self)

    def event(self, item_id, name="clicked"):
        self.bus.call(self.service, self.path, self.IFACE, "Event",
                      GLib.Variant("(isvu)", (item_id, name, GLib.Variant("i", 0), int(time.time()))),
                      None, Gio.DBusCallFlags.NONE, 1000, None, None)

    def about_to_show(self, item_id):
        def done(bus, res):
            try:
                (need,) = bus.call_finish(res).unpack()
            except GLib.Error:
                return
            if need:
                self._on_signal()
        self.bus.call(self.service, self.path, self.IFACE, "AboutToShow",
                      GLib.Variant("(i)", (item_id,)), GLib.VariantType("(b)"),
                      Gio.DBusCallFlags.NONE, 1000, None, done)


_MOD_MAP = {"Control": "<Control>", "Shift": "<Shift>", "Alt": "<Alt>", "Super": "<Super>"}


def _accel(shortcut):
    try:
        combo = shortcut[0]
    except (IndexError, TypeError):
        return None
    acc = "".join(_MOD_MAP.get(k, "") for k in combo[:-1]) + combo[-1]
    key, mods = Gtk.accelerator_parse(acc)
    return (key, mods) if key else None


def apply_live(client):
    """Update already-built menu items in place (used while a menu is open)."""
    def walk(node):
        cid, props, sub = node
        mi = client.widgets.get(cid)
        if mi is not None:
            try:
                mi.set_sensitive(props.get("enabled", True))
                mi.set_visible(props.get("visible", True))
                if isinstance(mi, Gtk.CheckMenuItem) and "toggle-state" in props:
                    h = getattr(mi, "_slope_handler", None)
                    if h:
                        mi.handler_block(h)
                    mi.set_active(props.get("toggle-state", 0) == 1)
                    if h:
                        mi.handler_unblock(h)
                if "label" in props and props.get("type") != "separator":
                    mi.set_label(props["label"])
            except Exception:
                pass
        for c in sub:
            walk(c)
    if client.layout is not None:
        walk(client.layout)


def build_menu_items(client, children):
    """Turn dbusmenu children into a list of Gtk.MenuItems."""
    items = []
    for child in children:
        cid, props, sub = child
        if not props.get("visible", True):
            continue
        if props.get("type") == "separator":
            items.append(Gtk.SeparatorMenuItem())
            continue
        label = props.get("label", "")
        toggle = props.get("toggle-type", "")
        if toggle in ("checkmark", "radio"):
            mi = Gtk.CheckMenuItem.new_with_mnemonic(label)
            mi.set_draw_as_radio(toggle == "radio")
            mi.set_active(props.get("toggle-state", 0) == 1)
        else:
            mi = Gtk.MenuItem.new_with_mnemonic(label)
        mi.set_sensitive(props.get("enabled", True))
        acc = _accel(props.get("shortcut"))
        if acc:
            lbl = mi.get_child()
            if isinstance(lbl, Gtk.AccelLabel):
                lbl.set_accel(*acc)
        if sub or props.get("children-display") == "submenu":
            menu = Gtk.Menu()
            for it in build_menu_items(client, sub):
                menu.append(it)
            menu.connect("show", lambda m, i=cid: client.about_to_show(i))
            mi.set_submenu(menu)
        else:
            mi._slope_handler = mi.connect("activate", lambda m, i=cid: client.event(i))
        client.widgets[cid] = mi
        items.append(mi)
    return items
