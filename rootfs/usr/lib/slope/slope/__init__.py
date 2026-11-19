"""The Slope desktop shell.

Pin the GObject-introspection versions up front: systems that also ship GTK 4
would otherwise hand us Gdk 4.0 when a module imports Gdk first.
"""
import os as _os

# The session loads appmenu-gtk-module into every GTK app so their menu bars move
# to the top bar.  Our own process must not load it, or it would hide the top
# bar's own menus.
_ORIG_ENV = {k: _os.environ.get(k) for k in ("GTK_MODULES", "UBUNTU_MENUPROXY")}
_mods = [m for m in _os.environ.get("GTK_MODULES", "").split(":") if m and "appmenu" not in m]
if _mods:
    _os.environ["GTK_MODULES"] = ":".join(_mods)
else:
    _os.environ.pop("GTK_MODULES", None)
_os.environ.pop("UBUNTU_MENUPROXY", None)

try:
    import gi
    for _ns, _ver in (("Gtk", "3.0"), ("Gdk", "3.0"), ("GdkX11", "3.0"), ("Wnck", "3.0"),
                      ("PangoCairo", "1.0"), ("Pango", "1.0"), ("GdkPixbuf", "2.0")):
        try:
            gi.require_version(_ns, _ver)
        except ValueError:
            pass
except ImportError:      # slope-ctl only needs python-xlib
    pass


def restore_app_env():
    """Call once GTK is initialised: apps we launch must get the global-menu module back."""
    for k, v in _ORIG_ENV.items():
        if v is not None:
            _os.environ[k] = v
