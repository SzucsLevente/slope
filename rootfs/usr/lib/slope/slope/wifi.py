"""slope-wifi: a glossy Wi-Fi app for iwd, plus wired (Ethernet) status."""
import os
import sys

import cairo
import gi

from . import art
from .common import SHARE, spawn
from .iwd import SECURITY_LABELS, Agent, Iwd, ethernet_status

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

CSS = b"""
.slope-wifi-header {
  padding: 18px 22px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.18);
  background-image: linear-gradient(to bottom, #fdfdfe 0%, #eef1f6 49%, #e2e7ef 50%, #edf1f7 100%);
  box-shadow: inset 0 1px #ffffff;
}
.slope-wifi-header .title { font-size: 18pt; font-weight: bold; color: #1d1d1f; }
.slope-wifi-header .status { color: alpha(#1d1d1f, 0.6); }
.slope-wifi-card {
  border-radius: 12px;
  border: 1px solid rgba(0, 0, 0, 0.16);
  background-color: #ffffff;
  background-image: linear-gradient(to bottom, #ffffff, #f7f8fb);
  box-shadow: inset 0 1px #ffffff, 0 1px 3px rgba(0, 0, 0, 0.10);
}
.slope-wifi-card row {
  padding: 8px 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  background: transparent;
}
.slope-wifi-card row:first-child { border-radius: 12px 12px 0 0; }
.slope-wifi-card row:last-child { border-bottom: none; border-radius: 0 0 12px 12px; }
.slope-wifi-card row:only-child { border-radius: 12px; }
.slope-wifi-card row:hover {
  background-image: linear-gradient(to bottom, rgba(140, 198, 255, 0.28), rgba(47, 134, 243, 0.14));
}
.slope-wifi-card row.connected label.name { font-weight: bold; }
.slope-wifi-section { font-weight: bold; color: alpha(#1d1d1f, 0.65); margin: 14px 4px 6px 4px; }
.slope-wifi-dim { color: alpha(#1d1d1f, 0.55); font-size: 9pt; }
.slope-wifi-error {
  border-radius: 10px;
  padding: 8px 12px;
  color: #7a1410;
  border: 1px solid rgba(200, 40, 30, 0.45);
  background-image: linear-gradient(to bottom, #ffe9e7, #ffd3cf);
}
"""


def wifi_icon(size, bars=4, active=True):
    """The glossy blue Wi-Fi app icon."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    art.draw_wifi_icon(cr, size * 0.04, size * 0.04, size * 0.92, bars, active)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


class SignalBars(Gtk.DrawingArea):
    def __init__(self, bars):
        super().__init__()
        self.bars = bars
        self.set_size_request(24, 18)
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        h = self.get_allocated_height()
        bw, gap = 4, 2
        for i in range(4):
            bh = 5 + i * 4
            x = i * (bw + gap)
            y = h - bh - 1
            art.rounded_rect(cr, x, y, bw, bh, 1.5)
            if i < self.bars:
                g = cairo.LinearGradient(x, 0, x + bw, 0)
                g.add_color_stop_rgb(0, 0.55, 0.80, 1.0)
                g.add_color_stop_rgb(0.5, 0.18, 0.53, 0.95)
                g.add_color_stop_rgb(1, 0.10, 0.40, 0.85)
                cr.set_source(g)
            else:
                cr.set_source_rgba(0, 0, 0, 0.13)
            cr.fill()
        return False


class WifiWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Wi-Fi")
        self.set_wmclass("slope-wifi", "Slope-wifi")
        self.set_default_size(520, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon(wifi_icon(64))
        self.iwd = Iwd()
        self.agent = Agent(self.iwd)
        self.connecting = None
        self._join_started = 0
        self.error = None
        self._updating = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        header = Gtk.Box(spacing=16)
        header.get_style_context().add_class("slope-wifi-header")
        self.header_img = Gtk.Image.new_from_pixbuf(wifi_icon(64))
        header.pack_start(self.header_img, False, False, 0)
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tb.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Wi-Fi", xalign=0)
        t.get_style_context().add_class("title")
        self.status = Gtk.Label(label="Looking for Wi-Fi…", xalign=0)
        self.status.get_style_context().add_class("status")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        tb.pack_start(t, False, False, 0)
        tb.pack_start(self.status, False, False, 0)
        header.pack_start(tb, True, True, 0)
        self.power = Gtk.Switch()
        self.power.set_valign(Gtk.Align.CENTER)
        self._power_handler = self.power.connect("notify::active", self._toggle_power)
        header.pack_end(self.power, False, False, 0)
        root.pack_start(header, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.pack_start(sw, True, True, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        body.set_border_width(18)
        sw.add(body)

        self.err_label = Gtk.Label(xalign=0)
        self.err_label.set_line_wrap(True)
        self.err_label.get_style_context().add_class("slope-wifi-error")
        self.err_label.set_no_show_all(True)
        body.pack_start(self.err_label, False, False, 4)

        nh = Gtk.Box(spacing=8)
        nl = Gtk.Label(label="Networks", xalign=0)
        nl.get_style_context().add_class("slope-wifi-section")
        nh.pack_start(nl, False, False, 0)
        self.spinner = Gtk.Spinner()
        nh.pack_start(self.spinner, False, False, 0)
        scan = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        scan.set_tooltip_text("Scan for networks")
        scan.get_style_context().add_class("circular")
        scan.set_valign(Gtk.Align.CENTER)
        scan.connect("clicked", lambda *_: self._scan())
        nh.pack_end(scan, False, False, 0)
        body.pack_start(nh, False, False, 0)

        self.net_list = Gtk.ListBox()
        self.net_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.net_list.get_style_context().add_class("slope-wifi-card")
        self.net_list.connect("row-activated", self._row_activated)
        body.pack_start(self.net_list, False, False, 0)

        other = Gtk.Button(label="Other Network…")
        other.set_halign(Gtk.Align.START)
        other.set_margin_top(10)
        other.connect("clicked", lambda *_: self._hidden())
        body.pack_start(other, False, False, 0)

        kl = Gtk.Label(label="Known Networks", xalign=0)
        kl.get_style_context().add_class("slope-wifi-section")
        body.pack_start(kl, False, False, 0)
        self.known_list = Gtk.ListBox()
        self.known_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.known_list.get_style_context().add_class("slope-wifi-card")
        body.pack_start(self.known_list, False, False, 0)

        el = Gtk.Label(label="Ethernet", xalign=0)
        el.get_style_context().add_class("slope-wifi-section")
        body.pack_start(el, False, False, 0)
        self.eth_list = Gtk.ListBox()
        self.eth_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.eth_list.get_style_context().add_class("slope-wifi-card")
        body.pack_start(self.eth_list, False, False, 0)
        adv = Gtk.Button(label="Advanced Network Settings…")
        adv.set_halign(Gtk.Align.START)
        adv.set_margin_top(10)
        adv.connect("clicked", lambda *_: spawn(["nm-connection-editor"]))
        body.pack_start(adv, False, False, 0)

        self.iwd.connect("changed", lambda *_: self.refresh())
        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        self.refresh()
        GLib.timeout_add(700, self._first_scan)
        GLib.timeout_add_seconds(20, self._periodic)
        GLib.timeout_add_seconds(5, self._refresh_eth)
        self._refresh_eth()

    # -- helpers -------------------------------------------------------------
    def _device(self):
        return self.iwd.primary()

    def _first_scan(self):
        self._scan()
        return False

    def _periodic(self):
        if self.is_visible():
            self._scan()
        return True

    def _scan(self):
        d = self._device()
        if d and d["station"] and d["powered"]:
            self.iwd.scan(d["path"])

    def _show_error(self, text):
        self.error = text
        if text:
            self.err_label.set_text(text)
            self.err_label.show()
        else:
            self.err_label.hide()

    @staticmethod
    def _clear(lb):
        for c in lb.get_children():
            lb.remove(c)
            c.destroy()

    @staticmethod
    def _placeholder(lb, text):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("slope-wifi-dim")
        lbl.set_line_wrap(True)
        row.add(lbl)
        lb.add(row)

    # -- refresh -------------------------------------------------------------
    def refresh(self):
        d = self._device()
        self._clear(self.net_list)
        self._clear(self.known_list)
        self.power.handler_block(self._power_handler)
        if not self.iwd.running:
            self.status.set_text("The Wi-Fi service (iwd) is not running")
            self.power.set_sensitive(False)
            self.power.set_active(False)
            self._placeholder(self.net_list, "Start it with: sudo systemctl start iwd")
        elif d is None:
            self.status.set_text("No Wi-Fi hardware found")
            self.power.set_sensitive(False)
            self.power.set_active(False)
            self._placeholder(self.net_list, "This computer has no Wi-Fi adapter, or its driver or "
                                             "firmware is missing. Wired connections still work.")
        else:
            self.power.set_sensitive(True)
            self.power.set_active(d["powered"] and d["adapter_powered"])
            self.spinner.start() if d["scanning"] else self.spinner.stop()
            if not d["powered"]:
                self.status.set_text("Wi-Fi is off")
                self._placeholder(self.net_list, "Turn Wi-Fi on to see networks.")
            else:
                self._fill_networks(d)
            self.header_img.set_from_pixbuf(wifi_icon(64, self._bars(d), d["state"] == "connected"))
        self.power.handler_unblock(self._power_handler)
        self._fill_known()
        self.net_list.show_all()
        self.known_list.show_all()

    def _bars(self, d):
        s = self.iwd.connected_strength(d["path"])
        if s is None:
            return 4
        from .iwd import signal_bars
        return signal_bars(s)

    def _fill_networks(self, d):
        state = d["state"]
        if state == "connected":
            self.status.set_text("Connected to %s" % d["connected_name"])
            if self.connecting:
                self.connecting = None
                self._show_error(None)
        elif state == "connecting" and d["connected_name"]:
            # associated already: waiting for an address from the router (DHCP)
            self.status.set_text("Joined %s, getting an IP address…" % d["connected_name"])
        elif state == "connecting":
            self.status.set_text("Connecting…")
        elif state == "disconnecting":
            self.status.set_text("Disconnecting…")
        else:
            self.status.set_text("Not connected")
            # a join that ended without success: drop its spinner
            if self.connecting and GLib.get_monotonic_time() - self._join_started > 4000000:
                self.connecting = None
        nets = self.iwd.network_list(d["path"])
        if not nets:
            self._placeholder(self.net_list, "Scanning for networks…" if d["scanning"] else "No networks found.")
            return
        for n in nets:
            self.net_list.add(self._net_row(d, n))

    def _net_row(self, d, n):
        row = Gtk.ListBoxRow()
        row.net = n
        box = Gtk.Box(spacing=12)
        box.pack_start(SignalBars(n["bars"]), False, False, 0)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        name = Gtk.Label(label=n["name"], xalign=0)
        name.get_style_context().add_class("name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        v.pack_start(name, False, False, 0)
        sub = SECURITY_LABELS.get(n["type"], n["type"])
        if n["connected"]:
            sub = "Connected · " + sub
            row.get_style_context().add_class("connected")
        elif n["known"]:
            sub = "Saved · " + sub
        s = Gtk.Label(label=sub, xalign=0)
        s.get_style_context().add_class("slope-wifi-dim")
        v.pack_start(s, False, False, 0)
        box.pack_start(v, True, True, 0)
        if n["type"] != "open":
            box.pack_start(Gtk.Image.new_from_icon_name("channel-secure-symbolic", Gtk.IconSize.MENU),
                           False, False, 0)
        if self.connecting == n["path"] and not n["connected"]:
            sp = Gtk.Spinner()
            sp.start()
            box.pack_end(sp, False, False, 0)
        elif n["connected"]:
            b = Gtk.Button(label="Disconnect")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self.iwd.disconnect(d["path"]))
            box.pack_end(b, False, False, 0)
        else:
            b = Gtk.Button(label="Join")
            b.set_valign(Gtk.Align.CENTER)
            b.get_style_context().add_class("suggested-action" if n["known"] else "text-button")
            b.connect("clicked", lambda *_: self._join(n))
            box.pack_end(b, False, False, 0)
        row.add(box)
        return row

    def _fill_known(self):
        known = self.iwd.known_networks() if self.iwd.running else []
        if not known:
            self._placeholder(self.known_list, "Networks you join are remembered here.")
            return
        for k in known:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            box = Gtk.Box(spacing=12)
            box.pack_start(Gtk.Image.new_from_icon_name("network-wireless-symbolic", Gtk.IconSize.MENU),
                           False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            nm = Gtk.Label(label=k["name"], xalign=0)
            nm.set_ellipsize(Pango.EllipsizeMode.END)
            v.pack_start(nm, False, False, 0)
            sub = SECURITY_LABELS.get(k["type"], k["type"]) + (" · hidden" if k["hidden"] else "")
            s = Gtk.Label(label=sub, xalign=0)
            s.get_style_context().add_class("slope-wifi-dim")
            v.pack_start(s, False, False, 0)
            box.pack_start(v, True, True, 0)
            aj = Gtk.Box(spacing=6)
            aj.pack_start(Gtk.Label(label="Auto-Join"), False, False, 0)
            sw = Gtk.Switch()
            sw.set_active(k["autoconnect"])
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", lambda w, p, path=k["path"]: self.iwd.set_autoconnect(path, w.get_active()))
            aj.pack_start(sw, False, False, 0)
            box.pack_start(aj, False, False, 0)
            fb = Gtk.Button(label="Forget")
            fb.get_style_context().add_class("destructive-action")
            fb.set_valign(Gtk.Align.CENTER)
            fb.connect("clicked", lambda *_, kk=k: self._forget(kk))
            box.pack_end(fb, False, False, 0)
            row.add(box)
            self.known_list.add(row)

    def _refresh_eth(self):
        self._clear(self.eth_list)
        devs = ethernet_status()
        if not devs:
            self._placeholder(self.eth_list, "No wired network adapter found.")
        for dev, state, conn, ip in devs:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            box = Gtk.Box(spacing=12)
            ok = state.startswith("connected")
            box.pack_start(Gtk.Image.new_from_icon_name(
                "network-wired-symbolic" if ok else "network-wired-disconnected-symbolic",
                Gtk.IconSize.MENU), False, False, 0)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            v.pack_start(Gtk.Label(label="Ethernet (%s)" % dev, xalign=0), False, False, 0)
            if ok:
                txt = "Connected" + (" · %s" % ip if ip else "")
            elif state == "unavailable":
                txt = "Cable unplugged"
            elif state.startswith("connecting"):
                txt = "Connecting…"
            elif state == "unmanaged":
                txt = "Not managed"
            else:
                txt = "Not connected"
            s = Gtk.Label(label=txt, xalign=0)
            s.get_style_context().add_class("slope-wifi-dim")
            v.pack_start(s, False, False, 0)
            box.pack_start(v, True, True, 0)
            if not ok and state not in ("unavailable",):
                b = Gtk.Button(label="Connect")
                b.set_valign(Gtk.Align.CENTER)
                b.connect("clicked", lambda *_, dv=dev: spawn(["nmcli", "device", "connect", dv]))
                box.pack_end(b, False, False, 0)
            row.add(box)
            self.eth_list.add(row)
        self.eth_list.show_all()
        return True

    # -- actions -------------------------------------------------------------
    def _toggle_power(self, sw, _p):
        d = self._device()
        if d is not None:
            self.iwd.set_powered(d, sw.get_active())

    def _row_activated(self, lb, row):
        n = getattr(row, "net", None)
        if n is not None and not n["connected"]:
            self._join(n)

    def _join(self, n):
        self.connecting = n["path"]
        self._join_started = GLib.get_monotonic_time()
        self._show_error(None)
        self.refresh()

        def failed(msg):
            self.connecting = None
            if "Canceled" not in msg and "Aborted" not in msg:
                self._show_error("Couldn’t join “%s”: %s" % (n["name"], self._nice(msg)))
            self.refresh()
        self.iwd.connect_network(n["path"], on_error=failed)

    @staticmethod
    def _nice(msg):
        m = msg.lower()
        if "failed" in m and "auth" in m or "invalid" in m:
            return "the password may be wrong."
        if "timeout" in m or "timed out" in m:
            return "the network didn’t answer in time."
        if "busy" in m:
            return "Wi-Fi is busy, try again in a moment."
        return msg

    def _forget(self, k):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text="Forget the Wi-Fi network “%s”?" % k["name"])
        dlg.format_secondary_text("Slope will no longer join this network automatically, "
                                  "and its saved password is removed.")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        b = dlg.add_button("Forget", Gtk.ResponseType.OK)
        b.get_style_context().add_class("destructive-action")
        if dlg.run() == Gtk.ResponseType.OK:
            self.iwd.forget(k["path"])
        dlg.destroy()

    def _hidden(self):
        d = self._device()
        if d is None or not d["station"]:
            return
        dlg = Gtk.Dialog(title="Join Other Network", transient_for=self, modal=True)
        area = dlg.get_content_area()
        area.set_border_width(18)
        area.set_spacing(10)
        area.pack_start(Gtk.Label(label="Enter the name of a hidden Wi-Fi network.", xalign=0), False, False, 0)
        e = Gtk.Entry()
        e.set_placeholder_text("Network name")
        e.set_activates_default(True)
        area.pack_start(e, False, False, 0)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        j = dlg.add_button("Join", Gtk.ResponseType.OK)
        j.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        resp = dlg.run()
        ssid = e.get_text().strip()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK and ssid:
            self.iwd.connect_hidden(d["path"], ssid, on_error=lambda m: self._show_error(
                "Couldn’t join “%s”: %s" % (ssid, self._nice(m))))


def main():
    GLib.set_prgname("slope-wifi")
    app = Gio.Application(application_id="org.slope.Wifi", flags=Gio.ApplicationFlags.FLAGS_NONE)
    try:
        app.register(None)
    except GLib.Error:
        pass
    if app.get_is_remote():
        app.activate()
        return 0
    for data in (CSS,):
        prov = Gtk.CssProvider()
        prov.load_from_data(data)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    shell_css = os.path.join(SHARE, "shell.css")
    if os.path.exists(shell_css):
        p2 = Gtk.CssProvider()
        try:
            p2.load_from_path(shell_css)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), p2,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass
    win = WifiWindow()
    app.connect("activate", lambda *_: win.present())
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
