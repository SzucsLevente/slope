"""The compositor (picom) config for this user.

Windows minimize *into the Dock*, so the animation's target point follows the
Dock's position (bottom, left or right).  ~/.config/slope/picom.conf is made
from /usr/share/slope/picom.conf at login and whenever the Dock moves;
picom reloads it on SIGUSR1.  Light module (no GTK): slope-ctl imports it.
"""
import json
import os
import re
import subprocess

TEMPLATE = "/usr/share/slope/picom.conf"
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slope")
OUT = os.path.join(CONFIG, "picom.conf")
TARGETS = {
    "bottom": ("window-monitor-x + window-monitor-width / 2", "window-monitor-y + window-monitor-height - 36"),
    "left": ("window-monitor-x + 36", "window-monitor-y + window-monitor-height / 2"),
    "right": ("window-monitor-x + window-monitor-width - 36", "window-monitor-y + window-monitor-height / 2"),
}


def dock_position():
    try:
        with open(os.path.join(CONFIG, "settings.json")) as f:
            pos = json.load(f).get("dock_position", "bottom")
    except (OSError, ValueError):
        pos = "bottom"
    return pos if pos in TARGETS else "bottom"


def write(position=None, reload=True):
    pos = position or dock_position()
    tx, ty = TARGETS[pos]
    try:
        with open(TEMPLATE) as f:
            text = f.read()
    except OSError:
        return
    text = re.sub(r'var-tx = "[^"]*";', 'var-tx = "%s";' % tx, text)
    text = re.sub(r'var-ty = "[^"]*";', 'var-ty = "%s";' % ty, text)
    text = ("# Made by Slope from %s (Dock: %s). Changes here are overwritten;\n"
            "# copy the file elsewhere to customise picom.\n" % (TEMPLATE, pos)) + text
    try:
        os.makedirs(CONFIG, exist_ok=True)
        try:
            with open(OUT) as f:
                if f.read() == text:
                    return
        except OSError:
            pass
        with open(OUT + ".tmp", "w") as f:
            f.write(text)
        os.replace(OUT + ".tmp", OUT)
    except OSError:
        return
    if reload:
        subprocess.run(["pkill", "-USR1", "-x", "picom"], capture_output=True)
