"""Light and dark appearance (System Settings > Appearance, top bar > Display).

"dark_mode", "accent" (a preset name or "#rrggbb") and "window_buttons"
("colorful", "graphite", "accent") in settings.json.  With the default blue
and colourful buttons the system themes Slope / Slope-Dark are used; any
other choice builds a personal copy in ~/.local/share/themes (GTK 3 colours
mixed from the accent, title buttons drawn in the chosen colours).
Switching any of them changes, live:
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


ACCENTS = [
    ("blue", "Blue", "#2f86f3"),
    ("purple", "Purple", "#9b5cf0"),
    ("pink", "Pink", "#ef4f9b"),
    ("red", "Red", "#e5443c"),
    ("orange", "Orange", "#f58a1f"),
    ("yellow", "Yellow", "#e9b40f"),
    ("green", "Green", "#32b04b"),
    ("graphite", "Graphite", "#8a919c"),
]
BUTTON_STYLES = [("colorful", "Colorful"), ("graphite", "Graphite"), ("accent", "Accent Color"),
                 ("custom", "Custom")]


def button_colors(settings=None):
    """The colours of the three title buttons in the "custom" style."""
    from .art import DEFAULT_BUTTON_COLORS
    s = settings if settings is not None else _settings()
    out = dict(DEFAULT_BUTTON_COLORS)
    for k, v in (s.get("button_colors") or {}).items():
        if k in out and re.match(r"^#[0-9a-fA-F]{6}$", str(v)):
            out[k] = v.lower()
    return out
USER_THEMES = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "themes")
SYS_THEMES = "/usr/share/themes"


def _settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def accent_color(settings=None):
    s = settings if settings is not None else _settings()
    a = s.get("accent", "blue") or "blue"
    if re.match(r"^#[0-9a-fA-F]{6}$", a):
        return a.lower()
    return dict((k, c) for k, _l, c in ACCENTS).get(a, "#2f86f3")


def customized(settings=None):
    s = settings if settings is not None else _settings()
    return accent_color(s) != "#2f86f3" or s.get("window_buttons", "colorful") != "colorful"


def _current_theme_name():
    try:
        with open(XS_USER) as f:
            m = re.search(r'^Net/ThemeName "([^"]*)"', f.read(), re.M)
            return m.group(1) if m else ""
    except OSError:
        return ""


def theme_name(dark, settings=None, fresh=False):
    """The theme to use.  fresh: a new name for a rebuilt personal theme, so
    running apps load it again (they only re-read a theme whose name changes)."""
    base = "Slope-Dark" if dark else "Slope"
    if not customized(settings):
        return base
    cur = _current_theme_name()
    n = 1
    if cur.startswith(base + "-Custom-"):
        n = int(cur.rsplit("-", 1)[1]) if cur.rsplit("-", 1)[1].isdigit() else 1
        if fresh:
            n = 2 if n == 1 else 1
    elif fresh:
        n = 1
    return "%s-Custom-%d" % (base, n)


def build_theme(name, dark, settings=None):
    """Write the personal theme <name> into ~/.local/share/themes."""
    import shutil
    s = settings if settings is not None else _settings()
    base = "Slope-Dark" if dark else "Slope"
    root = os.path.join(USER_THEMES, name)
    accent = accent_color(s)
    _write(os.path.join(root, "index.theme"),
           "[Desktop Entry]\nType=X-GNOME-Metatheme\nName=%s\nComment=Made by Slope (accent %s)\n\n"
           "[X-GNOME-Metatheme]\nGtkTheme=%s\nIconTheme=Slope\nCursorTheme=Slope-Cursors\n" % (name, accent, name))
    gtk3 = ['@import url("resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained%s.css");' % ("-dark" if dark else ""),
            '@import url("file://%s/%s/gtk-3.0/colors.css");' % (SYS_THEMES, base),
            '@import url("accent.css");',
            '@import url("file://%s/Slope/gtk-3.0/slope-common.css");' % SYS_THEMES]
    if dark:
        gtk3.append('@import url("file://%s/Slope-Dark/gtk-3.0/dark.css");' % SYS_THEMES)
    _write(os.path.join(root, "gtk-3.0", "gtk.css"), "/* Made by Slope */\n" + "\n".join(gtk3) + "\n")
    _write(os.path.join(root, "gtk-3.0", "accent.css"), "@define-color slope_accent %s;\n" % accent)
    _write(os.path.join(root, "gtk-4.0", "gtk.css"),
           '@import url("file://%s/%s/gtk-4.0/gtk.css");\n' % (SYS_THEMES, base))
    # window frame: the base theme's Openbox look with the chosen title buttons
    ob = os.path.join(root, "openbox-3")
    os.makedirs(ob, exist_ok=True)
    src = os.path.join(SYS_THEMES, base, "openbox-3")
    try:
        for f in os.listdir(src):
            if f.endswith(".xbm") or f == "themerc" or (f.endswith(".png") and
                                                         s.get("window_buttons", "colorful") == "colorful"):
                shutil.copyfile(os.path.join(src, f), os.path.join(ob, f))
    except OSError:
        pass
    style = s.get("window_buttons", "colorful")
    if style != "colorful":
        from . import art
        art.render_titlebuttons(ob, lights=art.lights_for(style, accent, button_colors(s)))
    # the other (older) copy is no longer needed
    other = name[:-1] + ("2" if name.endswith("1") else "1")
    shutil.rmtree(os.path.join(USER_THEMES, other), ignore_errors=True)


def _gnome_accent(hexcol):
    """libadwaita's nearest named accent."""
    import colorsys
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    if sat < 0.25:
        return "slate"
    names = [(0.0, "red"), (0.08, "orange"), (0.14, "yellow"), (0.33, "green"), (0.5, "teal"),
             (0.6, "blue"), (0.75, "purple"), (0.92, "pink"), (1.0, "red")]
    return min(names, key=lambda n: abs(n[0] - h))[1]


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def write_xsettings(dark, name=None):
    name = name or theme_name(dark)
    try:
        with open(XS_SYSTEM) as f:
            text = f.read()
    except OSError:
        text = ""
    text = re.sub(r'^Net/ThemeName .*$', 'Net/ThemeName "%s"' % name, text, flags=re.M)
    text = re.sub(r'^Gtk/ApplicationPreferDarkTheme .*\n?', '', text, flags=re.M)
    size = int(_settings().get("cursor_size", 24) or 24)
    text = re.sub(r'^Gtk/CursorThemeSize .*$', 'Gtk/CursorThemeSize %d' % size, text, flags=re.M)
    text = text.rstrip("\n") + "\nGtk/ApplicationPreferDarkTheme %d\n" % int(dark)
    _write(XS_USER, "# Made by Slope from %s (dark mode: %s)\n" % (XS_SYSTEM, "on" if dark else "off") + text)


def write_openbox(dark, name=None):
    name = name or theme_name(dark)
    try:
        with open(OB_SYSTEM) as f:
            text = f.read()
    except OSError:
        return False
    text = re.sub(r"(<theme>\s*<name>)[^<]*(</name>)", r"\g<1>%s\g<2>" % name, text, count=1)
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
    name = theme_name(dark)
    if customized():
        build_theme(name, dark)
    write_xsettings(dark, name)
    write_openbox(dark, name)
    _gnome(dark)


def _gnome(dark):
    _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
          "prefer-dark" if dark else "default"])
    _run(["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", theme_name(dark)])
    _run(["gsettings", "set", "org.gnome.desktop.interface", "accent-color", _gnome_accent(accent_color())])


def _terminal(dark):
    for prop, val in TERMINAL[dark].items():
        _run(["xfconf-query", "-c", "xfce4-terminal", "-p", "/" + prop, "-n", "-t", "string", "-s", val])


def apply(dark):
    """Switch the running session."""
    name = theme_name(dark, fresh=True)
    if customized():
        build_theme(name, dark)
    write_xsettings(dark, name)
    _run(["pkill", "-HUP", "-x", "-u", str(os.getuid()), "xsettingsd"])
    if write_openbox(dark, name):
        _run(["openbox", "--reconfigure"])
    _gnome(dark)
    _terminal(dark)


def set_dark(dark):
    """Save the choice and switch."""
    set_options(dark_mode=bool(dark))


def set_options(**opts):
    """Save dark_mode / accent / window_buttons and switch the session."""
    s = _settings()
    s.update(opts)
    _write(SETTINGS_PATH, json.dumps(s, indent=2))
    apply(bool(s.get("dark_mode", False)))
