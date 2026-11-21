"""Files, shared by the desktop and Slope Files (the file manager):
icons (glossy coloured folders, image thumbnails), folder colours,
opening files, rename / trash / new folder, copy and move, and a clipboard
that other file managers understand (x-special/gnome-copied-files).
"""
import json
import os
import threading
import urllib.parse

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402
from .common import icon_for, launch  # noqa: E402

ATTRS = ("standard::name,standard::display-name,standard::type,standard::icon,standard::size,"
         "standard::content-type,standard::is-hidden,standard::is-backup,standard::symlink-target,"
         "standard::is-symlink,time::modified,access::can-write,access::can-trash,"
         "access::can-execute,metadata::slope-color")
FILES_APP = "slope-files.desktop"
COLORS = {k: c for k, _n, c in art.FOLDER_COLORS}
COLOR_NAMES = [(k, n) for k, n, _c in art.FOLDER_COLORS]
_COLOR_DB = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "slope", "folder-colors.json")
_db = None
_pix = {}


# -- special places ---------------------------------------------------------------
def special_dirs():
    """{path: (label, symbolic icon)} for the home folder and the XDG folders."""
    home = GLib.get_home_dir()
    out = {home: ("Home", "user-home-symbolic")}
    for kind, icon in ((GLib.UserDirectory.DIRECTORY_DESKTOP, "user-desktop-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "folder-documents-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "folder-download-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_MUSIC, "folder-music-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_PICTURES, "folder-pictures-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_VIDEOS, "folder-videos-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_PUBLIC_SHARE, "folder-publicshare-symbolic"),
                       (GLib.UserDirectory.DIRECTORY_TEMPLATES, "folder-templates-symbolic")):
        p = GLib.get_user_special_dir(kind)
        if p and p != home:
            out[p] = (os.path.basename(p), icon)
    return out


def desktop_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP) or os.path.expanduser("~/Desktop")


# -- folder colours ---------------------------------------------------------------------
def _load_db():
    global _db
    if _db is None:
        try:
            with open(_COLOR_DB) as f:
                _db = json.load(f)
        except (OSError, ValueError):
            _db = {}
    return _db


def get_color(gfile, info=None):
    """The folder's colour name ("blue" when none was chosen)."""
    c = None
    if info is not None and info.has_attribute("metadata::slope-color"):
        c = info.get_attribute_string("metadata::slope-color")
    if not c:
        c = _load_db().get(gfile.get_path() or gfile.get_uri())
    return c if c in COLORS else "blue"


def set_color(gfile, color):
    color = color if color in COLORS and color != "blue" else ""
    try:   # GIO metadata follows the folder when it is moved or renamed
        gfile.set_attribute_string("metadata::slope-color", color or "", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        pass
    db = _load_db()
    key = gfile.get_path() or gfile.get_uri()
    if color:
        db[key] = color
    else:
        db.pop(key, None)
    try:
        os.makedirs(os.path.dirname(_COLOR_DB), exist_ok=True)
        with open(_COLOR_DB + ".tmp", "w") as f:
            json.dump(db, f)
        os.replace(_COLOR_DB + ".tmp", _COLOR_DB)
    except OSError:
        pass
    _pix.clear()


def color_swatch(color, size=14):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.arc(size / 2.0, size / 2.0, size / 2.0 - 1, 0, 6.2832)
    r, g, b = art.hex_rgb(COLORS[color])
    grad = cairo.LinearGradient(0, 0, 0, size)
    grad.add_color_stop_rgb(0, min(1, r * 1.25), min(1, g * 1.25), min(1, b * 1.25))
    grad.add_color_stop_rgb(1, r * 0.85, g * 0.85, b * 0.85)
    cr.set_source(grad)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.3)
    cr.set_line_width(1)
    cr.stroke()
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


# -- icons ------------------------------------------------------------------------------
def folder_pixbuf(size, color="blue", symbol=None):
    key = ("folder", size, color, symbol)
    if key in _pix:
        return _pix[key]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    top, bottom = art.draw_folder(cr, size * 0.03, size * 0.03, size * 0.94, COLORS.get(color, COLORS["blue"]))
    if symbol and size >= 32:
        try:
            info = Gtk.IconTheme.get_default().lookup_icon(symbol, int(size * 0.34), Gtk.IconLookupFlags.FORCE_SIZE)
            if info is not None:
                r, g, b = art.hex_rgb(COLORS.get(color, COLORS["blue"]))
                fg = Gdk.RGBA(r * 0.45, g * 0.45, b * 0.45, 0.6)
                sym, _ = info.load_symbolic(fg, None, None, None)
                sx = (size - sym.get_width()) / 2.0
                sy = top + (bottom - top - sym.get_height()) / 2.0 + size * 0.02
                Gdk.cairo_set_source_pixbuf(cr, sym, sx, sy)
                cr.paint()
        except GLib.Error:
            pass
    pb = Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)
    _pix[key] = pb
    return pb


def _framed(pb, size):
    """An image thumbnail on white photo paper with a soft shadow."""
    w, h = pb.get_width(), pb.get_height()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    b = max(2, size // 24)
    x, y = (size - w) / 2.0, (size - h) / 2.0
    for i in range(3):
        cr.rectangle(x - b - i + 1, y - b - i + 2, w + 2 * b + 2 * i - 2, h + 2 * b + 2 * i - 2)
        cr.set_source_rgba(0, 0, 0, 0.08)
        cr.fill()
    cr.rectangle(x - b, y - b, w + 2 * b, h + 2 * b)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
    cr.paint()
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


def theme_pixbuf(gicon, size, fallback="text-x-generic"):
    theme = Gtk.IconTheme.get_default()
    info = None
    if gicon is not None:
        info = theme.lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
    if info is None:
        info = theme.lookup_icon(fallback, size, Gtk.IconLookupFlags.FORCE_SIZE)
    try:
        return info.load_icon() if info is not None else None
    except GLib.Error:
        return None


def file_pixbuf(gfile, info, size):
    """The icon Slope shows for a file or folder."""
    ftype = info.get_file_type()
    path = gfile.get_path()
    if ftype == Gio.FileType.DIRECTORY or (ftype == Gio.FileType.MOUNTABLE):
        sym = special_dirs().get(path, (None, None))[1] if path else None
        return folder_pixbuf(size, get_color(gfile, info), sym)
    ctype = info.get_content_type() or ""
    name = info.get_name() or ""
    if name.endswith(".desktop") and path:
        try:
            app = Gio.DesktopAppInfo.new_from_filename(path)
        except TypeError:
            app = None
        if app is not None:
            return icon_for(app, size)
    if ctype.startswith("image/") and path and info.get_size() < 40 * 1024 * 1024:
        mtime = info.get_attribute_uint64("time::modified")
        key = ("thumb", path, mtime, size)
        if key in _pix:
            return _pix[key]
        try:
            inner = int(size * 0.86)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, inner, inner, True)
            pb = _framed(pb, size)
            if len(_pix) > 600:
                _pix.clear()
            _pix[key] = pb
            return pb
        except GLib.Error:
            pass
    return theme_pixbuf(info.get_icon(), size)


# -- opening ------------------------------------------------------------------------------
def open_folder(gfile):
    """Open a folder in Slope Files."""
    from .common import open_app
    open_app(FILES_APP, gfile.get_uri())


def open_file(gfile, info=None, parent=None):
    """Open like a double-click: folders in Slope Files, apps run, documents
    in their default app (or ask which app)."""
    if info is None:
        try:
            info = gfile.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        open_folder(gfile)
        return
    path = gfile.get_path()
    if path and path.endswith(".desktop"):
        try:
            app = Gio.DesktopAppInfo.new_from_filename(path)
        except TypeError:
            app = None
        if app is not None:
            launch(app)
            return
    ctype = info.get_content_type()
    app = Gio.AppInfo.get_default_for_type(ctype, False) if ctype else None
    if app is not None:
        try:
            app.launch([gfile], Gdk.Display.get_default().get_app_launch_context())
            return
        except GLib.Error:
            pass
    open_with_dialog(gfile, info, parent)


def open_with_dialog(gfile, info, parent=None):
    dlg = Gtk.AppChooserDialog.new(parent, Gtk.DialogFlags.MODAL, gfile)
    dlg.set_title("Open With")
    if dlg.run() == Gtk.ResponseType.OK:
        app = dlg.get_app_info()
        if app is not None:
            try:
                app.launch([gfile], Gdk.Display.get_default().get_app_launch_context())
            except GLib.Error:
                pass
    dlg.destroy()


def apps_for(info):
    ctype = info.get_content_type() if info is not None else None
    return Gio.AppInfo.get_all_for_type(ctype) if ctype else []


# -- changing files -------------------------------------------------------------------------
def unique_child(directory, name):
    """directory/name, or "name 2", "name 3"... if it exists."""
    child = directory.get_child(name)
    if not child.query_exists(None):
        return child
    stem, ext = os.path.splitext(name)
    if not ext or len(ext) > 6 or " " in ext:
        stem, ext = name, ""
    i = 2
    while True:
        child = directory.get_child("%s %d%s" % (stem, i, ext))
        if not child.query_exists(None):
            return child
        i += 1


def new_folder(directory, name="untitled folder"):
    child = unique_child(directory, name)
    child.make_directory(None)
    return child


def rename(gfile, new_name):
    """Rename; returns the renamed Gio.File or raises GLib.Error."""
    new_name = new_name.strip().replace("/", "-")
    if not new_name or new_name == gfile.get_basename():
        return gfile
    return gfile.set_display_name(new_name, None)


def trash(gfiles):
    """Move to the Trash; returns [(gfile, error message)] for the ones that failed."""
    failed = []
    for f in gfiles:
        try:
            f.trash(None)
        except GLib.Error as e:
            failed.append((f, e.message))
    return failed


def _copy_tree(src, dst, cancellable, progress):
    info = src.query_info("standard::type,standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        dst.make_directory(cancellable)
        for child in src.enumerate_children("standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                                            cancellable):
            name = child.get_name()
            _copy_tree(src.get_child(name), dst.get_child(name), cancellable, progress)
    else:
        src.copy(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA,
                 cancellable, None, None)
        progress(src)


def transfer(sources, dest_dir, move=False, on_progress=None, on_done=None):
    """Copy or move files into dest_dir in a background thread.
    on_progress(done, total, name) and on_done(errors) run in the main loop."""
    sources = [s for s in sources if s is not None]
    cancellable = Gio.Cancellable()

    def work():
        errors = []
        total = len(sources)
        for i, src in enumerate(sources):
            name = src.get_basename()
            if on_progress:
                GLib.idle_add(on_progress, i, total, name)
            parent = src.get_parent()
            if move and parent is not None and parent.equal(dest_dir):
                continue
            if dest_dir.equal(src) or dest_dir.has_prefix(src):
                errors.append((src, "A folder can't be copied into itself."))
                continue
            dst = unique_child(dest_dir, name)
            try:
                if move:
                    try:
                        src.move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA,
                                 cancellable, None, None)
                        continue
                    except GLib.Error as e:
                        if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.WOULD_RECURSE) and \
                                not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                            raise
                    _copy_tree(src, dst, cancellable, lambda f: None)
                    _delete_tree(src)
                else:
                    _copy_tree(src, dst, cancellable, lambda f: None)
            except GLib.Error as e:
                errors.append((src, e.message))
        if on_progress:
            GLib.idle_add(on_progress, total, total, "")
        if on_done:
            GLib.idle_add(on_done, errors)
    threading.Thread(target=work, daemon=True).start()
    return cancellable


def _delete_tree(f):
    info = f.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        for child in f.enumerate_children("standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None):
            _delete_tree(f.get_child(child.get_name()))
    f.delete(None)


def same_filesystem(a, b):
    try:
        ia = a.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None)
        ib = b.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None)
        return ia.get_attribute_string("id::filesystem") == ib.get_attribute_string("id::filesystem")
    except GLib.Error:
        return False


# -- clipboard ------------------------------------------------------------------------------
GNOME_FILES = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"


class FileClipboard:
    """Owns the clipboard with copied/cut files, in the format Thunar,
    Nautilus and other file managers use."""
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.widget = Gtk.Invisible()
        self.uris = []
        self.cut = False
        sel = Gdk.Atom.intern("CLIPBOARD", False)
        for i, target in enumerate((GNOME_FILES, URI_LIST, "UTF8_STRING", "text/plain")):
            Gtk.selection_add_target(self.widget, sel, Gdk.Atom.intern(target, False), i)
        self.widget.connect("selection-get", self._get)
        self.widget.connect("selection-clear-event", self._clear)

    def set(self, gfiles, cut=False):
        self.uris = [f.get_uri() for f in gfiles]
        self.cut = cut
        Gtk.selection_owner_set(self.widget, Gdk.Atom.intern("CLIPBOARD", False), Gtk.get_current_event_time())

    def _get(self, widget, data, info, time_):
        if info == 0:
            text = ("cut" if self.cut else "copy") + "\n" + "\n".join(self.uris)
            data.set(Gdk.Atom.intern(GNOME_FILES, False), 8, text.encode())
        elif info == 1:
            data.set_uris(self.uris)
        else:
            data.set_text("\n".join(urllib.parse.unquote(u.replace("file://", "")) for u in self.uris), -1)

    def _clear(self, *a):
        self.uris = []
        return False

    @staticmethod
    def read():
        """(gfiles, cut) currently on the clipboard."""
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        data = cb.wait_for_contents(Gdk.Atom.intern(GNOME_FILES, False))
        if data is not None and data.get_length() > 0:
            lines = data.get_data().decode(errors="replace").splitlines()
            if lines:
                cut = lines[0].strip() == "cut"
                return [Gio.File.new_for_uri(u) for u in lines[1:] if u.strip()], cut
        uris = cb.wait_for_uris()
        if uris:
            return [Gio.File.new_for_uri(u) for u in uris], False
        return [], False

    def paste_into(self, dest_dir, on_done=None):
        files, cut = self.read()
        if not files:
            return False
        transfer(files, dest_dir, move=cut, on_done=on_done)
        if cut:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).clear()
        return True


def color_menu_items(gfiles, on_change=None):
    """A row of colour items for a context menu ("Folder Colour")."""
    items = []
    for key, label in COLOR_NAMES:
        mi = Gtk.MenuItem()
        box = Gtk.Box(spacing=8)
        box.pack_start(Gtk.Image.new_from_pixbuf(color_swatch(key)), False, False, 0)
        box.pack_start(Gtk.Label(label=label if key != "blue" else "Blue (normal)", xalign=0), True, True, 0)
        mi.add(box)

        def pick(m, key=key):
            for f in gfiles:
                set_color(f, key)
            if on_change:
                on_change()
        mi.connect("activate", pick)
        items.append(mi)
    return items
