"""Screen resolution and brightness.

Resolutions chosen in System Settings > Displays are saved in settings.json
("displays": {"eDP-1": "1440x900"}) and applied again at every login
(slope-ctl display); before, xrandr changes were forgotten at log out.

Brightness: laptops (a MacBook's built-in screen...) have a backlight in
/sys/class/backlight, set through logind (no root needed, like GNOME does).
Screens without one (external monitors, virtual machines) are dimmed in
software with xrandr --brightness, saved as "brightness_sw" and restored at
login.  Light module (no GTK): slope-ctl imports it.
"""
import json
import os
import re
import subprocess

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slope")
SETTINGS_PATH = os.path.join(CONFIG, "settings.json")
BACKLIGHT = "/sys/class/backlight"
LEDS = "/sys/class/leds"
MIN_PERCENT = 3          # never switch the screen off completely
MIN_SOFTWARE = 25        # software dimming below this is unreadable


def _load():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _update(key, value):
    s = _load()
    s[key] = value
    os.makedirs(CONFIG, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


# -- resolutions ------------------------------------------------------------
def outputs():
    """[{"name", "modes": [...], "current"}] for every connected screen."""
    try:
        out = subprocess.run(["xrandr", "-q"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res, cur = [], None
    for line in out.splitlines():
        m = re.match(r"^(\S+) connected", line)
        if m:
            cur = {"name": m.group(1), "modes": [], "current": None}
            res.append(cur)
            continue
        m = re.match(r"^\s+(\d+x\d+i?)\s+(.*)$", line)
        if m and cur is not None:
            if m.group(1) not in cur["modes"]:
                cur["modes"].append(m.group(1))
            if "*" in m.group(2):
                cur["current"] = m.group(1)
        elif not line.startswith(" "):
            cur = None
    return res


def set_mode(output, mode, save=True):
    subprocess.run(["xrandr", "--output", output, "--mode", mode], capture_output=True, timeout=15)
    if save:
        d = _load().get("displays") or {}
        d[output] = mode
        _update("displays", d)


def apply():
    """At login: saved resolutions, then software brightness."""
    saved = _load().get("displays") or {}
    for o in outputs():
        mode = saved.get(o["name"])
        if mode and mode in o["modes"] and mode != o["current"]:
            set_mode(o["name"], mode, save=False)
    if backlight() is None:
        pct = _load().get("brightness_sw")
        if pct is not None and int(pct) < 100:
            _software(int(pct))


# -- brightness ---------------------------------------------------------------
def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def backlight():
    """The backlight device to use: name, or None if this screen has none."""
    try:
        names = os.listdir(BACKLIGHT)
    except OSError:
        return None
    rank = {"firmware": 0, "platform": 1, "raw": 2}
    best = None
    for n in names:
        try:
            with open(os.path.join(BACKLIGHT, n, "type")) as f:
                t = f.read().strip()
        except OSError:
            t = "raw"
        # MacBooks with two graphics chips switch the backlight through gmux
        r = -1 if n.startswith("gmux") else rank.get(t, 3)
        if best is None or r < best[0]:
            best = (r, n)
    return best[1] if best else None


def _logind_set(subsystem, name, value):
    """Set a backlight / LED through logind; falls back to writing sysfs."""
    try:
        r = subprocess.run(["busctl", "call", "--system", "org.freedesktop.login1",
                            "/org/freedesktop/login1/session/auto", "org.freedesktop.login1.Session",
                            "SetBrightness", "ssu", subsystem, name, str(int(value))],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        base = BACKLIGHT if subsystem == "backlight" else LEDS
        with open(os.path.join(base, name, "brightness"), "w") as f:
            f.write(str(int(value)))
        return True
    except OSError:
        return False


def get_brightness():
    """Brightness in percent (0-100)."""
    dev = backlight()
    if dev is None:
        return int(_load().get("brightness_sw", 100))
    cur = _read_int(os.path.join(BACKLIGHT, dev, "brightness"))
    mx = _read_int(os.path.join(BACKLIGHT, dev, "max_brightness")) or 1
    if cur is None:
        return 100
    return round(cur * 100.0 / mx)


def _software(pct):
    for o in outputs():
        subprocess.run(["xrandr", "--output", o["name"], "--brightness", "%.2f" % (pct / 100.0)],
                       capture_output=True, timeout=10)


def set_brightness(pct):
    dev = backlight()
    if dev is None:
        pct = max(MIN_SOFTWARE, min(100, int(round(pct))))
        _software(pct)
        _update("brightness_sw", pct)
        return pct
    pct = max(MIN_PERCENT, min(100, pct))
    mx = _read_int(os.path.join(BACKLIGHT, dev, "max_brightness")) or 1
    _logind_set("backlight", dev, max(1, round(mx * pct / 100.0)))
    return int(round(pct))


def keyboard_light():
    try:
        for n in sorted(os.listdir(LEDS)):
            if "kbd_backlight" in n:
                return n
    except OSError:
        pass
    return None


def _osd(icon, title, pct):
    # xfce4-notifyd shows a level bar for "value"; the hint keeps a single bubble
    subprocess.Popen(["notify-send", "-t", "1200", "-i", icon,
                      "-h", "int:value:%d" % pct, "-h", "string:x-canonical-private-synchronous:" + title,
                      title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cli(cmd, args):
    step = 100 / 16.0      # 16 steps, like a Mac
    if cmd == "brightness":
        cur = get_brightness()
        arg = args[0] if args else "up"
        if arg == "up":
            new = cur + step
        elif arg == "down":
            new = cur - step
        else:
            new = float(arg)
        pct = set_brightness(new)
        _osd("display-brightness-symbolic", "Brightness", pct)
        return 0
    dev = keyboard_light()
    if dev is None:
        return 1
    mx = _read_int(os.path.join(LEDS, dev, "max_brightness")) or 1
    cur = _read_int(os.path.join(LEDS, dev, "brightness")) or 0
    pct = cur * 100.0 / mx + (step if (args[:1] or ["up"])[0] == "up" else -step)
    pct = max(0, min(100, pct))
    _logind_set("leds", dev, round(mx * pct / 100.0))
    _osd("keyboard-brightness-symbolic", "Keyboard Brightness", round(pct))
    return 0
