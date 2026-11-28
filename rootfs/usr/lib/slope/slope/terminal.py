"""slope-terminal: Slope's own terminal (VTE, the engine of GNOME's terminal).

Tabs, menus in the top bar, ⌘C / ⌘V to copy and paste (Ctrl+C still
interrupts the running program), light and dark colour schemes that follow
dark mode, the cursor in the accent colour, ⌘+ / ⌘- / ⌘0 for the text size.

    slope-terminal [--working-directory DIR] [-e COMMAND ...]

Also installed as x-terminal-emulator, so other programs open it with -e.
"""
import os
import shlex
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402

APP_ID = "org.slope.Terminal"
DESKTOP = "/usr/share/applications/slope-terminal.desktop"

# foreground, background, 16-colour palette
SCHEMES = {
    False: ("#1d1d1f", "#fbfbfd",
            "#1d1d1f #c41a16 #007400 #826b28 #0b4bc2 #a90d91 #3e8087 #c7c7c7 "
            "#686868 #e0443c #28c840 #d79c19 #2f86f3 #c54fd8 #26a4b5 #ffffff"),
    True: ("#e6e6ea", "#1e1e21",
           "#2c2c30 #ff6b62 #5fd36c #e3b341 #5aa8ff #d282e8 #4fc4d2 #d0d0d6 "
           "#6a6a72 #ff8a82 #7ee58a #f2cc62 #82bfff #e3a3f2 #78dbe6 #ffffff"),
}

CSS = b"""
.slope-term notebook > header { padding: 0; }
.slope-term notebook > header tab { padding: 2px 10px; margin: 2px 1px; min-height: 0; }
.slope-term notebook > header tab button { padding: 0; min-height: 16px; min-width: 16px; border: none;
  background: none; box-shadow: none; }
"""


def rgba(text):
    c = Gdk.RGBA()
    c.parse(text)
    return c


def _accent():
    try:
        from .appearance import accent_color
        return accent_color()
    except Exception:
        return "#2f86f3"


class TerminalTab(Gtk.ScrolledWindow):
    def __init__(self, win, cwd=None, argv=None):
        super().__init__()
        self.win = win
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.term = Vte.Terminal()
        self.term.set_scrollback_lines(10000)
        self.term.set_mouse_autohide(True)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.set_allow_hyperlink(True)
        self.term.set_font(Pango.FontDescription(win.app.font))
        # a command given with -e stays on screen when it ends, like macOS
        # Terminal ("[Process completed]"); the login shell closes its tab
        self.command = bool(argv)
        self.term.connect("child-exited", self._exited)
        self.term.connect("window-title-changed", lambda *_: win.update_title(self))
        self.term.connect("button-press-event", self._press)
        self.add(self.term)
        self.colours()
        shell = os.environ.get("SHELL") or "/bin/bash"
        argv = argv or [shell]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, cwd or GLib.get_home_dir(), argv,
                              ["TERM=xterm-256color", "COLORTERM=truecolor", "TERM_PROGRAM=Slope"],
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, self._spawned, None)
        self.label = Gtk.Label(label="Terminal")
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_max_width_chars(40)
        self.label.set_width_chars(8)

    def _spawned(self, term, pid, error, *_):
        if error is not None:
            self.command = True
            self._note("Couldn't run the command: %s" % error.message)

    def _exited(self, term, status):
        if self.command:
            self._note("[Process completed]")
        else:
            self.win.close_tab(self)

    def _note(self, text):
        self.term.feed(("\r\n\x1b[2m%s\x1b[0m\r\n" % text).encode())

    def colours(self):
        from .common import dark_theme
        fg, bg, pal = SCHEMES[dark_theme()]
        self.term.set_colors(rgba(fg), rgba(bg), [rgba(c) for c in pal.split()])
        self.term.set_color_cursor(rgba(_accent()))
        self.term.set_color_cursor_foreground(rgba("#ffffff"))
        sel = rgba(_accent())
        sel.alpha = 0.35
        self.term.set_color_highlight(sel)
        self.term.set_color_highlight_foreground(None)

    def title(self):
        t = self.term.get_window_title() or ""
        return t or "Terminal"

    def cwd(self):
        uri = self.term.get_current_directory_uri()
        if uri:
            return Gio.File.new_for_uri(uri).get_path()
        return None

    def _press(self, w, ev):
        if ev.button != 3:
            return False
        menu = Gtk.Menu()
        for label, action, ok in (("Copy", "win.copy", self.term.get_has_selection()),
                                  ("Paste", "win.paste", True), ("Select All", "win.select-all", True)):
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(ok)
            mi.set_action_name(action)
            menu.append(mi)
        menu.show_all()
        menu.attach_to_widget(self.term, None)
        menu.popup_at_pointer(ev)
        return True


class TerminalWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cwd=None, argv=None):
        super().__init__(application=app, title="Terminal")
        self.app = app
        self.set_default_size(760, 480)
        self.set_icon_name("utilities-terminal")
        self.get_style_context().add_class("slope-term")
        self.book = Gtk.Notebook()
        self.book.set_scrollable(True)
        self.book.set_show_border(False)
        self.book.connect("switch-page", lambda b, page, n: self.update_title(page))
        self.add(self.book)
        for name, cb in (("new-tab", lambda *_: self.new_tab(cwd=self.current().cwd() if self.current() else None)),
                         ("close-tab", lambda *_: self.current() and self.close_tab(self.current(), kill=True)),
                         ("copy", lambda *_: self.current() and self.current().term.copy_clipboard_format(
                             Vte.Format.TEXT)),
                         ("paste", lambda *_: self.current() and self.current().term.paste_clipboard()),
                         ("select-all", lambda *_: self.current() and self.current().term.select_all()),
                         ("clear", lambda *_: self.current() and self.current().term.reset(True, True)),
                         ("bigger", lambda *_: self.zoom(1.1)), ("smaller", lambda *_: self.zoom(1 / 1.1)),
                         ("actual-size", lambda *_: self.zoom(None)),
                         ("next-tab", lambda *_: self.book.next_page()),
                         ("previous-tab", lambda *_: self.book.prev_page())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self.new_tab(cwd, argv)
        self.show_all()

    def current(self):
        n = self.book.get_current_page()
        return self.book.get_nth_page(n) if n >= 0 else None

    def new_tab(self, cwd=None, argv=None):
        tab = TerminalTab(self, cwd, argv)
        head = Gtk.Box(spacing=4)
        head.pack_start(tab.label, True, True, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.close_tab(tab, kill=True))
        head.pack_start(close, False, False, 0)
        head.show_all()
        n = self.book.append_page(tab, head)
        self.book.set_tab_reorderable(tab, True)
        self.book.child_set_property(tab, "tab-expand", True)   # tabs share the width, like Safari
        self.book.set_show_tabs(self.book.get_n_pages() > 1)
        tab.show_all()
        self.book.set_current_page(n)
        tab.term.grab_focus()
        return tab

    def close_tab(self, tab, kill=False):
        n = self.book.page_num(tab)
        if n < 0:
            return
        self.book.remove_page(n)
        self.book.set_show_tabs(self.book.get_n_pages() > 1)
        if self.book.get_n_pages() == 0:
            self.destroy()
        elif self.current():
            self.current().term.grab_focus()

    def update_title(self, tab):
        if tab is None:
            return
        t = tab.title()
        tab.label.set_text(t)
        if tab is self.current():
            self.set_title(t)

    def zoom(self, factor):
        for i in range(self.book.get_n_pages()):
            term = self.book.get_nth_page(i).term
            term.set_font_scale(1.0 if factor is None else max(0.5, min(4.0, term.get_font_scale() * factor)))

    def recolour(self):
        for i in range(self.book.get_n_pages()):
            self.book.get_nth_page(i).colours()


class TerminalApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.font = "DejaVu Sans Mono 11"

    def do_startup(self):
        Gtk.Application.do_startup(self)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            from .common import load_settings, on_theme_change
            self.font = load_settings().get("terminal_font", self.font)
            # dark mode / accent switched: new colours for every terminal
            on_theme_change(lambda: [w.recolour() for w in self.get_windows() if isinstance(w, TerminalWindow)])
        except Exception:
            pass
        for name, cb in (("new-window", lambda *_: self.new_window()), ("quit", lambda *_: self._quit())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self._menus()
        # a Mac app: stays in the Dock when its last window is closed
        self.hold()
        try:
            from . import keepalive
            keepalive.register(APP_ID, DESKTOP)

            def poll():
                if keepalive.quit_requested() and not self.get_windows():
                    keepalive.unregister()
                    self.release()
                    return False
                return True
            GLib.timeout_add(500, poll)
        except Exception:
            pass

    def _quit(self):
        try:
            from . import keepalive
            keepalive.unregister()
        except Exception:
            pass
        self.quit()

    def _menus(self):
        def section(*items):
            m = Gio.Menu()
            for label, action in items:
                m.append(label, action)
            return m

        def menu(*sections):
            m = Gio.Menu()
            for s in sections:
                m.append_section(None, s)
            return m
        bar = Gio.Menu()
        bar.append_submenu("Shell", menu(section(("New Window", "app.new-window"), ("New Tab", "win.new-tab")),
                                         section(("Close Tab", "win.close-tab"))))
        bar.append_submenu("Edit", menu(section(("Copy", "win.copy"), ("Paste", "win.paste"),
                                                ("Select All", "win.select-all")),
                                        section(("Clear Scrollback", "win.clear"))))
        bar.append_submenu("View", menu(section(("Bigger", "win.bigger"), ("Smaller", "win.smaller"),
                                                ("Actual Size", "win.actual-size"))))
        bar.append_submenu("Window", menu(section(("Show Previous Tab", "win.previous-tab"),
                                                  ("Show Next Tab", "win.next-tab"))))
        self.set_menubar(bar)
        for action, keys in (("app.new-window", ["<Primary><Shift>n"]),
                             ("win.new-tab", ["<Primary><Shift>t"]),
                             ("win.close-tab", ["<Primary><Shift>w"]),
                             ("win.copy", ["<Super>c", "<Primary><Shift>c"]),
                             ("win.paste", ["<Super>v", "<Primary><Shift>v"]),
                             ("win.select-all", ["<Super>a", "<Primary><Shift>a"]),
                             ("win.clear", ["<Super>k", "<Primary><Shift>k"]),
                             ("win.bigger", ["<Super>plus", "<Super>equal", "<Primary>plus", "<Primary>equal"]),
                             ("win.smaller", ["<Super>minus", "<Primary>minus"]),
                             ("win.actual-size", ["<Super>0", "<Primary>0"]),
                             ("win.next-tab", ["<Primary>Page_Down", "<Primary>Tab"]),
                             ("win.previous-tab", ["<Primary>Page_Up", "<Primary><Shift>Tab"]),
                             ("app.quit", ["<Primary><Shift>q"])):
            self.set_accels_for_action(action, keys)

    def new_window(self, cwd=None, argv=None):
        w = TerminalWindow(self, cwd, argv)
        w.present()
        return w

    def do_command_line(self, cl):
        args = cl.get_arguments()[1:]
        cwd, argv = cl.get_cwd(), None
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("--working-directory", "--workdir") and i + 1 < len(args):
                cwd = args[i + 1]
                i += 2
                continue
            if a.startswith("--working-directory="):
                cwd = a.split("=", 1)[1]
            elif a in ("-e", "-x", "--command", "--"):
                rest = args[i + 1:]
                # "-e 'ls -l'" (one string) or "-e ls -l" (separate words)
                argv = shlex.split(rest[0]) if len(rest) == 1 else rest
                break
            i += 1
        self.new_window(cwd, argv)
        return 0


def main(argv=None):
    GLib.set_prgname("slope-terminal")
    GLib.set_application_name("Terminal")
    app = TerminalApp()
    return app.run(sys.argv if argv is None else argv)
