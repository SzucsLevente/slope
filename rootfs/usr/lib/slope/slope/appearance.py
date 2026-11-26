"""Light and dark appearance (System Settings > Appearance, top bar > Display).

"dark_mode" in settings.json.  Switching it changes, live:
  * GTK 3 apps (and Firefox, and Qt apps through their GTK theme): the
    Slope / Slope-Dark theme, through xsettingsd (~/.config/slope/xsettingsd.conf)
  * GTK 4 / libadwaita apps: org.gnome.desktop.interface color-scheme
  * window title bars: the Openbox theme (~/.config/slope/openbox-rc.xml)
  * Terminal colours
  * Slope's own top bar, Dock menus and popovers (the shell reads the setting)

prepare_session() runs from slope-session before xsettingsd and Openbox
start, so a new login comes up in the right look.  Light module (no GTK).
"""
import json
import os
import re
import subprocess

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slope")
SETTINGS_PATH = os.path.join(CONFIG, "settings.json")
XS_SYSTEM = "/usr/share/slope/xsettingsd.conf"
XS_USER = os.path.join(CONFIG, "xsettingsd.conf")
OB_SYSTEM = "/usr/share/slope/openbox/rc.xml"
OB_USER = os.path.join(CONFIG, "openbox-rc.xml")

TERMINAL = {
    False: {"color-foreground": "#1d1d1f", "color-background": "#fbfbfd", "color-cursor": "#2f86f3",
            "color-palette": "#1d1d1f;#c41a16;#007400;#826b28;#0b4bc2;#a90d91;#3e8087;#c7c7c7;"
                             "#686868;#e0443c;#28c840;#d79c19;#2f86f3;#c54fd8;#26a4b5;#ffffff"},
    True: {"color-foreground": "#e6e6ea", "color-background": "#1e1e21", "color-cursor": "#5aa8ff",
           "color-palette": "#2c2c30;#ff6b62;#5fd36c;#e3b341;#5aa8ff;#d282e8;#4fc4d2;#d0d0d6;"
                            "#6a6a72;#ff8a82;#7ee58a;#f2cc62;#82bfff;#e3a3f2;#78dbe6;#ffffff"},
}


def is_dark(settings=None):
    if settings is not None:
        return bool(settings.get("dark_mode", False))
    try:
        with open(SETTINGS_PATH) as f:
            return bool(json.load(f).get("dark_mode", False))
    except (OSError, ValueError):
        return False


def theme_name(dark):
    return "Slope-Dark" if dark else "Slope"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def write_xsettings(dark):
    try:
        with open(XS_SYSTEM) as f:
            text = f.read()
    except OSError:
        text = ""
    text = re.sub(r'^Net/ThemeName .*$', 'Net/ThemeName "%s"' % theme_name(dark), text, flags=re.M)
    text = re.sub(r'^Gtk/ApplicationPreferDarkTheme .*\n?', '', text, flags=re.M)
    text = text.rstrip("\n") + "\nGtk/ApplicationPreferDarkTheme %d\n" % int(dark)
    _write(XS_USER, "# Made by Slope from %s (dark mode: %s)\n" % (XS_SYSTEM, "on" if dark else "off") + text)


def write_openbox(dark):
    try:
        with open(OB_SYSTEM) as f:
            text = f.read()
    except OSError:
        return False
    text = re.sub(r"(<theme>\s*<name>)[^<]*(</name>)", r"\g<1>%s\g<2>" % theme_name(dark), text, count=1)
    _write(OB_USER, text)
    return True


def _run(argv):
    try:
        subprocess.run(argv, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def prepare_session():
    """Before xsettingsd and Openbox start (slope-session)."""
    dark = is_dark()
    write_xsettings(dark)
    write_openbox(dark)
    _gnome(dark)


def _gnome(dark):
    _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
          "prefer-dark" if dark else "default"])
    _run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", theme_name(dark)])


def _terminal(dark):
    for prop, val in TERMINAL[dark].items():
        _run(["xfconf-query", "-c", "xfce4-terminal", "-p", "/" + prop, "-n", "-t", "string", "-s", val])


def apply(dark):
    """Switch the running session."""
    write_xsettings(dark)
    _run(["pkill", "-HUP", "-x", "-u", str(os.getuid()), "xsettingsd"])
    if write_openbox(dark):
        _run(["openbox", "--reconfigure"])
    _gnome(dark)
    _terminal(dark)


def set_dark(dark):
    """Save the choice and switch."""
    try:
        with open(SETTINGS_PATH) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s["dark_mode"] = bool(dark)
    _write(SETTINGS_PATH, json.dumps(s, indent=2))
    apply(bool(dark))
