"""slope-ctl: window actions bound to titlebar buttons and keyboard shortcuts.

  close-window   red button / Super+W / Alt+F4.  Closes the window, but if it is
                 the app's last window the window is only hidden: the app keeps
                 running in the dock, exactly like on macOS.
  quit-app       Super+Q: closes every window of the focused app (really quits).
  hide-app       Super+H: hides all windows of the focused app.
  hide-others    Super+Alt+H
  minimize       Super+M
"""
import os
import sys

# apps that should really quit when their last window is closed
QUIT_ON_CLOSE = {"slope-settings", "slope-spotlight", "slope-about", "slope-forcequit",
                 "slope-welcome", "slope-launchpad", "slope-wifi", "calamares"}


def _setting(key, default):
    try:
        import json
        p = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "slope", "settings.json")
        with open(p) as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError):
        return default


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    from .xutil import X11
    x = X11()
    cmd = argv[0]
    act = int(argv[1], 0) if len(argv) > 1 else x.active()
    if not act:
        return 0
    types = x.types(act)
    if any(t in types for t in ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")):
        return 0
    res_name, res_class = x.wm_class(act)

    if cmd == "close-window":
        if not _setting("close_hides_app", True) or res_name in QUIT_ON_CLOSE \
                or res_class.lower() in QUIT_ON_CLOSE or not x.is_normal(act):
            x.close(act)
        else:
            siblings = [w for w in x.app_windows(act)
                        if w != act and x.is_normal(w) and not x.is_hidden(w)
                        and not x.is_minimized(w)]
            if siblings:
                x.close(act)      # other windows remain: just close this one
            else:
                x.hide(act)       # last window: hide it, the app stays alive
    elif cmd == "quit-app":
        for w in x.app_windows(act):
            x.close(w)
    elif cmd == "hide-app":
        for w in x.app_windows(act):
            if x.is_normal(w):
                x.hide(w)
    elif cmd == "hide-others":
        mine = set(x.app_windows(act))
        for w in x.clients():
            if w not in mine and x.is_normal(w) and \
                    not any(t in x.types(w) for t in ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")):
                x.hide(w)
    elif cmd == "minimize":
        x.iconify(act)
    else:
        print("unknown command:", cmd, file=sys.stderr)
        return 2
    x.flush()
    return 0
