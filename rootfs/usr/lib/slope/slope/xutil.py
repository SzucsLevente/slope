"""Raw X11/EWMH helpers (python-xlib only, no GTK) shared by slope-ctl and the shell."""


class X11:
    """Tiny EWMH helper.  Kept separate from GTK so slope-ctl starts fast."""

    def __init__(self):
        from Xlib import X, Xatom, display
        from Xlib.protocol import event
        self.X, self.Xatom, self.event = X, Xatom, event
        self.d = display.Display()
        self.root = self.d.screen().root
        self._atoms = {}

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def win(self, xid):
        return self.d.create_resource_object("window", xid)

    def prop_str(self, xid, name):
        try:
            p = self.win(xid).get_full_property(self.atom(name), self.X.AnyPropertyType)
        except Exception:
            return None
        if not p or p.format != 8:
            return None
        v = p.value
        return (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)) or None

    def prop(self, xid, name, typ=None):
        try:
            p = self.win(xid).get_full_property(self.atom(name), typ or self.X.AnyPropertyType)
            return list(p.value) if p else []
        except Exception:
            return []

    def active(self):
        v = self.prop(self.root.id, "_NET_ACTIVE_WINDOW")
        return v[0] if v and v[0] else None

    def clients(self):
        return self.prop(self.root.id, "_NET_CLIENT_LIST_STACKING") or self.prop(self.root.id, "_NET_CLIENT_LIST")

    def wm_class(self, xid):
        try:
            c = self.win(xid).get_wm_class()
            return (c[0] if c else "", c[1] if c else "")
        except Exception:
            return ("", "")

    def types(self, xid):
        return [self.d.get_atom_name(a) for a in self.prop(xid, "_NET_WM_WINDOW_TYPE")]

    def states(self, xid):
        return [self.d.get_atom_name(a) for a in self.prop(xid, "_NET_WM_STATE")]

    def transient_for(self, xid):
        try:
            t = self.win(xid).get_wm_transient_for()
            return t.id if t else None
        except Exception:
            return None

    def pid(self, xid):
        v = self.prop(xid, "_NET_WM_PID")
        return v[0] if v else None

    def is_normal(self, xid):
        t = self.types(xid)
        return (not t or "_NET_WM_WINDOW_TYPE_NORMAL" in t) and not self.transient_for(xid)

    def is_hidden(self, xid):
        return bool(self.prop(xid, "_SLOPE_HIDDEN"))

    def is_minimized(self, xid):
        return "_NET_WM_STATE_HIDDEN" in self.states(xid)

    def _client_msg(self, xid, typ, data):
        ev = self.event.ClientMessage(window=self.win(xid), client_type=self.atom(typ),
                                      data=(32, (data + [0] * 5)[:5]))
        mask = self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask
        self.root.send_event(ev, event_mask=mask)

    def close(self, xid):
        self._client_msg(xid, "_NET_CLOSE_WINDOW", [self.X.CurrentTime, 2])

    def iconify(self, xid):
        self._client_msg(xid, "WM_CHANGE_STATE", [3])  # IconicState

    def set_state(self, xid, add, state):
        self._client_msg(xid, "_NET_WM_STATE", [1 if add else 0, self.atom(state), 0, 2])

    def activate(self, xid):
        self._client_msg(xid, "_NET_ACTIVE_WINDOW", [2, self.X.CurrentTime, 0])

    def mark_hidden(self, xid, hidden=True):
        w = self.win(xid)
        if hidden:
            w.change_property(self.atom("_SLOPE_HIDDEN"), self.Xatom.CARDINAL, 32, [1])
        else:
            w.delete_property(self.atom("_SLOPE_HIDDEN"))
        self.set_state(xid, hidden, "_NET_WM_STATE_SKIP_TASKBAR")

    def hide(self, xid):
        """macOS-style 'close': the window goes away, the app keeps running.

        _SLOPE_CLOSED tells the compositor to animate this like a real close
        (fade out where it is) and later like a real open, instead of the
        minimize animation into the Dock.  The shell removes it once the
        window is back on screen."""
        self.win(xid).change_property(self.atom("_SLOPE_CLOSED"), self.Xatom.CARDINAL, 32, [1])
        self.mark_hidden(xid, True)
        self.iconify(xid)

    def is_closed(self, xid):
        return bool(self.prop(xid, "_SLOPE_CLOSED"))

    def clear_closed(self, xid):
        try:
            self.win(xid).delete_property(self.atom("_SLOPE_CLOSED"))
        except Exception:
            pass

    def unhide(self, xid):
        self.mark_hidden(xid, False)

    def app_windows(self, xid):
        cls = self.wm_class(xid)[1]
        out = []
        for c in self.clients():
            if self.wm_class(c)[1] == cls:
                out.append(c)
        return out

    def flush(self):
        self.d.sync()
