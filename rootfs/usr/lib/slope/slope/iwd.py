"""Talk to iwd (the iNet wireless daemon) over D-Bus.

Used by the top bar's Wi-Fi menu and by the Wi-Fi app.  Wraps the
net.connman.iwd object tree (adapters, devices, stations, networks and
known networks) and provides an Agent so iwd can ask us for passwords.
"""
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

IWD = "net.connman.iwd"
AGENT_MANAGER_PATH = "/net/connman/iwd"

AGENT_XML = """
<node>
  <interface name="net.connman.iwd.Agent">
    <method name="Release"/>
    <method name="RequestPassphrase">
      <arg type="o" name="network" direction="in"/>
      <arg type="s" name="passphrase" direction="out"/>
    </method>
    <method name="RequestPrivateKeyPassphrase">
      <arg type="o" name="network" direction="in"/>
      <arg type="s" name="passphrase" direction="out"/>
    </method>
    <method name="RequestUserNameAndPassword">
      <arg type="o" name="network" direction="in"/>
      <arg type="s" name="user" direction="out"/>
      <arg type="s" name="password" direction="out"/>
    </method>
    <method name="RequestUserPassword">
      <arg type="o" name="network" direction="in"/>
      <arg type="s" name="user" direction="in"/>
      <arg type="s" name="password" direction="out"/>
    </method>
    <method name="Cancel">
      <arg type="s" name="reason" direction="in"/>
    </method>
  </interface>
</node>
"""

SECURITY_LABELS = {"open": "Open", "psk": "WPA/WPA2/WPA3 Personal", "8021x": "Enterprise", "wep": "WEP"}


def signal_bars(strength):
    """iwd reports signal strength in 100 * dBm."""
    dbm = strength / 100.0
    if dbm >= -60:
        return 4
    if dbm >= -67:
        return 3
    if dbm >= -75:
        return 2
    return 1


class Iwd(GObject.Object):
    """Cached view of iwd's objects.  Emits "changed" whenever anything moves."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self):
        super().__init__()
        self.mgr = None
        self.bus = None
        self._queued = None
        self.networks = {}       # station path -> [(network path, strength)]
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return
        Gio.DBusObjectManagerClient.new(
            self.bus, Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START, IWD, "/",
            None, None, None, self._ready)

    def _ready(self, src, res):
        try:
            self.mgr = Gio.DBusObjectManagerClient.new_finish(res)
        except GLib.Error:
            self.mgr = None
            return
        for sig in ("object-added", "object-removed", "interface-added", "interface-removed",
                    "interface-proxy-properties-changed"):
            self.mgr.connect(sig, lambda *a: self._queue(refresh=True))
        self.mgr.connect("notify::name-owner", lambda *a: self._queue(refresh=True))
        self._queue(refresh=True)

    def _queue(self, refresh=False):
        if refresh:
            for st in self.stations():
                self._fetch_networks(st["path"])
        if self._queued is None:
            self._queued = GLib.timeout_add(80, self._emit)

    def _emit(self):
        self._queued = None
        self.emit("changed")
        return False

    # -- state -------------------------------------------------------------
    @property
    def running(self):
        return self.mgr is not None and self.mgr.get_name_owner() is not None

    def _iface(self, path, iface):
        if self.mgr is None:
            return None
        obj = self.mgr.get_object(path)
        return obj.get_interface(iface) if obj is not None else None

    @staticmethod
    def _p(proxy, name, default=None):
        if proxy is None:
            return default
        v = proxy.get_cached_property(name)
        return v.unpack() if v is not None else default

    def _objects(self, iface):
        if not self.running:
            return []
        out = []
        for obj in self.mgr.get_objects():
            px = obj.get_interface(iface)
            if px is not None:
                out.append((obj.get_object_path(), px))
        return out

    def devices(self):
        """Wireless interfaces, with their station state when in station mode."""
        res = []
        for path, dev in self._objects("net.connman.iwd.Device"):
            st = self._iface(path, "net.connman.iwd.Station")
            adapter = self._iface(self._p(dev, "Adapter", "/"), "net.connman.iwd.Adapter")
            connected = self._p(st, "ConnectedNetwork")
            net = self._iface(connected, "net.connman.iwd.Network") if connected else None
            res.append({
                "path": path,
                "name": self._p(dev, "Name", "wlan"),
                "address": self._p(dev, "Address", ""),
                "powered": bool(self._p(dev, "Powered", False)),
                "adapter_powered": bool(self._p(adapter, "Powered", True)),
                "adapter": self._p(dev, "Adapter"),
                "mode": self._p(dev, "Mode", "station"),
                "station": st is not None,
                "state": self._p(st, "State", "disconnected" if st is not None else "off"),
                "scanning": bool(self._p(st, "Scanning", False)),
                "connected_path": connected,
                "connected_name": self._p(net, "Name") if net is not None else None,
            })
        return sorted(res, key=lambda d: d["name"])

    def stations(self):
        return [d for d in self.devices() if d["station"]]

    def primary(self):
        st = self.stations()
        if st:
            for d in st:
                if d["state"] == "connected":
                    return d
            return st[0]
        devs = self.devices()
        return devs[0] if devs else None

    def _fetch_networks(self, station_path):
        def done(bus, res):
            try:
                (lst,) = bus.call_finish(res).unpack()
            except GLib.Error:
                lst = []
            self.networks[station_path] = lst
            self._queue()
        self.bus.call(IWD, station_path, "net.connman.iwd.Station", "GetOrderedNetworks", None,
                      GLib.VariantType("(a(on))"), Gio.DBusCallFlags.NONE, 3000, None, done)

    def network_list(self, station_path):
        """Visible networks, strongest first: dicts with name/security/known/connected/bars."""
        out = []
        for path, strength in self.networks.get(station_path, []):
            px = self._iface(path, "net.connman.iwd.Network")
            if px is None:
                continue
            out.append({
                "path": path,
                "name": self._p(px, "Name", "?"),
                "type": self._p(px, "Type", "open"),
                "connected": bool(self._p(px, "Connected", False)),
                "known": self._p(px, "KnownNetwork") is not None,
                "strength": strength,
                "bars": signal_bars(strength),
            })
        out.sort(key=lambda n: (not n["connected"], -n["strength"]))
        return out

    def known_networks(self):
        out = []
        for path, px in self._objects("net.connman.iwd.KnownNetwork"):
            out.append({
                "path": path,
                "name": self._p(px, "Name", "?"),
                "type": self._p(px, "Type", "psk"),
                "hidden": bool(self._p(px, "Hidden", False)),
                "autoconnect": bool(self._p(px, "AutoConnect", True)),
                "last": self._p(px, "LastConnectedTime", ""),
            })
        out.sort(key=lambda k: k["last"] or "", reverse=True)
        return out

    def connected_strength(self, station_path):
        for path, strength in self.networks.get(station_path, []):
            px = self._iface(path, "net.connman.iwd.Network")
            if px is not None and self._p(px, "Connected", False):
                return strength
        return None

    # -- actions -----------------------------------------------------------
    def _call(self, path, iface, method, args=None, on_error=None, on_done=None, timeout=60000):
        def done(bus, res):
            try:
                bus.call_finish(res)
                if on_done:
                    on_done()
            except GLib.Error as e:
                msg = e.message.split(":", 2)[-1].strip() if e.message else str(e)
                if on_error:
                    on_error(msg)
            self._queue(refresh=True)
        self.bus.call(IWD, path, iface, method, args, None, Gio.DBusCallFlags.NONE, timeout, None, done)

    def scan(self, station_path):
        self._call(station_path, "net.connman.iwd.Station", "Scan", timeout=15000)

    def connect_network(self, network_path, on_error=None, on_done=None):
        self._call(network_path, "net.connman.iwd.Network", "Connect", on_error=on_error, on_done=on_done)

    def connect_hidden(self, station_path, ssid, on_error=None, on_done=None):
        self._call(station_path, "net.connman.iwd.Station", "ConnectHiddenNetwork",
                   GLib.Variant("(s)", (ssid,)), on_error=on_error, on_done=on_done)

    def disconnect(self, station_path):
        self._call(station_path, "net.connman.iwd.Station", "Disconnect")

    def forget(self, known_path):
        self._call(known_path, "net.connman.iwd.KnownNetwork", "Forget")

    def _set(self, path, iface, prop, value):
        self._call(path, "org.freedesktop.DBus.Properties", "Set",
                   GLib.Variant("(ssv)", (iface, prop, value)))

    def set_powered(self, device, on):
        """Turn Wi-Fi on or off (the radio adapter and the interface)."""
        if on and device.get("adapter") and not device.get("adapter_powered", True):
            self._set(device["adapter"], "net.connman.iwd.Adapter", "Powered", GLib.Variant("b", True))
        self._set(device["path"], "net.connman.iwd.Device", "Powered", GLib.Variant("b", on))

    def set_autoconnect(self, known_path, on):
        self._set(known_path, "net.connman.iwd.KnownNetwork", "AutoConnect", GLib.Variant("b", on))

    def network_name(self, path):
        return self._p(self._iface(path, "net.connman.iwd.Network"), "Name", "the network")


# ---------------------------------------------------------------------------
# Password agent
# ---------------------------------------------------------------------------

class PassphraseDialog(Gtk.Dialog):
    """A glossy "join this network" sheet."""

    def __init__(self, network, need_user=False, user=None, kind="passphrase"):
        super().__init__(title="Wi-Fi")
        self.set_wmclass("slope-dialog", "Slope")
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        area = self.get_content_area()
        area.set_border_width(20)
        area.set_spacing(10)
        top = Gtk.Box(spacing=16)
        icon = Gtk.Image.new_from_icon_name("network-wireless-encrypted-symbolic", Gtk.IconSize.DIALOG)
        icon.set_pixel_size(48)
        top.pack_start(icon, False, False, 0)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>The Wi-Fi network “%s” requires a %s.</b>" % (
            GLib.markup_escape_text(network), "password" if kind == "passphrase" else kind))
        title.set_line_wrap(True)
        title.set_max_width_chars(42)
        txt.pack_start(title, False, False, 0)
        hint = Gtk.Label(label="The password is saved, so Slope joins automatically next time.", xalign=0)
        hint.get_style_context().add_class("dim-label")
        hint.set_line_wrap(True)
        hint.set_max_width_chars(46)
        txt.pack_start(hint, False, False, 0)
        top.pack_start(txt, True, True, 0)
        area.pack_start(top, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        grid.set_margin_start(64)
        row = 0
        self.user_entry = None
        if need_user:
            grid.attach(Gtk.Label(label="Username:", xalign=1), 0, row, 1, 1)
            self.user_entry = Gtk.Entry()
            self.user_entry.set_hexpand(True)
            if user:
                self.user_entry.set_text(user)
            grid.attach(self.user_entry, 1, row, 1, 1)
            row += 1
        grid.attach(Gtk.Label(label="Password:", xalign=1), 0, row, 1, 1)
        self.entry = Gtk.Entry()
        self.entry.set_visibility(False)
        self.entry.set_activates_default(True)
        self.entry.set_width_chars(28)
        self.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        grid.attach(self.entry, 1, row, 1, 1)
        row += 1
        show = Gtk.CheckButton(label="Show password")
        show.connect("toggled", lambda b: self.entry.set_visibility(b.get_active()))
        grid.attach(show, 1, row, 1, 1)
        area.pack_start(grid, False, False, 0)

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        join = self.add_button("Join", Gtk.ResponseType.OK)
        join.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        join.set_sensitive(False)
        self.entry.connect("changed", lambda e: join.set_sensitive(
            len(e.get_text()) >= (8 if kind == "passphrase" and not need_user else 1)))
        self.show_all()
        (self.user_entry or self.entry).grab_focus()


class Agent:
    """Answers iwd's password requests with a dialog."""

    def __init__(self, iwd):
        self.iwd = iwd
        self.path = "/org/slope/IwdAgent%d" % os.getpid()
        self.dialog = None
        self.pending = None
        self._reg_id = None
        if iwd.bus is None:
            return
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        try:
            self._reg_id = iwd.bus.register_object(self.path, node.interfaces[0], self._on_call, None, None)
        except GLib.Error:
            return
        Gio.bus_watch_name_on_connection(iwd.bus, IWD, Gio.BusNameWatcherFlags.NONE,
                                         lambda *a: self._register(), None)

    def _register(self):
        self.iwd.bus.call(IWD, AGENT_MANAGER_PATH, "net.connman.iwd.AgentManager", "RegisterAgent",
                          GLib.Variant("(o)", (self.path,)), None, Gio.DBusCallFlags.NONE, 5000, None, None)

    def _ask(self, inv, network_path, need_user=False, user=None, kind="passphrase", two=False):
        name = self.iwd.network_name(network_path)
        if self.dialog is not None:
            self.dialog.destroy()
        dlg = PassphraseDialog(name, need_user=need_user, user=user, kind=kind)
        self.dialog, self.pending = dlg, inv

        def respond(d, resp):
            pw = d.entry.get_text()
            usr = d.user_entry.get_text() if d.user_entry else ""
            d.destroy()
            self.dialog = None
            pending, self.pending = self.pending, None
            if pending is None:
                return
            if resp == Gtk.ResponseType.OK:
                pending.return_value(GLib.Variant("(ss)", (usr, pw)) if two else GLib.Variant("(s)", (pw,)))
            else:
                pending.return_dbus_error("net.connman.iwd.Agent.Error.Canceled", "Cancelled by the user")
        dlg.connect("response", respond)
        dlg.present()

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        if method == "Release":
            inv.return_value(None)
        elif method == "RequestPassphrase":
            (net,) = params.unpack()
            self._ask(inv, net)
        elif method == "RequestPrivateKeyPassphrase":
            (net,) = params.unpack()
            self._ask(inv, net, kind="private key passphrase")
        elif method == "RequestUserNameAndPassword":
            (net,) = params.unpack()
            self._ask(inv, net, need_user=True, kind="user name and password", two=True)
        elif method == "RequestUserPassword":
            net, user = params.unpack()
            self._ask(inv, net, need_user=True, user=user, kind="password")
        elif method == "Cancel":
            if self.dialog is not None:
                self.dialog.destroy()
                self.dialog = None
            self.pending = None
            inv.return_value(None)
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)


# ---------------------------------------------------------------------------
# Wired networking (NetworkManager), for the status line
# ---------------------------------------------------------------------------

def ethernet_status():
    """[(interface, state, connection, ipv4)] for wired devices, via nmcli."""
    import subprocess
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4 or parts[1] != "ethernet":
            continue
        dev, _t, state, conn = parts[:4]
        ip = ""
        if state.startswith("connected"):
            try:
                ip = subprocess.run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev],
                                    capture_output=True, text=True, timeout=3).stdout.strip().split("|")[0]
            except (OSError, subprocess.SubprocessError):
                ip = ""
        res.append((dev, state, conn, ip))
    return res
