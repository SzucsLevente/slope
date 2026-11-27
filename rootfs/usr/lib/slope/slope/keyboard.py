"""Keyboard layouts and key options (System Settings > Keyboard).

Settings live in ~/.config/slope/settings.json:
  kb_layouts      [["hu", ""], ["us", "mac"]]  layout + variant, first one is the default;
                  empty: the computer's layout (/etc/default/keyboard, set by the installer)
  kb_switch       key combination that switches layouts (xkb "grp:" option)
  kb_compose      Compose key: "" (none), "rctrl", "menu", "caps", "rwin"
  kb_option_altgr both Option (Alt) keys type special characters, like on a Mac
  kb_swap_alt_win use the key next to the space bar as Command on a PC keyboard
  kb_swap_iso     swap the key above Tab with the key left of Z (Apple ISO keyboards
                  whose two keys come out the wrong way round)

apply() runs at login (slope-ctl keyboard), when a keyboard is plugged in and
whenever something changes in System Settings.  Light module (no GTK).
"""
import json
import os
import re
import subprocess

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slope")
SETTINGS_PATH = os.path.join(CONFIG, "settings.json")
RULES = "/usr/share/X11/xkb/rules/evdev.lst"

DEFAULTS = {
    "kb_layouts": [],
    "kb_switch": "grp:ctrl_space_toggle",
    "kb_compose": "",
    "kb_option_altgr": False,
    "kb_swap_alt_win": False,
    "kb_swap_iso": False,
}

SWITCH_CHOICES = [
    ("grp:ctrl_space_toggle", "⌃ Control + Space"),
    ("grp:alt_shift_toggle", "⌥ Option (Alt) + Shift"),
    ("grp:ctrl_shift_toggle", "⌃ Control + Shift"),
    ("grp:ctrls_toggle", "Both Control keys"),
]
COMPOSE_CHOICES = [
    ("", "None"),
    ("rctrl", "Right Control"),
    ("caps", "Caps Lock"),
    ("menu", "Menu key"),
    ("rwin", "Right ⌘ Command"),
]


def load():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH) as f:
            stored = json.load(f)
        for k in DEFAULTS:
            if k in stored:
                data[k] = stored[k]
    except (OSError, ValueError):
        pass
    return data


def system_keyboard():
    """The computer's keyboard settings (/etc/default/keyboard)."""
    out = {"XKBMODEL": "pc105", "XKBLAYOUT": "us", "XKBVARIANT": "", "XKBOPTIONS": ""}
    try:
        with open("/etc/default/keyboard") as f:
            for line in f:
                m = re.match(r'\s*(XKB\w+)\s*=\s*"?([^"\n]*)"?', line)
                if m:
                    out[m.group(1)] = m.group(2).strip()
    except OSError:
        pass
    return out


def layouts(settings=None):
    """[[layout, variant], ...] in use (the first is the default)."""
    s = settings or load()
    lay = [list(x)[:2] for x in s.get("kb_layouts") or [] if x and x[0]]
    if lay:
        return lay
    sysk = system_keyboard()
    ls = [x.strip() for x in sysk["XKBLAYOUT"].split(",")]
    vs = [x.strip() for x in sysk["XKBVARIANT"].split(",")]
    return [[l, vs[i] if i < len(vs) else ""] for i, l in enumerate(ls) if l] or [["us", ""]]


def options(settings=None):
    s = settings or load()
    opts = []
    if len(layouts(s)) > 1 and s.get("kb_switch"):
        opts.append(s["kb_switch"])
    if s.get("kb_compose"):
        opts.append("compose:" + s["kb_compose"])
    if s.get("kb_option_altgr"):
        opts.append("lv3:alt_switch")      # both Alt / Option keys choose the 3rd level
    # (otherwise the layout decides: Hungarian, German... make the right
    # Alt / Option key AltGr.  It used to be turned into Compose at login,
    # which made @ { } [ ] \ | unreachable on those layouts.)
    if s.get("kb_swap_alt_win"):
        opts.append("altwin:swap_alt_win")
    return opts


def setxkbmap_args(settings=None):
    s = settings or load()
    lay = layouts(s)
    model = system_keyboard()["XKBMODEL"] or "pc105"
    return ["setxkbmap", "-model", model,
            "-layout", ",".join(l for l, _v in lay),
            "-variant", ",".join(v for _l, v in lay),
            "-option", ""] + [a for o in options(s) for a in ("-option", o)]


def apply(settings=None):
    s = settings or load()
    args = setxkbmap_args(s)
    try:
        if not s.get("kb_swap_iso"):
            subprocess.run(args, capture_output=True, timeout=10)
            return
        # Apple ISO keyboards: swap the key above Tab (TLDE, code 49) with the
        # key left of Z (LSGT, code 94) by compiling a keymap with the two
        # key codes exchanged.
        keymap = subprocess.run(args + ["-print"], capture_output=True, text=True, timeout=10).stdout
        keymap = re.sub(r'(xkb_keycodes\s*\{\s*include\s*"[^"]*")\s*\};',
                        r'\1\n    override <TLDE> = 94;\n    override <LSGT> = 49;\n};', keymap, count=1)
        subprocess.run(["xkbcomp", "-w", "0", "-", os.environ.get("DISPLAY", ":0")],
                       input=keymap, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def save_for_login_screen(settings=None):
    """Use the layouts on the login screen and the text console too (asks for a password)."""
    s = settings or load()
    lay = layouts(s)
    model = system_keyboard()["XKBMODEL"] or "pc105"
    opts = [o for o in options(s)]
    return subprocess.Popen(["localectl", "set-x11-keymap", ",".join(l for l, _v in lay), model,
                             ",".join(v for _l, v in lay), ",".join(opts)])


_catalog = None


def catalog():
    """{layout: (description, [(variant, description), ...])} from the xkb rules."""
    global _catalog
    if _catalog is not None:
        return _catalog
    cat = {}
    section = None
    try:
        with open(RULES, encoding="utf-8") as f:
            for line in f:
                if line.startswith("!"):
                    section = line[1:].strip()
                    continue
                m = re.match(r"\s+(\S+)\s+(.*)$", line)
                if not m:
                    continue
                if section == "layout":
                    cat[m.group(1)] = (m.group(2).strip(), [])
                elif section == "variant":
                    lm = re.match(r"(\S+):\s*(.*)$", m.group(2))
                    if lm and lm.group(1) in cat:
                        cat[lm.group(1)][1].append((m.group(1), lm.group(2).strip()))
    except OSError:
        pass
    _catalog = cat
    return cat


def describe(layout, variant=""):
    desc, variants = catalog().get(layout, (layout, []))
    if variant:
        for v, d in variants:
            if v == variant:
                return d
        return "%s (%s)" % (desc, variant)
    return desc
