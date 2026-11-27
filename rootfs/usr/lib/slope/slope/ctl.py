"""slope-ctl: window actions bound to titlebar buttons and keyboard shortcuts.

  close-window   red button / Super+W / Alt+F4.  Closes the window; the app keeps
                 running in the dock, like on macOS.  Apps that can live without
                 windows (GTK apps, through libslope-keepalive) really close the
                 window and open a fresh one next time.  For other apps the
                 last window is hidden instead.
  quit-app       Super+Q: really quits the focused app.
  hide-app       Super+H: hides all windows of the focused app.
  hide-others    Super+Alt+H
  minimize       Super+M
  maximize       zoom / unzoom the focused window
  show-desktop   show the desktop (again: bring the windows back)
  switch next|previous   switch to the next / previous app window
  key COMBO      press a key combination, e.g. "alt+Left" (used by gestures)
  input          apply trackpad, mouse and gesture settings (at login)
  keyboard       apply keyboard layouts and key options (at login)
  display        apply the saved screen resolutions and brightness (at login)
  appearance     write the light / dark theme files (at login, before Openbox)
  dark on|off|toggle           switch dark mode
  brightness up|down|N         screen brightness (brightness keys)
  kbd-brightness up|down       keyboard backlight (MacBook keys)
  picom-config   write ~/.config/slope/picom.conf (Dock position for animations)
"""
import os
import sys

# Every app keeps running when its last window is closed.  These are only
# Slope's own panels (not apps), so closing them really closes them.
QUIT_ON_CLOSE = {"slope-spotlight", "slope-about", "slope-forcequit",
                 "slope-welcome", "slope-launchpad"}


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
    cmd = argv[0]
    if cmd == "input":
        from . import inputdev
        inputdev.apply()
        return 0
    if cmd == "keyboard":
        from . import keyboard
        keyboard.apply()
        return 0
    if cmd == "appearance":
        from . import appearance
        appearance.prepare_session()
        return 0
    if cmd == "dark":
        from . import appearance
        arg = argv[1] if len(argv) > 1 else "toggle"
        appearance.set_dark(not appearance.is_dark() if arg == "toggle" else arg == "on")
        return 0
    if cmd == "display":
        from . import display
        display.apply()
        return 0
    if cmd in ("brightness", "kbd-brightness"):
        from . import display
        return display.cli(cmd, argv[1:])
    if cmd == "picom-config":
        from . import picomconf
        picomconf.write(reload=False)
        return 0
    from .xutil import X11
    x = X11()
    if cmd == "key" and len(argv) > 1:
        x.send_keys(argv[1])
        return 0
    if cmd == "show-desktop":
        x.show_desktop(not x.showing_desktop())
        x.flush()
        return 0
    if cmd == "switch":
        wins = [w for w in x.stacking() if x.is_normal(w) and not x.is_hidden(w)
                and not x.is_minimized(w) and x.wm_class(w)[1] != "Slope"]
        if len(wins) > 1:
            if len(argv) > 1 and argv[1].startswith("prev"):
                x.lower(wins[-1])           # send the front window to the back
                x.activate(wins[-2])
            else:
                x.activate(wins[0])         # bring the one at the back forward
        x.flush()
        return 0
    act = int(argv[1], 0) if len(argv) > 1 else x.active()
    if not act:
        return 0
    types = x.types(act)
    if any(t in types for t in ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")):
        return 0
    res_name, res_class = x.wm_class(act)

    from . import keepalive
    if cmd == "close-window":
        pid = x.pid(act)
        if keepalive.is_kept(pid):
            # the app runs on without windows: just close this one for real
            if not _setting("close_hides_app", True) and x.is_normal(act) and not [
                    w for w in x.app_windows(act) if w != act and x.is_normal(w)]:
                keepalive.request_quit(pid)
            x.close(act)
        elif not _setting("close_hides_app", True) or res_name in QUIT_ON_CLOSE \
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
        wins = x.app_windows(act)
        for pid in {x.pid(w) for w in wins}:
            if keepalive.is_kept(pid):
                keepalive.request_quit(pid)
        for w in wins:
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
    elif cmd == "maximize":
        x.toggle_maximized(act)
    else:
        print("unknown command:", cmd, file=sys.stderr)
        return 2
    x.flush()
    return 0
