"""Slope Files: a Mac OS X style file manager.

Sidebar (Favorites, Devices, Colors), Icons / List / Columns views, Back /
Forward, a path bar, search, Quick Look (Space), Get Info, drag and drop,
copy / cut / paste (shared with other file managers), folder colors, the
Trash with Put Back.  Menus live in the top bar (File, Edit, View, Go).
"""
import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import files  # noqa: E402

APP_ID = "org.slope.Files"
URI_TARGETS = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
TRASH = "trash:///"
(C_FILE, C_INFO, C_PIX, C_NAME, C_DATE, C_SIZE, C_KIND, C_DIR, C_SMALL, C_SORT) = range(10)

DARK_CSS = b"""
.finder-toolbar { border-bottom-color: #19191b; background-image: linear-gradient(to bottom, #3e3e43, #2f2f33); }
.finder-sidebar, .finder-sidebar list { background-color: #27292e; }
.finder-sidebar row label { color: #e4e6ea; }
.finder-sidebar row.section label { color: #8e97a6; }
.finder-sidebar row image { color: mix(@slope_accent, #ffffff, 0.30); }
.finder-sidebar row:selected label, .finder-sidebar row:selected image { color: #ffffff; }
.finder-bottom { border-top-color: #19191b; background-image: linear-gradient(to bottom, #36363a, #2b2b2f); }
.finder-bottom label { color: #b8bcc4; }
iconview.finder-icons { background-color: #1f1f22; color: #ececf0; }
.finder-column { border-right-color: #3a3a3e; }
.finder-preview { background-color: #29292c; }
"""

CSS = b"""
.finder-toolbar { padding: 6px 10px; border-bottom: 1px solid #a7a7a7;
  background-image: linear-gradient(to bottom, #ececec, #d3d3d3); }
.finder-sidebar, .finder-sidebar list { background-color: #e3e8ef; }
.finder-sidebar row { padding: 3px 12px 3px 16px; min-height: 22px; }
.finder-sidebar row.section { padding: 12px 8px 2px 10px; }
.finder-sidebar row.section label { color: #6f7b8b; font-weight: bold; font-size: 8.5pt; }
.finder-sidebar row:selected { background-image: linear-gradient(to bottom, mix(@slope_accent, #ffffff, 0.34), mix(@slope_accent, #000000, 0.06)); }
.finder-sidebar row:selected label { color: #ffffff; }
.finder-sidebar row image { color: mix(@slope_accent, #000000, 0.15); }
.finder-sidebar row:selected image { color: #ffffff; }
.finder-bottom { padding: 2px 8px; border-top: 1px solid #b4b4b4;
  background-image: linear-gradient(to bottom, #eeeeee, #d9d9d9); }
.finder-bottom label { font-size: 9pt; color: #4b4b4b; }
.finder-path button { padding: 0 4px; min-height: 0; border: none; background: none; box-shadow: none; }
.finder-path button label { font-size: 9pt; }
iconview.finder-icons { background-color: #ffffff; }
iconview.finder-icons:selected { background-color: mix(@slope_accent, #ffffff, 0.07); color: #ffffff; border-radius: 6px; }
.finder-column { border-right: 1px solid #d7d7d7; }
.finder-preview { background-color: #f6f6f6; }
"""


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else "%.1f %s" % (n, unit)
        n /= 1000.0
    return ""


def human_date(t):
    if not t:
        return "--"
    lt = time.localtime(t)
    now = time.localtime()
    if lt.tm_year == now.tm_year and lt.tm_yday == now.tm_yday:
        return time.strftime("Today, %H:%M", lt)
    if lt.tm_year == now.tm_year and lt.tm_yday == now.tm_yday - 1:
        return time.strftime("Yesterday, %H:%M", lt)
    return time.strftime("%d %b %Y, %H:%M", lt)


def display_name(gfile):
    uri = gfile.get_uri()
    if uri.startswith(TRASH):
        return "Trash"
    path = gfile.get_path()
    if path == "/":
        return "Computer"
    sp = files.special_dirs().get(path)
    if sp:
        return sp[0]
    try:
        return gfile.query_info("standard::display-name", Gio.FileQueryInfoFlags.NONE,
                                None).get_display_name()
    except GLib.Error:
        return gfile.get_basename() or uri


class FilesWindow(Gtk.ApplicationWindow):
    def __init__(self, app, location):
        super().__init__(application=app, title="Files")
        self.app = app
        self.set_wmclass("slope-files", "Slope-files")
        self.set_icon_name("slope-files")
        self.set_default_size(1000, 640)
        self.cwd = None
        self.back_stack, self.fwd_stack = [], []
        self.mode = app.settings.get("view", "icons")
        self.icon_size = app.settings.get("icon_size", 64)
        self.show_hidden = False
        self.search_text = ""
        self._mon = None
        self._load_id = 0
        self._search_cancel = None
        self._columns = []

        self.store = Gtk.ListStore(GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT, GdkPixbuf.Pixbuf, str,
                                   str, str, str, bool, GdkPixbuf.Pixbuf, str)
        self.store.set_sort_column_id(C_SORT, Gtk.SortType.ASCENDING)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        outer.pack_start(self._toolbar(), False, False, 0)
        paned = Gtk.Paned()
        paned.set_position(190)
        outer.pack_start(paned, True, True, 0)
        paned.pack1(self._sidebar(), False, False)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        paned.pack2(right, True, False)
        self.views = Gtk.Stack()
        right.pack_start(self.views, True, True, 0)
        self.views.add_named(self._icon_view(), "icons")
        self.views.add_named(self._list_view(), "list")
        self.views.add_named(self._column_view(), "columns")
        right.pack_start(self._bottom_bar(), False, False, 0)
        self._actions()
        self.connect("key-press-event", self._key)
        self.show_all()
        self.set_mode(self.mode, reload=False)
        self.go(location, record=False)

    # -- building ---------------------------------------------------------------------------
    def _toolbar(self):
        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class("finder-toolbar")
        nav = Gtk.Box()
        nav.get_style_context().add_class("linked")
        self.back_btn = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.back_btn.set_tooltip_text("Back")
        self.back_btn.connect("clicked", lambda *_: self.back())
        self.fwd_btn = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.fwd_btn.set_tooltip_text("Forward")
        self.fwd_btn.connect("clicked", lambda *_: self.forward())
        nav.pack_start(self.back_btn, False, False, 0)
        nav.pack_start(self.fwd_btn, False, False, 0)
        bar.pack_start(nav, False, False, 0)
        seg = Gtk.Box()
        seg.get_style_context().add_class("linked")
        self.mode_btns = {}
        group = None
        for mode, icon, tip in (("icons", "view-grid-symbolic", "as Icons"),
                                ("list", "view-list-symbolic", "as List"),
                                ("columns", "view-column-symbolic", "as Columns")):
            b = Gtk.RadioButton.new_from_widget(group)
            group = group or b
            b.set_mode(False)
            b.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
            b.set_tooltip_text(tip)
            b.connect("toggled", lambda w, m=mode: w.get_active() and self.set_mode(m))
            seg.pack_start(b, False, False, 0)
            self.mode_btns[mode] = b
        bar.pack_start(seg, False, False, 0)
        self.title_lbl = Gtk.Label()        # (the window title shows the folder name)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search")
        self.search.set_width_chars(20)
        self.search.connect("search-changed", self._search_changed)
        bar.pack_end(self.search, False, False, 0)
        self.trash_btn = Gtk.Button(label="Empty")
        self.trash_btn.set_tooltip_text("Empty Trash")
        self.trash_btn.connect("clicked", lambda *_: self.empty_trash())
        self.trash_btn.set_no_show_all(True)
        bar.pack_end(self.trash_btn, False, False, 0)
        return bar

    def _sidebar(self):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.get_style_context().add_class("finder-sidebar")
        self.places = Gtk.ListBox()
        self.places.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.places.connect("row-activated", self._place_activated)
        self.places.drag_dest_set(Gtk.DestDefaults.ALL, URI_TARGETS, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self.places.connect("drag-data-received", self._drop_on_place)
        sw.add(self.places)
        self.volumes = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed"):
            self.volumes.connect(sig, lambda *_: self._fill_places())
        self._fill_places()
        return sw

    def _section(self, text):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        row.get_style_context().add_class("section")
        lab = Gtk.Label(label=text.upper(), xalign=0)
        row.add(lab)
        row.target = None
        return row

    def _place(self, label, icon, target, eject=None, pixbuf=None):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=8)
        img = Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf else Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        box.pack_start(img, False, False, 0)
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(lab, True, True, 0)
        if eject is not None:
            b = Gtk.Button.new_from_icon_name("media-eject-symbolic", Gtk.IconSize.MENU)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text("Eject")
            b.connect("clicked", lambda *_: eject())
            box.pack_end(b, False, False, 0)
        row.add(box)
        row.target = target
        return row

    def _fill_places(self):
        for c in self.places.get_children():
            c.destroy()
        add = self.places.add
        add(self._section("Favorites"))
        sp = files.special_dirs()
        home = GLib.get_home_dir()
        order = [home] + [GLib.get_user_special_dir(k) for k in (
            GLib.UserDirectory.DIRECTORY_DESKTOP, GLib.UserDirectory.DIRECTORY_DOCUMENTS,
            GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_PICTURES,
            GLib.UserDirectory.DIRECTORY_MUSIC, GLib.UserDirectory.DIRECTORY_VIDEOS)]
        for p in order:
            if p and p in sp and os.path.isdir(p):
                add(self._place(sp[p][0], sp[p][1], Gio.File.new_for_path(p)))
        add(self._place("Applications", "view-app-grid-symbolic", "launchpad"))
        add(self._place("Trash", "user-trash-symbolic", Gio.File.new_for_uri(TRASH)))
        add(self._section("Devices"))
        add(self._place("Computer", "computer-symbolic", Gio.File.new_for_path("/")))
        for m in self.volumes.get_mounts():
            if m.is_shadowed():
                continue
            eject = None
            if m.can_eject():
                eject = (lambda m=m: m.eject_with_operation(Gio.MountUnmountFlags.NONE, None, None, None, None))
            elif m.can_unmount():
                eject = (lambda m=m: m.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, None, None))
            gi_ = m.get_symbolic_icon()
            add(self._place(m.get_name(), gi_.to_string() if gi_ else "drive-harddisk-symbolic",
                            m.get_root(), eject))
        add(self._section("Colors"))
        for key, name in files.COLOR_NAMES:
            if key == "blue":
                continue
            add(self._place(name, None, "color:" + key, pixbuf=files.color_swatch(key, 12)))
        self.places.show_all()
        self._select_place()

    def _select_place(self):
        if self.cwd is None:
            return
        for row in self.places.get_children():
            t = getattr(row, "target", None)
            if isinstance(t, Gio.File) and t.equal(self.cwd):
                self.places.select_row(row)
                return
        self.places.unselect_all()

    def _icon_view(self):
        sw = Gtk.ScrolledWindow()
        iv = Gtk.IconView.new_with_model(self.store)
        iv.get_style_context().add_class("finder-icons")
        iv.set_pixbuf_column(C_PIX)
        iv.set_text_column(C_NAME)
        iv.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        iv.set_item_padding(6)
        iv.set_item_width(self.icon_size + 36)
        iv.set_row_spacing(4)
        iv.set_column_spacing(4)
        iv.connect("item-activated", lambda v, path: self.open_rows([path]))
        iv.connect("button-press-event", self._view_press)
        iv.connect("selection-changed", lambda *_: self._update_status())
        iv.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, URI_TARGETS,
                                    Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        iv.enable_model_drag_dest(URI_TARGETS, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        iv.connect("drag-data-get", self._drag_get)
        iv.connect("drag-data-received", self._drag_received)
        for cell in iv.get_cells():
            if isinstance(cell, Gtk.CellRendererText):
                cell.set_property("wrap-mode", Pango.WrapMode.WORD_CHAR)
                cell.set_property("alignment", Pango.Alignment.CENTER)
        self.iconview = iv
        sw.add(iv)
        return sw

    def _list_view(self):
        sw = Gtk.ScrolledWindow()
        tv = Gtk.TreeView(model=self.store)
        tv.set_rubber_banding(True)
        tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        tv.set_enable_search(False)
        col = Gtk.TreeViewColumn("Name")
        pr = Gtk.CellRendererPixbuf()
        col.pack_start(pr, False)
        col.add_attribute(pr, "pixbuf", C_SMALL)
        tr = Gtk.CellRendererText()
        tr.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(tr, True)
        col.add_attribute(tr, "text", C_NAME)
        col.set_expand(True)
        col.set_resizable(True)
        col.set_sort_column_id(C_SORT)
        tv.append_column(col)
        for title, cid in (("Date Modified", C_DATE), ("Size", C_SIZE), ("Kind", C_KIND)):
            r = Gtk.CellRendererText()
            r.set_property("foreground", "#555555")
            c = Gtk.TreeViewColumn(title, r, text=cid)
            c.set_resizable(True)
            c.set_min_width(110 if cid != C_KIND else 150)
            tv.append_column(c)
        tv.connect("row-activated", lambda v, path, c: self.open_rows([path]))
        tv.connect("button-press-event", self._view_press)
        tv.get_selection().connect("changed", lambda *_: self._update_status())
        tv.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, URI_TARGETS,
                                    Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        tv.enable_model_drag_dest(URI_TARGETS, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        tv.connect("drag-data-get", self._drag_get)
        tv.connect("drag-data-received", self._drag_received)
        self.listview = tv
        sw.add(tv)
        return sw

    def _column_view(self):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.colbox = Gtk.Box()
        sw.add(self.colbox)
        self.colscroll = sw
        return sw

    def _bottom_bar(self):
        bar = Gtk.Box(spacing=8)
        bar.get_style_context().add_class("finder-bottom")
        self.path_box = Gtk.Box()
        self.path_box.get_style_context().add_class("finder-path")
        bar.pack_start(self.path_box, True, True, 0)
        self.status = Gtk.Label()
        bar.pack_start(self.status, False, False, 0)
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 32, 128, 8)
        self.zoom.set_draw_value(False)
        self.zoom.set_size_request(110, -1)
        self.zoom.set_value(self.icon_size)
        self.zoom.connect("value-changed", self._zoom_changed)
        bar.pack_end(self.zoom, False, False, 0)
        return bar

    # -- actions and menus -----------------------------------------------------------------------
    def _actions(self):
        def add(name, cb, param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", lambda act, p: cb(p) if param else cb())
            self.add_action(a)
        add("new-folder", self.new_folder)
        add("open", lambda: self.open_rows(self.selected_paths()))
        add("get-info", self.get_info)
        add("rename", self.rename_selected)
        add("trash", self.trash_selected)
        add("empty-trash", self.empty_trash)
        add("copy", lambda: self.clipboard(False))
        add("cut", lambda: self.clipboard(True))
        add("paste", self.paste)
        add("duplicate", self.duplicate)
        add("select-all", self.select_all)
        add("hidden", self.toggle_hidden)
        add("view", lambda p: self.set_mode(p.get_string()), GLib.VariantType.new("s"))
        add("back", self.back)
        add("forward", self.forward)
        add("up", self.up)
        add("go", lambda p: self.go_named(p.get_string()), GLib.VariantType.new("s"))
        add("goto", self.goto_dialog)
        add("terminal", self.open_terminal)
        add("quicklook", self.quick_look)
        add("close", self.close)
        add("search", lambda: self.search.grab_focus())

    # -- navigation --------------------------------------------------------------------------------
    def go(self, gfile, record=True):
        if isinstance(gfile, str):
            gfile = Gio.File.new_for_commandline_arg(gfile)
        if record and self.cwd is not None and not self.cwd.equal(gfile):
            self.back_stack.append(self.cwd)
            self.fwd_stack.clear()
        self.cwd = gfile
        self.search_text = ""
        if self.search.get_text():
            self.search.set_text("")
        name = display_name(gfile)
        self.set_title(name)
        self.title_lbl.set_markup("<b>%s</b>" % GLib.markup_escape_text(name))
        self.back_btn.set_sensitive(bool(self.back_stack))
        self.fwd_btn.set_sensitive(bool(self.fwd_stack))
        self.trash_btn.set_visible(gfile.get_uri().startswith(TRASH))
        self._select_place()
        self._fill_path()
        self.load()
        if self.mode == "columns":
            self._reset_columns()

    def back(self):
        if self.back_stack:
            self.fwd_stack.append(self.cwd)
            self.go(self.back_stack.pop(), record=False)

    def forward(self):
        if self.fwd_stack:
            self.back_stack.append(self.cwd)
            self.go(self.fwd_stack.pop(), record=False)

    def up(self):
        p = self.cwd.get_parent() if self.cwd else None
        if p is not None:
            self.go(p)

    def go_named(self, name):
        home = GLib.get_home_dir()
        kinds = {"desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
                 "documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
                 "downloads": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
                 "pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
                 "music": GLib.UserDirectory.DIRECTORY_MUSIC}
        if name == "home":
            self.go(Gio.File.new_for_path(home))
        elif name == "computer":
            self.go(Gio.File.new_for_path("/"))
        elif name == "trash":
            self.go(Gio.File.new_for_uri(TRASH))
        elif name in kinds:
            self.go(Gio.File.new_for_path(GLib.get_user_special_dir(kinds[name]) or home))

    def _place_activated(self, lb, row):
        t = getattr(row, "target", None)
        if t is None:
            return
        if t == "launchpad":
            from .common import spawn
            spawn(["slope-launchpad"])
            self._select_place()
        elif isinstance(t, str) and t.startswith("color:"):
            self.show_color(t[6:])
        else:
            self.go(t)

    def _fill_path(self):
        for c in self.path_box.get_children():
            c.destroy()
        chain = []
        f = self.cwd
        while f is not None and len(chain) < 12:
            chain.append(f)
            if f.get_path() == GLib.get_home_dir():
                break
            f = f.get_parent()
        for i, f in enumerate(reversed(chain)):
            if i:
                self.path_box.pack_start(Gtk.Label(label="›"), False, False, 2)
            b = Gtk.Button()
            box = Gtk.Box(spacing=4)
            box.pack_start(Gtk.Image.new_from_pixbuf(files.folder_pixbuf(16, "blue")), False, False, 0)
            box.pack_start(Gtk.Label(label=display_name(f)), False, False, 0)
            b.add(box)
            b.connect("clicked", lambda w, f=f: self.go(f))
            self.path_box.pack_start(b, False, False, 0)
        self.path_box.show_all()

    # -- loading -----------------------------------------------------------------------------------
    def load(self):
        self._load_id += 1
        token = self._load_id
        folder = self.cwd
        hidden = self.show_hidden
        size, small = self.icon_size, 20
        keep = {r[C_FILE].get_uri() for r in self._selected_rows()} if self.cwd else set()
        if self._mon is not None:
            self._mon.cancel()
        try:
            self._mon = folder.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._mon.connect("changed", lambda *_: self._queue_reload())
        except GLib.Error:
            self._mon = None

        def work():
            rows, err = [], None
            try:
                attrs = files.ATTRS + ",trash::orig-path,trash::deletion-date"
                for info in folder.enumerate_children(attrs, Gio.FileQueryInfoFlags.NONE, None):
                    if not hidden and (info.get_is_hidden() or info.get_is_backup()):
                        continue
                    rows.append((folder.get_child(info.get_name()), info))
            except GLib.Error as e:
                err = e.message
            GLib.idle_add(self._loaded, token, rows, err, size, small, keep)
        threading.Thread(target=work, daemon=True).start()

    def _row(self, gfile, info, size, small):
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        name = info.get_display_name()
        mtime = info.get_attribute_uint64("time::modified")
        ctype = info.get_content_type() or ""
        kind = "Folder" if is_dir else (Gio.content_type_get_description(ctype) if ctype else "Document")
        sort = ("0" if is_dir else "1") + name.lower()
        return [gfile, info, files.file_pixbuf(gfile, info, size), name, human_date(mtime),
                "--" if is_dir else human_size(info.get_size()), kind, is_dir,
                files.file_pixbuf(gfile, info, small), sort]

    def _loaded(self, token, rows, err, size, small, keep):
        if token != self._load_id:
            return False
        self.store.clear()
        for gfile, info in rows:
            self.store.append(self._row(gfile, info, size, small))
        if err:
            self.status.set_text(err)
        else:
            self._update_status()
        if keep:
            for i, r in enumerate(self.store):
                if r[C_FILE].get_uri() in keep:
                    self._select_path(Gtk.TreePath(i), clear=False)
        return False

    def _queue_reload(self):
        if getattr(self, "_reload_id", None):
            return
        self._reload_id = GLib.timeout_add(300, self._do_reload)

    def _do_reload(self):
        self._reload_id = None
        if not self.search_text:
            self.load()
        return False

    def _update_status(self):
        n = len(self.store)
        sel = len(self.selected_paths())
        text = "%d item%s" % (n, "" if n == 1 else "s")
        if sel:
            text = "%d of %s selected" % (sel, text)
        try:
            fsinfo = self.cwd.query_filesystem_info("filesystem::free", None)
            free = fsinfo.get_attribute_uint64("filesystem::free")
            if free:
                text += ", %s available" % human_size(free)
        except GLib.Error:
            pass
        self.status.set_text(text)

    # -- views ---------------------------------------------------------------------------------------
    def set_mode(self, mode, reload=True):
        if mode not in ("icons", "list", "columns"):
            return
        self.mode = mode
        self.app.settings["view"] = mode
        self.app.save_settings()
        if not self.mode_btns[mode].get_active():
            self.mode_btns[mode].set_active(True)
        self.views.set_visible_child_name(mode)
        self.zoom.set_visible(mode == "icons")
        if mode == "columns" and self.cwd is not None:
            self._reset_columns()

    def _zoom_changed(self, scale):
        size = int(scale.get_value())
        if size == self.icon_size:
            return
        self.icon_size = size
        self.app.settings["icon_size"] = size
        self.app.save_settings()
        self.iconview.set_item_width(size + 36)
        for r in self.store:
            r[C_PIX] = files.file_pixbuf(r[C_FILE], r[C_INFO], size)

    def active_view(self):
        return self.iconview if self.mode == "icons" else self.listview

    def selected_paths(self):
        if self.mode == "icons":
            return self.iconview.get_selected_items()
        if self.mode == "list":
            return self.listview.get_selection().get_selected_rows()[1]
        col = self._focused_column()
        if col is not None:
            return [("col", col, p) for p in col[2].get_selection().get_selected_rows()[1]]
        return []

    def _selected_rows(self):
        out = []
        for p in self.selected_paths():
            if isinstance(p, tuple):
                _tag, col, path = p
                out.append(col[1][path])
            else:
                out.append(self.store[p])
        return out

    def selected_files(self):
        return [(r[C_FILE], r[C_INFO]) for r in self._selected_rows()]

    def _select_path(self, path, clear=True):
        if self.mode == "icons":
            if clear:
                self.iconview.unselect_all()
            self.iconview.select_path(path)
        else:
            sel = self.listview.get_selection()
            if clear:
                sel.unselect_all()
            sel.select_path(path)

    def select_all(self):
        if self.mode == "icons":
            self.iconview.select_all()
        elif self.mode == "list":
            self.listview.get_selection().select_all()

    def open_rows(self, paths):
        rows = [self.store[p] for p in paths if not isinstance(p, tuple)]
        rows += [p[1][1][p[2]] for p in paths if isinstance(p, tuple)]
        dirs = [r for r in rows if r[C_DIR]]
        for r in rows:
            if not r[C_DIR]:
                files.open_file(r[C_FILE], r[C_INFO], self)
        if len(dirs) == 1:
            self.go(dirs[0][C_FILE])
        else:
            for r in dirs:
                self.app.new_window(r[C_FILE])

    # -- column view ---------------------------------------------------------------------------------
    def _reset_columns(self):
        for c in self.colbox.get_children():
            c.destroy()
        self._columns = []
        self._add_column(self.cwd)

    def _add_column(self, folder):
        store = Gtk.ListStore(GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT, GdkPixbuf.Pixbuf, str,
                              str, str, str, bool, GdkPixbuf.Pixbuf, str)
        store.set_sort_column_id(C_SORT, Gtk.SortType.ASCENDING)
        try:
            for info in folder.enumerate_children(files.ATTRS, Gio.FileQueryInfoFlags.NONE, None):
                if not self.show_hidden and (info.get_is_hidden() or info.get_is_backup()):
                    continue
                store.append(self._row(folder.get_child(info.get_name()), info, 32, 16))
        except GLib.Error:
            pass
        tv = Gtk.TreeView(model=store)
        tv.set_headers_visible(False)
        tv.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        col = Gtk.TreeViewColumn("Name")
        pr = Gtk.CellRendererPixbuf()
        col.pack_start(pr, False)
        col.add_attribute(pr, "pixbuf", C_SMALL)
        tr = Gtk.CellRendererText()
        tr.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(tr, True)
        col.add_attribute(tr, "text", C_NAME)
        ar = Gtk.CellRendererText()
        col.pack_end(ar, False)
        col.set_cell_data_func(ar, lambda c, cell, m, it, d: cell.set_property(
            "text", "›" if m[it][C_DIR] else ""))
        tv.append_column(col)
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(220, -1)
        sw.get_style_context().add_class("finder-column")
        sw.add(tv)
        entry = (folder, store, tv, sw)
        self._columns.append(entry)
        tv.get_selection().connect("changed", lambda s, e=entry: self._column_selected(e))
        tv.connect("row-activated", lambda v, p, c, e=entry: self.open_rows([("col", e, p)]))
        tv.connect("button-press-event", self._view_press)
        tv.connect("focus-in-event", lambda *_, e=entry: setattr(self, "_focus_col", e))
        self.colbox.pack_start(sw, False, False, 0)
        sw.show_all()
        GLib.idle_add(lambda: (self.colscroll.get_hadjustment().set_value(
            self.colscroll.get_hadjustment().get_upper()), False)[1])

    def _focused_column(self):
        e = getattr(self, "_focus_col", None)
        return e if e in self._columns else (self._columns[-1] if self._columns else None)

    def _column_selected(self, entry):
        self._focus_col = entry
        idx = self._columns.index(entry)
        for e in self._columns[idx + 1:]:
            e[3].destroy()
        self._columns = self._columns[:idx + 1]
        for c in self.colbox.get_children():
            if getattr(c, "is_preview", False):
                c.destroy()
        rows = entry[2].get_selection().get_selected_rows()[1]
        if len(rows) != 1:
            return
        r = entry[1][rows[0]]
        if r[C_DIR]:
            self._add_column(r[C_FILE])
        else:
            self._add_preview(r[C_FILE], r[C_INFO])

    def _add_preview(self, gfile, info):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("finder-preview")
        box.set_size_request(260, -1)
        box.set_border_width(16)
        box.is_preview = True
        box.pack_start(Gtk.Image.new_from_pixbuf(files.file_pixbuf(gfile, info, 128)), False, False, 8)
        name = Gtk.Label()
        name.set_markup("<b>%s</b>" % GLib.markup_escape_text(info.get_display_name()))
        name.set_line_wrap(True)
        box.pack_start(name, False, False, 0)
        ctype = info.get_content_type() or ""
        for k, v in (("Kind", Gio.content_type_get_description(ctype) if ctype else "Document"),
                     ("Size", human_size(info.get_size())),
                     ("Modified", human_date(info.get_attribute_uint64("time::modified")))):
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<small><span foreground='#888'>%s</span>  %s</small>" % (k, GLib.markup_escape_text(v)))
            box.pack_start(lab, False, False, 0)
        self.colbox.pack_start(box, False, False, 0)
        box.show_all()

    # -- search ------------------------------------------------------------------------------------------
    def _search_changed(self, entry):
        text = entry.get_text().strip()
        self.search_text = text
        if self._search_cancel is not None:
            self._search_cancel.cancel()
        if not text:
            self.load()
            return
        cancel = Gio.Cancellable()
        self._search_cancel = cancel
        root = self.cwd
        low = text.lower()
        size, small = self.icon_size, 20
        self._load_id += 1
        token = self._load_id
        self.store.clear()
        if self.mode == "columns":
            self.set_mode("list")

        def walk():
            found, stack = [], [(root, 0)]
            while stack and len(found) < 400 and not cancel.is_cancelled():
                d, depth = stack.pop()
                try:
                    for info in d.enumerate_children(files.ATTRS, Gio.FileQueryInfoFlags.NONE, cancel):
                        if info.get_is_hidden() or info.get_is_backup():
                            continue
                        child = d.get_child(info.get_name())
                        if low in info.get_display_name().lower():
                            found.append((child, info))
                            if len(found) % 40 == 0:
                                GLib.idle_add(self._search_batch, token, found[-40:], size, small)
                        if info.get_file_type() == Gio.FileType.DIRECTORY and depth < 8:
                            stack.append((child, depth + 1))
                except GLib.Error:
                    pass
            rest = len(found) % 40
            if rest and not cancel.is_cancelled():
                GLib.idle_add(self._search_batch, token, found[-rest:], size, small)
        threading.Thread(target=walk, daemon=True).start()

    def _search_batch(self, token, batch, size, small):
        if token != self._load_id:
            return False
        for gfile, info in batch:
            self.store.append(self._row(gfile, info, size, small))
        self.status.set_text("%d found" % len(self.store))
        return False

    def show_color(self, color):
        """All folders that have this color."""
        self._load_id += 1
        self.store.clear()
        self.set_title(dict(files.COLOR_NAMES)[color])
        db = files._load_db()
        for path, c in sorted(db.items()):
            if c != color:
                continue
            f = Gio.File.new_for_commandline_arg(path)
            try:
                info = f.query_info(files.ATTRS, Gio.FileQueryInfoFlags.NONE, None)
            except GLib.Error:
                continue
            self.store.append(self._row(f, info, self.icon_size, 20))
        if self.mode == "columns":
            self.set_mode("icons")
        self._update_status()

    # -- context menus -----------------------------------------------------------------------------------
    def _view_press(self, view, ev):
        if ev.button != 3:
            return False
        path = None
        if isinstance(view, Gtk.IconView):
            path = view.get_path_at_pos(int(ev.x), int(ev.y))
            if path is not None and not view.path_is_selected(path):
                view.unselect_all()
                view.select_path(path)
            elif path is None:
                view.unselect_all()
        else:
            hit = view.get_path_at_pos(int(ev.x), int(ev.y))
            sel = view.get_selection()
            if hit:
                path = hit[0]
                if not sel.path_is_selected(path):
                    sel.unselect_all()
                    sel.select_path(path)
            else:
                sel.unselect_all()
            for e in self._columns:
                if e[2] is view:
                    self._focus_col = e
        self._menu(ev, bool(path))
        return True

    def _mi(self, label, action, sensitive=True):
        mi = Gtk.MenuItem(label=label)
        mi.set_sensitive(sensitive)
        mi.connect("activate", lambda *_: self.activate_action(action, None))
        return mi

    def _menu(self, ev, on_item):
        menu = Gtk.Menu()
        in_trash = self.cwd.get_uri().startswith(TRASH)
        sel = self.selected_files()
        if on_item and sel:
            menu.append(self._mi("Open", "open"))
            if len(sel) == 1 and sel[0][1].get_file_type() != Gio.FileType.DIRECTORY:
                gfile, info = sel[0]
                sub = Gtk.Menu()
                for app in files.apps_for(info)[:10]:
                    mi = Gtk.MenuItem(label=app.get_name())
                    mi.connect("activate", lambda m, a=app: a.launch(
                        [gfile], Gdk.Display.get_default().get_app_launch_context()))
                    sub.append(mi)
                sub.append(Gtk.SeparatorMenuItem())
                other = Gtk.MenuItem(label="Other…")
                other.connect("activate", lambda *_: files.open_with_dialog(gfile, info, self))
                sub.append(other)
                ow = Gtk.MenuItem(label="Open With")
                ow.set_submenu(sub)
                menu.append(ow)
            menu.append(Gtk.SeparatorMenuItem())
            if in_trash:
                pb = Gtk.MenuItem(label="Put Back")
                pb.connect("activate", lambda *_: self.put_back())
                menu.append(pb)
                de = Gtk.MenuItem(label="Delete Immediately…")
                de.connect("activate", lambda *_: self.delete_now())
                menu.append(de)
            else:
                menu.append(self._mi("Get Info", "get-info"))
                menu.append(self._mi("Rename…", "rename", len(sel) == 1))
                menu.append(self._mi("Duplicate", "duplicate"))
                menu.append(self._mi("Copy", "copy"))
                menu.append(self._mi("Cut", "cut"))
                menu.append(self._mi("Move to Trash", "trash"))
                dirs = [f for f, i in sel if i.get_file_type() == Gio.FileType.DIRECTORY]
                if dirs:
                    menu.append(Gtk.SeparatorMenuItem())
                    sub = Gtk.Menu()
                    for mi in files.color_menu_items(dirs, self._recolor):
                        sub.append(mi)
                    cm = Gtk.MenuItem(label="Folder Color")
                    cm.set_submenu(sub)
                    menu.append(cm)
        else:
            if in_trash:
                menu.append(self._mi("Empty Trash…", "empty-trash"))
            else:
                menu.append(self._mi("New Folder", "new-folder"))
                menu.append(self._mi("Paste", "paste"))
                menu.append(Gtk.SeparatorMenuItem())
                menu.append(self._mi("Open in Terminal", "terminal"))
                menu.append(self._mi("Get Info", "get-info"))
            menu.append(Gtk.SeparatorMenuItem())
            for label, mode in (("as Icons", "icons"), ("as List", "list"), ("as Columns", "columns")):
                mi = Gtk.MenuItem(label="View " + label)
                mi.connect("activate", lambda *_, m=mode: self.set_mode(m))
                menu.append(mi)
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(ev)

    def _recolor(self):
        size = self.icon_size
        for r in self.store:
            if r[C_DIR]:
                r[C_PIX] = files.file_pixbuf(r[C_FILE], r[C_INFO], size)
                r[C_SMALL] = files.file_pixbuf(r[C_FILE], r[C_INFO], 20)

    # -- file operations -----------------------------------------------------------------------------------
    def _target_dir(self):
        return self.cwd

    def new_folder(self):
        try:
            f = files.new_folder(self._target_dir())
        except GLib.Error as e:
            self.error("The folder could not be made.", e.message)
            return
        self._rename_after_load = f.get_uri()
        self.load()
        GLib.timeout_add(400, self._rename_new)

    def _rename_new(self):
        uri = getattr(self, "_rename_after_load", None)
        self._rename_after_load = None
        for i, r in enumerate(self.store):
            if r[C_FILE].get_uri() == uri:
                self._select_path(Gtk.TreePath(i))
                self.rename_selected()
                break
        return False

    def rename_selected(self):
        sel = self.selected_files()
        if len(sel) != 1:
            return
        gfile, info = sel[0]
        dlg = Gtk.Dialog(title="Rename", transient_for=self, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Rename", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(info.get_display_name())
        entry.set_activates_default(True)
        entry.set_width_chars(32)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(Gtk.Label(label="New name:", xalign=0))
        box.add(entry)
        dlg.show_all()
        stem, ext = os.path.splitext(info.get_display_name())
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        entry.select_region(0, len(info.get_display_name() if is_dir or not ext else stem))
        if dlg.run() == Gtk.ResponseType.OK:
            try:
                files.rename(gfile, entry.get_text())
            except GLib.Error as e:
                self.error("The item could not be renamed.", e.message)
        dlg.destroy()

    def trash_selected(self):
        sel = [f for f, i in self.selected_files()]
        if not sel:
            return
        if self.cwd.get_uri().startswith(TRASH):
            self.delete_now()
            return
        failed = files.trash(sel)
        if failed:
            self.error("Some items could not be moved to the Trash.",
                       "\n".join("%s: %s" % (f.get_basename(), m) for f, m in failed[:6]))

    def empty_trash(self):
        if not self.confirm("Empty the Trash?", "Everything in the Trash is deleted for good.", "Empty Trash"):
            return
        Gio.Subprocess.new(["gio", "trash", "--empty"], Gio.SubprocessFlags.NONE)
        GLib.timeout_add(600, lambda: (self.load(), False)[1])

    def put_back(self):
        for gfile, info in self.selected_files():
            orig = info.get_attribute_byte_string("trash::orig-path")
            if not orig:
                continue
            dest = Gio.File.new_for_path(orig)
            parent = dest.get_parent()
            try:
                if parent is not None and not parent.query_exists(None):
                    parent.make_directory_with_parents(None)
                if dest.query_exists(None):
                    dest = files.unique_child(parent, dest.get_basename())
                gfile.move(dest, Gio.FileCopyFlags.NONE, None, None, None)
            except GLib.Error as e:
                self.error("The item could not be put back.", e.message)

    def delete_now(self):
        sel = [f for f, i in self.selected_files()]
        if not sel or not self.confirm("Delete %d item%s immediately?" % (len(sel), "" if len(sel) == 1 else "s"),
                                       "You can't undo this.", "Delete"):
            return
        for f in sel:
            try:
                files._delete_tree(f)
            except GLib.Error as e:
                self.error("The item could not be deleted.", e.message)

    def clipboard(self, cut):
        sel = [f for f, i in self.selected_files()]
        if sel:
            files.FileClipboard.get().set(sel, cut=cut)

    def paste(self):
        if not files.FileClipboard.get().paste_into(self._target_dir(), self._transfer_done):
            self.error("There is nothing to paste.", "Copy some files first.")

    def duplicate(self):
        sel = [f for f, i in self.selected_files()]
        if sel:
            files.transfer(sel, self.cwd, move=False, on_done=self._transfer_done)

    def _transfer_done(self, errors):
        if errors:
            self.error("Some items could not be copied.",
                       "\n".join("%s: %s" % (f.get_basename(), m) for f, m in errors[:6]))
        return False

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.load()
        if self.mode == "columns":
            self._reset_columns()

    def open_terminal(self):
        path = self.cwd.get_path()
        if path:
            from .common import spawn
            spawn(["xfce4-terminal", "--working-directory", path])

    def goto_dialog(self):
        dlg = Gtk.Dialog(title="Go to Folder", transient_for=self, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Go", Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(self.cwd.get_path() or self.cwd.get_uri())
        entry.set_activates_default(True)
        entry.set_width_chars(40)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        box.add(Gtk.Label(label="Go to the folder:", xalign=0))
        box.add(entry)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            text = os.path.expanduser(entry.get_text().strip())
            f = Gio.File.new_for_commandline_arg(text)
            if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY:
                self.go(f)
            else:
                self.error("The folder can't be found.", text)
        dlg.destroy()

    # -- drag and drop ---------------------------------------------------------------------------------------
    def _drag_get(self, view, ctx, data, info, time_):
        data.set_uris([f.get_uri() for f, i in self.selected_files()])

    def _drag_received(self, view, ctx, x, y, data, info, time_):
        uris = data.get_uris() or []
        dest = self.cwd
        if isinstance(view, Gtk.IconView):
            hit = view.get_dest_item_at_pos(x, y)
            path = hit[1] if hit and hit[0] else None
        else:
            hit = view.get_dest_row_at_pos(x, y)
            path = hit[0] if hit else None
        if path is not None:
            r = self.store[path]
            if r[C_DIR]:
                dest = r[C_FILE]
        sources = [Gio.File.new_for_uri(u) for u in uris]
        sources = [s for s in sources if not s.equal(dest)]
        if not sources:
            return
        move = ctx.get_selected_action() == Gdk.DragAction.MOVE and files.same_filesystem(sources[0], dest)
        files.transfer(sources, dest, move=move, on_done=self._transfer_done)
        ctx.finish(True, False, time_)

    def _drop_on_place(self, lb, ctx, x, y, data, info, time_):
        row = lb.get_row_at_y(y)
        t = getattr(row, "target", None) if row else None
        uris = data.get_uris() or []
        if not isinstance(t, Gio.File) or not uris:
            return
        sources = [Gio.File.new_for_uri(u) for u in uris]
        if t.get_uri().startswith(TRASH):
            files.trash(sources)
            return
        move = files.same_filesystem(sources[0], t)
        files.transfer(sources, t, move=move, on_done=self._transfer_done)

    # -- Get Info and Quick Look -------------------------------------------------------------------------------
    def get_info(self):
        sel = self.selected_files()
        if not sel:
            try:
                sel = [(self.cwd, self.cwd.query_info(files.ATTRS, Gio.FileQueryInfoFlags.NONE, None))]
            except GLib.Error:
                return
        for gfile, info in sel[:5]:
            InfoWindow(self, gfile, info)

    def quick_look(self):
        sel = self.selected_files()
        if sel:
            QuickLook(self, *sel[0])

    # -- keys and dialogs -----------------------------------------------------------------------------------------
    def _key(self, w, ev):
        if self.search.has_focus():
            if ev.keyval == Gdk.KEY_Escape:
                self.search.set_text("")
                self.active_view().grab_focus()
                return True
            return False
        k = ev.keyval
        if k == Gdk.KEY_space and self.selected_files():
            self.quick_look()
            return True
        if k in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.trash_selected()
            return True
        if k == Gdk.KEY_F2:
            self.rename_selected()
            return True
        if k == Gdk.KEY_BackSpace and not ev.state & Gdk.ModifierType.CONTROL_MASK:
            self.back()
            return True
        return False

    def error(self, title, detail=""):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK, text=title)
        if detail:
            dlg.format_secondary_text(detail)
        dlg.run()
        dlg.destroy()

    def confirm(self, title, detail, ok_label):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=title)
        dlg.format_secondary_text(detail)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button(ok_label, Gtk.ResponseType.OK).get_style_context().add_class("destructive-action")
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return ok


class InfoWindow(Gtk.Window):
    """Get Info: name, kind, size, where, dates, folder color."""

    def __init__(self, parent, gfile, info):
        super().__init__(title="%s Info" % info.get_display_name(), transient_for=parent)
        self.set_wmclass("slope-files", "Slope-files")
        self.set_default_size(300, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(16)
        self.add(box)
        top = Gtk.Box(spacing=12)
        top.pack_start(Gtk.Image.new_from_pixbuf(files.file_pixbuf(gfile, info, 64)), False, False, 0)
        name = Gtk.Label(xalign=0)
        name.set_markup("<b>%s</b>" % GLib.markup_escape_text(info.get_display_name()))
        name.set_line_wrap(True)
        top.pack_start(name, True, True, 0)
        box.pack_start(top, False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        ctype = info.get_content_type() or ""
        self.size_lbl = Gtk.Label(label="Calculating…" if is_dir else
                                  "%s (%s bytes)" % (human_size(info.get_size()), "{:,}".format(info.get_size())),
                                  xalign=0)
        parent_f = gfile.get_parent()
        rows = [("Kind:", Gtk.Label(label="Folder" if is_dir else Gio.content_type_get_description(ctype), xalign=0)),
                ("Size:", self.size_lbl),
                ("Where:", Gtk.Label(label=(parent_f.get_path() or parent_f.get_uri()) if parent_f else "", xalign=0)),
                ("Modified:", Gtk.Label(label=human_date(info.get_attribute_uint64("time::modified")), xalign=0))]
        for i, (k, w) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=1)
            kl.get_style_context().add_class("dim-label")
            w.set_selectable(True)
            w.set_line_wrap(True)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)
        if is_dir:
            box.pack_start(Gtk.Separator(), False, False, 4)
            cl = Gtk.Label(label="Folder color", xalign=0)
            box.pack_start(cl, False, False, 0)
            sw = Gtk.Box(spacing=6)
            current = files.get_color(gfile, info)
            for key, label in files.COLOR_NAMES:
                b = Gtk.RadioButton.new_from_widget(sw.get_children()[0] if sw.get_children() else None)
                b.set_mode(False)
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.set_image(Gtk.Image.new_from_pixbuf(files.color_swatch(key, 18)))
                b.set_tooltip_text(label)
                b.set_active(key == current)
                b.connect("toggled", lambda w, k=key: w.get_active() and (files.set_color(gfile, k),
                                                                          parent._recolor()))
                sw.pack_start(b, False, False, 0)
            box.pack_start(sw, False, False, 0)
            threading.Thread(target=self._measure, args=(gfile,), daemon=True).start()
        self.show_all()

    def _measure(self, gfile):
        total, count = 0, 0
        stack = [gfile]
        while stack and count < 200000:
            d = stack.pop()
            try:
                for i in d.enumerate_children("standard::name,standard::type,standard::size",
                                              Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None):
                    count += 1
                    if i.get_file_type() == Gio.FileType.DIRECTORY:
                        stack.append(d.get_child(i.get_name()))
                    else:
                        total += i.get_size()
            except GLib.Error:
                pass
        GLib.idle_add(lambda: (self.size_lbl.set_text("%s for %d items" % (human_size(total), count)), False)[1])


class QuickLook(Gtk.Window):
    """Space bar preview: pictures, text, or a big icon with details."""

    def __init__(self, parent, gfile, info):
        super().__init__(title=info.get_display_name(), transient_for=parent)
        self.set_wmclass("slope-files", "Slope-files")
        self.set_default_size(720, 520)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        ctype = info.get_content_type() or ""
        path = gfile.get_path()
        content = None
        if path and ctype.startswith("image/"):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 900, 640, True)
                content = Gtk.Image.new_from_pixbuf(pb)
                self.set_default_size(min(900, pb.get_width() + 40), min(680, pb.get_height() + 40))
            except GLib.Error:
                content = None
        elif path and (ctype.startswith("text/") or Gio.content_type_is_a(ctype, "text/plain")):
            try:
                with open(path, errors="replace") as f:
                    text = f.read(200000)
                tv = Gtk.TextView()
                tv.set_editable(False)
                tv.set_monospace(True)
                tv.set_left_margin(12)
                tv.get_buffer().set_text(text)
                content = Gtk.ScrolledWindow()
                content.add(tv)
            except OSError:
                content = None
        if content is None:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
            box.pack_start(Gtk.Image.new_from_pixbuf(files.file_pixbuf(gfile, info, 256)), False, False, 0)
            lab = Gtk.Label()
            lab.set_markup("<big><b>%s</b></big>\n%s" % (GLib.markup_escape_text(info.get_display_name()),
                                                        GLib.markup_escape_text(human_size(info.get_size()))))
            lab.set_justify(Gtk.Justification.CENTER)
            box.pack_start(lab, False, False, 0)
            content = box
            self.set_default_size(420, 420)
        self.add(content)
        self.connect("key-press-event", lambda w, ev: (self.destroy(), True)[1]
                     if ev.keyval in (Gdk.KEY_space, Gdk.KEY_Escape) else False)
        self.show_all()


class FilesApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.settings = {}

    def save_settings(self):
        from .common import update_setting
        update_setting("files", dict(self.settings))

    def do_startup(self):
        Gtk.Application.do_startup(self)
        from .common import load_settings
        self.settings = dict(load_settings().get("files", {}))
        from .common import install_css
        install_css(CSS, DARK_CSS)        # DARK_CSS follows dark mode live
        self._menus()
        # like a Mac app: keeps running (in the Dock) when its last window closes
        self.hold()
        from . import keepalive
        keepalive.register(APP_ID, "/usr/share/applications/slope-files.desktop")

        def poll():
            if keepalive.quit_requested() and not self.get_windows():
                keepalive.unregister()
                self.release()
                return False
            return True
        GLib.timeout_add(500, poll)
        q = Gio.SimpleAction.new("quit", None)
        q.connect("activate", lambda *_: self._quit())
        self.add_action(q)
        nw = Gio.SimpleAction.new("new-window", None)
        nw.connect("activate", lambda *_: self.new_window(Gio.File.new_for_path(GLib.get_home_dir())))
        self.add_action(nw)

    def _quit(self):
        from . import keepalive
        keepalive.unregister()
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
        bar.append_submenu("File", menu(
            section(("New Window", "app.new-window"), ("New Folder", "win.new-folder")),
            section(("Open", "win.open"), ("Quick Look", "win.quicklook"), ("Get Info", "win.get-info")),
            section(("Rename…", "win.rename"), ("Duplicate", "win.duplicate"), ("Move to Trash", "win.trash")),
            section(("Empty Trash…", "win.empty-trash"), ("Close Window", "win.close"))))
        bar.append_submenu("Edit", menu(
            section(("Cut", "win.cut"), ("Copy", "win.copy"), ("Paste", "win.paste"),
                    ("Select All", "win.select-all")),
            section(("Find…", "win.search"))))
        bar.append_submenu("View", menu(
            section(("as Icons", "win.view::icons"), ("as List", "win.view::list"),
                    ("as Columns", "win.view::columns")),
            section(("Show Hidden Files", "win.hidden"))))
        bar.append_submenu("Go", menu(
            section(("Back", "win.back"), ("Forward", "win.forward"), ("Enclosing Folder", "win.up")),
            section(("Home", "win.go::home"), ("Desktop", "win.go::desktop"), ("Documents", "win.go::documents"),
                    ("Downloads", "win.go::downloads"), ("Pictures", "win.go::pictures"), ("Music", "win.go::music"),
                    ("Computer", "win.go::computer"), ("Trash", "win.go::trash")),
            section(("Go to Folder…", "win.goto"), ("Open in Terminal", "win.terminal"))))
        self.set_menubar(bar)
        for action, keys in (("app.new-window", ["<Primary>n"]), ("win.new-folder", ["<Primary><Shift>n"]),
                             ("win.open", ["<Primary>o", "<Primary>Down"]), ("win.get-info", ["<Primary>i"]),
                             ("win.duplicate", ["<Primary>d"]), ("win.trash", ["<Primary>BackSpace"]),
                             ("win.close", ["<Primary>w"]), ("win.cut", ["<Primary>x"]),
                             ("win.copy", ["<Primary>c"]), ("win.paste", ["<Primary>v"]),
                             ("win.select-all", ["<Primary>a"]), ("win.search", ["<Primary>f"]),
                             ("win.view::icons", ["<Primary>1"]), ("win.view::list", ["<Primary>2"]),
                             ("win.view::columns", ["<Primary>3"]), ("win.hidden", ["<Primary>h"]),
                             ("win.back", ["<Alt>Left", "<Primary>bracketleft"]),
                             ("win.forward", ["<Alt>Right", "<Primary>bracketright"]),
                             ("win.up", ["<Alt>Up", "<Primary>Up"]), ("win.go::home", ["<Primary><Shift>h"]),
                             ("win.go::desktop", ["<Primary><Shift>d"]), ("win.go::computer", ["<Primary><Shift>c"]),
                             ("win.goto", ["<Primary>l", "<Primary><Shift>g"]), ("app.quit", ["<Primary>q"])):
            self.set_accels_for_action(action, keys)

    def new_window(self, gfile):
        w = FilesWindow(self, gfile)
        w.present()
        return w

    def do_activate(self):
        self.new_window(Gio.File.new_for_path(GLib.get_home_dir()))

    def do_open(self, gfiles, n, hint):
        for f in gfiles:
            ftype = f.query_file_type(Gio.FileQueryInfoFlags.NONE, None)
            if ftype == Gio.FileType.DIRECTORY or f.get_uri().startswith(TRASH):
                self.new_window(f)
            else:   # "show in folder": open its folder with it selected
                parent = f.get_parent() or f
                w = self.new_window(parent)

                def select(w=w, uri=f.get_uri()):
                    for i, r in enumerate(w.store):
                        if r[C_FILE].get_uri() == uri:
                            w._select_path(Gtk.TreePath(i))
                            return False
                    return False
                GLib.timeout_add(500, select)


def main(argv=None):
    GLib.set_prgname("slope-files")
    GLib.set_application_name("Files")
    app = FilesApp()
    return app.run(sys.argv if argv is None else argv)
