"""macOS-style app lifetime: apps that keep running with no windows.

GTK apps get this from libslope-keepalive.so (packages/keepalive), which is
preloaded into the session.  Each such app announces itself with a small file
$XDG_RUNTIME_DIR/slope-apps/<pid>:

    id=org.gnome.Calculator
    path=/org/gnome/Calculator
    desktop=/usr/share/applications/org.gnome.Calculator.desktop
    exe=/usr/bin/gnome-calculator
    unique=0        (1: opening the app again goes to this copy;
                     0: it starts a new copy, and this one is ended)

For these apps the red close button really closes the window, and the app
stays running.  To quit one, create <pid>.quit next to it and close its
windows; it exits once no window is left.

Slope's own Python apps (Settings, Wi-Fi) do the same through register().
This module must stay light: slope-ctl imports it on every close.
"""
import os

DIR = (os.path.join(os.environ["XDG_RUNTIME_DIR"], "slope-apps")
       if os.environ.get("XDG_RUNTIME_DIR") else "/tmp/slope-apps-%d" % os.getuid())


def _alive(pid):
    try:
        return os.stat("/proc/%d" % pid).st_uid == os.getuid()
    except OSError:
        return False


def apps():
    """{pid: {"id":…, "path":…, "desktop":…, "exe":…}} for running kept-alive apps."""
    out = {}
    try:
        names = os.listdir(DIR)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        p = os.path.join(DIR, name)
        if not _alive(pid):
            for stale in (p, p + ".quit"):
                try:
                    os.unlink(stale)
                except OSError:
                    pass
            continue
        info = {}
        try:
            with open(p) as f:
                for line in f:
                    k, _, v = line.rstrip("\n").partition("=")
                    info[k] = v
        except OSError:
            continue
        out[pid] = info
    return out


def retire_windowless(appinfo, window_pids):
    """Before starting an app again: copies of it that allow several
    instances (unique=0) and have no windows left are ended, so opening the
    app gives a fresh window instead of a second hidden copy.  They are asked
    to quit (so they can save their settings), not killed."""
    import time
    keys = {(appinfo.get_id() or "")[:-len(".desktop")].lower(), (appinfo.get_filename() or "")}
    old = []
    for pid, info in apps().items():
        if info.get("unique") != "0" or pid in window_pids:
            continue
        if (info.get("id") or "").lower() in keys or info.get("desktop") in keys:
            request_quit(pid)
            old.append(pid)
    # wait (briefly) until they are gone, so the new copy does not hand its
    # window over to an old one that is just quitting
    end = time.monotonic() + 2.0
    while old and time.monotonic() < end:
        time.sleep(0.05)
        old = [p for p in old if _alive(p)]


def is_kept(pid):
    return bool(pid) and os.path.exists(os.path.join(DIR, str(pid))) and _alive(pid)


def request_quit(pid):
    """Ask a kept-alive app to quit once its windows are closed."""
    try:
        with open(os.path.join(DIR, "%d.quit" % pid), "w"):
            pass
    except OSError:
        pass


def quit_requested(pid=None):
    return os.path.exists(os.path.join(DIR, "%d.quit" % (pid or os.getpid())))


def register(app_id, desktop=""):
    """For Slope's own Python apps: announce this process as kept alive."""
    try:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        p = os.path.join(DIR, str(os.getpid()))
        with open(p, "w") as f:
            f.write("id=%s\npath=\ndesktop=%s\nexe=\n" % (app_id, desktop))
        try:
            os.unlink(p + ".quit")
        except OSError:
            pass
        import atexit
        atexit.register(unregister)
    except OSError:
        pass


def unregister():
    p = os.path.join(DIR, str(os.getpid()))
    for f in (p, p + ".quit"):
        try:
            os.unlink(f)
        except OSError:
            pass


def keep_window(win, app_id, desktop=""):
    """Make a Gtk.Window behave like a Mac app window: closing it hides it,
    the process stays (and shows as running in the Dock) until it is asked
    to quit.  The app's activate/command-line handler presents it again."""
    from gi.repository import GLib, Gtk
    register(app_id, desktop)

    def on_delete(w, ev):
        if quit_requested():
            Gtk.main_quit()
            return True
        w.hide()
        return True

    def poll():
        if quit_requested() and not win.get_visible():
            Gtk.main_quit()
            return False
        return True

    win.connect("delete-event", on_delete)
    GLib.timeout_add(500, poll)
