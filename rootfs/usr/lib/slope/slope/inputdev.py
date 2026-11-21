"""Trackpad, mouse and gesture settings.

Settings live in ~/.config/slope/settings.json (keys below).  apply() pushes
them to every trackpad and mouse through xinput (libinput driver), and writes
~/.config/slope/gestures.conf for the gesture daemon (slope-gestures).
It runs at login (slope-ctl input), whenever a device is plugged in (the
shell), and whenever something changes in System Settings.

This module must stay light (no GTK): slope-ctl imports it.
"""
import json
import os
import re
import subprocess

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slope")
SETTINGS_PATH = os.path.join(CONFIG, "settings.json")
GESTURES_PATH = os.path.join(CONFIG, "gestures.conf")

DEFAULTS = {
    # one setting for every pointing device (trackpad, mouse, a VM's pointer),
    # like on a Mac: the content follows the fingers / the wheel
    "natural_scroll": True,
    "tp_tap": True,             # tap to click
    "tp_click": "clickfinger",  # secondary click: "clickfinger" (two fingers) or "areas" (corner)
    "tp_scroll": "twofinger",   # "twofinger" or "edge"
    "tp_speed": 0.0,            # pointer speed -1 .. 1
    "tp_scroll_speed": 15,      # libinput scrolling pixel distance: lower = faster
    "tp_dwt": True,             # ignore the trackpad while typing
    "mouse_speed": 0.0,
    "gestures": {
        "swipe-up-3": "launchpad",
        "swipe-down-3": "show-desktop",
        "swipe-left-3": "forward",
        "swipe-right-3": "back",
        "swipe-up-4": "spotlight",
        "swipe-down-4": "hide-app",
        "swipe-left-4": "next-app",
        "swipe-right-4": "previous-app",
        "pinch-in-4": "launchpad",
        "pinch-out-4": "show-desktop",
        "pinch-in-3": "none",
        "pinch-out-3": "none",
    },
}

# gesture -> label shown in System Settings, in display order
GESTURES = [
    ("swipe-up-3", "Swipe up with three fingers"),
    ("swipe-down-3", "Swipe down with three fingers"),
    ("swipe-left-3", "Swipe left with three fingers"),
    ("swipe-right-3", "Swipe right with three fingers"),
    ("swipe-up-4", "Swipe up with four fingers"),
    ("swipe-down-4", "Swipe down with four fingers"),
    ("swipe-left-4", "Swipe left with four fingers"),
    ("swipe-right-4", "Swipe right with four fingers"),
    ("pinch-in-4", "Pinch with four fingers"),
    ("pinch-out-4", "Spread with four fingers"),
    ("pinch-in-3", "Pinch with three fingers"),
    ("pinch-out-3", "Spread with three fingers"),
]

# action id -> (label, command)
ACTIONS = {
    "none": ("Nothing", None),
    "launchpad": ("Open Launchpad", "slope-launchpad"),
    "spotlight": ("Spotlight Search", "slope-spotlight"),
    "show-desktop": ("Show Desktop", "slope-ctl show-desktop"),
    "next-app": ("Switch to the next app", "slope-ctl switch next"),
    "previous-app": ("Switch to the previous app", "slope-ctl switch previous"),
    "back": ("Back (browsers, Files)", "slope-ctl key alt+Left"),
    "forward": ("Forward (browsers, Files)", "slope-ctl key alt+Right"),
    "next-tab": ("Next tab", "slope-ctl key ctrl+Tab"),
    "previous-tab": ("Previous tab", "slope-ctl key ctrl+shift+Tab"),
    "zoom-in": ("Zoom in", "slope-ctl key ctrl+plus"),
    "zoom-out": ("Zoom out", "slope-ctl key ctrl+minus"),
    "minimize": ("Minimize window", "slope-ctl minimize"),
    "maximize": ("Zoom window (maximize)", "slope-ctl maximize"),
    "close-window": ("Close window", "slope-ctl close-window"),
    "hide-app": ("Hide app", "slope-ctl hide-app"),
    "quit-app": ("Quit app", "slope-ctl quit-app"),
    "volume-up": ("Volume up", "pactl set-sink-volume @DEFAULT_SINK@ +10%"),
    "volume-down": ("Volume down", "pactl set-sink-volume @DEFAULT_SINK@ -10%"),
}


def load():
    data = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_PATH) as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    for k in DEFAULTS:
        if k in user:
            if k == "gestures" and isinstance(user[k], dict):
                data[k].update(user[k])
            else:
                data[k] = user[k]
    return data


# -- xinput ------------------------------------------------------------------
def _xinput(*args):
    try:
        return subprocess.run(["xinput", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def devices():
    """[(id, name, kind)] for every pointing device: kind "touchpad", "mouse"
    (libinput) or "other" (a driver without libinput's settings)."""
    out = []
    for line in _xinput("list").splitlines():
        m = re.search(r"↳?\s*(.+?)\s+id=(\d+)\s+\[slave\s+pointer", line)
        if not m:
            continue
        name, dev = m.group(1).strip("⎜↳ \t"), m.group(2)
        if "XTEST" in name:
            continue
        props = _xinput("list-props", dev)
        if "libinput Natural Scrolling Enabled" not in props:
            out.append((dev, name, "other"))
            continue
        kind = "touchpad" if "libinput Tapping Enabled (" in props else "mouse"
        out.append((dev, name, kind))
    return out


def _button_map_natural(dev, on):
    """Fallback for drivers without a natural-scrolling setting: swap the
    scroll "buttons" (4/5 up/down, 6/7 left/right)."""
    current = _xinput("get-button-map", dev).split()
    if len(current) < 7:
        return
    m = [str(i) for i in range(1, len(current) + 1)]
    if on:
        m[3], m[4], m[5], m[6] = "5", "4", "7", "6"
    _xinput("set-button-map", dev, *m)


def has_touchpad():
    return any(k == "touchpad" for _d, _n, k in devices())


def _set(dev, prop, *values):
    _xinput("set-prop", dev, prop, *[str(v) for v in values])


def apply(settings=None):
    s = settings or load()
    natural = int(bool(s["natural_scroll"]))
    for dev, _name, kind in devices():
        if kind == "other":
            _button_map_natural(dev, natural)
            continue
        _set(dev, "libinput Natural Scrolling Enabled", natural)
        if kind == "touchpad":
            _set(dev, "libinput Tapping Enabled", int(bool(s["tp_tap"])))
            _set(dev, "libinput Click Method Enabled",
                 *((0, 1) if s["tp_click"] == "clickfinger" else (1, 0)))
            _set(dev, "libinput Scroll Method Enabled",
                 *((1, 0, 0) if s["tp_scroll"] == "twofinger" else (0, 1, 0)))
            _set(dev, "libinput Accel Speed", float(s["tp_speed"]))
            _set(dev, "libinput Scrolling Pixel Distance", int(s["tp_scroll_speed"]))
            _set(dev, "libinput Disable While Typing Enabled", int(bool(s["tp_dwt"])))
        else:
            _set(dev, "libinput Accel Speed", float(s["mouse_speed"]))
    write_gestures(s)


def write_gestures(settings=None):
    s = settings or load()
    lines = ["# Written by Slope System Settings -> Trackpad. Read by slope-gestures.",
             "# <gesture> = <shell command>"]
    for g, _label in GESTURES:
        action = s["gestures"].get(g, "none")
        if action in ACTIONS:
            cmd = ACTIONS[action][1]
        elif action.startswith("cmd:"):
            cmd = action[4:].strip()
        else:
            cmd = None
        if cmd:
            lines.append("%s = %s" % (g, cmd))
    text = "\n".join(lines) + "\n"
    try:
        os.makedirs(CONFIG, exist_ok=True)
        try:
            with open(GESTURES_PATH) as f:
                if f.read() == text:
                    return
        except OSError:
            pass
        tmp = GESTURES_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, GESTURES_PATH)
    except OSError:
        pass
