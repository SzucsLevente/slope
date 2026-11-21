"""Icons on the desktop: the files and folders in ~/Desktop, plus plugged-in drives.

Like on a Mac they line up from the top-right corner.  Click to select (drag
a rectangle or Ctrl-click for several), double-click to open, drag them around
(positions are remembered) or into other apps, drop files on the desktop,
right-click for Open With, Rename, Move to Trash, Folder Color...
"""
import json
import os

import cairo
import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import files  # noqa: E402
from .common import open_app, spawn  # noqa: E402

CELL_W, CELL_H, ICON = 108, 104, 64
MARGIN = 14
POS_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "slope", "desktop-icons.json")
URI_TARGET = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]


class Item:
    def __init__(self, gfile, info=None, mount=None):
        self.gfile = gfile
        self.info = info
        self.mount = mount
        self.name = mount.get_name() if mount is not None else info.get_display_name()
        if mount is None and self.name.endswith(".desktop") and gfile.get_path():
            try:         # an app launcher shows the app's name, like on a Mac
                app = Gio.DesktopAppInfo.new_from_filename(gfile.get_path())
            except TypeError:
                app = None
            if app is not None:
                from .common import friendly_name
                self.name = friendly_name(app)
        self.key = ("mount:" + gfile.get_uri()) if mount is not None else info.get_name()
        self.cell = (0, 0)
        self.x = self.y = 0
        self.pix = None

    @property
    def uri(self):
        return self.gfile.get_uri()

    def icon(self):
        if self.pix is None:
            if self.mount is not None:
                self.pix = files.theme_pixbuf(self.mount.get_icon(), ICON, "drive-harddisk")
            else:
                self.pix = files.file_pixbuf(self.gfile, self.info, ICON)
        return self.pix


class DesktopIcons:
    def __init__(self, win, shell):
        self.win = win
        self.shell = shell
        path = files.desktop_dir()
        os.makedirs(path, exist_ok=True)
        self.dir = Gio.File.new_for_path(path)
        self.items = []
        self.selected = set()
        self.positions = self._load_positions()
        self.press = None            # (item, x, y) of a button-1 press
        self.band = None             # rubber band (x0, y0, x1, y1)
        self._reload_id = None

        win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                       Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        win.set_can_focus(True)
        win.connect("motion-notify-event", self._motion)
        win.connect("button-release-event", self._release)
        win.connect("key-press-event", self._key)
        win.drag_dest_set(Gtk.DestDefaults.ALL, URI_TARGET,
                          Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        win.connect("drag-data-received", self._drop)
        win.connect("drag-data-get", self._drag_get)
        win.connect("drag-begin", self._drag_begin)

        self._mon = self.dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._mon.connect("changed", lambda *_: self.queue_reload())
        self.volumes = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed"):
            self.volumes.connect(sig, lambda *_: self.queue_reload())
        self.reload()

    # -- data ---------------------------------------------------------------------------
    def _load_positions(self):
        try:
            with open(POS_FILE) as f:
                return {k: tuple(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            return {}

    def _save_positions(self):
        try:
            os.makedirs(os.path.dirname(POS_FILE), exist_ok=True)
            with open(POS_FILE + ".tmp", "w") as f:
                json.dump(self.positions, f)
            os.replace(POS_FILE + ".tmp", POS_FILE)
        except OSError:
            pass

    def queue_reload(self):
        if self._reload_id is None:
            self._reload_id = GLib.timeout_add(250, self.reload)

    def reload(self):
        self._reload_id = None
        items = []
        for m in self.volumes.get_mounts():
            if m.can_unmount() or m.can_eject():
                items.append(Item(m.get_root(), mount=m))
        try:
            infos = list(self.dir.enumerate_children(files.ATTRS, Gio.FileQueryInfoFlags.NONE, None))
        except GLib.Error:
            infos = []
        infos = [i for i in infos if not i.get_is_hidden() and not i.get_is_backup()]
        infos.sort(key=lambda i: (i.get_file_type() != Gio.FileType.DIRECTORY, i.get_display_name().lower()))
        for info in infos:
            items.append(Item(self.dir.get_child(info.get_name()), info))
        keys = {it.key for it in items}
        self.selected = {k for k in self.selected if k in keys}
        self.items = items
        self.layout()
        return False

    # -- layout ---------------------------------------------------------------------------------
    def _area(self):
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        wa = mon.get_workarea()
        geo = mon.get_geometry()
        top = max(wa.y, geo.y + 28)            # below the top bar
        wa.height -= top - wa.y
        wa.y = top
        return wa

    def _rows(self):
        wa = self._area()
        return max(1, (wa.height - 2 * MARGIN) // CELL_H), max(1, (wa.width - 2 * MARGIN) // CELL_W)

    def _cell_xy(self, col, row):
        wa = self._area()
        return wa.x + wa.width - MARGIN - (col + 1) * CELL_W, wa.y + MARGIN + row * CELL_H

    def _cell_at(self, x, y):
        wa = self._area()
        col = int((wa.x + wa.width - MARGIN - x) // CELL_W)
        row = int((y - wa.y - MARGIN) // CELL_H)
        rows, cols = self._rows()
        return max(0, min(cols - 1, col)), max(0, min(rows - 1, row))

    def layout(self):
        rows, cols = self._rows()
        taken = set()
        placed = []
        for it in self.items:                      # remembered places first
            pos = self.positions.get(it.key)
            if pos and pos[0] < cols and pos[1] < rows and pos not in taken:
                it.cell = pos
                taken.add(pos)
                placed.append(it)
        free = ((c, r) for c in range(cols) for r in range(rows))
        for it in self.items:
            if it in placed:
                continue
            for cell in free:
                if cell not in taken:
                    it.cell = cell
                    taken.add(cell)
                    break
        for it in self.items:
            it.x, it.y = self._cell_xy(*it.cell)
        self.win.queue_draw()

    def clean_up(self):
        self.positions = {}
        self._save_positions()
        self.layout()

    # -- drawing --------------------------------------------------------------------------------
    def _label_layout(self, cr, text):
        lay = PangoCairo.create_layout(cr)
        lay.set_font_description(Pango.FontDescription("Inter, Sans 9.5"))
        lay.set_width((CELL_W - 10) * Pango.SCALE)
        lay.set_alignment(Pango.Alignment.CENTER)
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        lay.set_height(-2)
        lay.set_text(text, -1)
        return lay

    def draw(self, cr):
        for it in self.items:
            sel = it.key in self.selected
            ix = it.x + (CELL_W - ICON) / 2.0
            iy = it.y + 4
            if sel:
                from .art import rounded_rect
                rounded_rect(cr, ix - 6, iy - 4, ICON + 12, ICON + 8, 8)
                cr.set_source_rgba(0, 0, 0, 0.28)
                cr.fill()
            pb = it.icon()
            if pb is not None:
                Gdk.cairo_set_source_pixbuf(cr, pb, ix + (ICON - pb.get_width()) / 2.0,
                                            iy + (ICON - pb.get_height()) / 2.0)
                cr.paint()
            lay = self._label_layout(cr, it.name)
            ink, logical = lay.get_pixel_extents()
            ty = iy + ICON + 6
            if sel:
                from .art import rounded_rect
                lx = it.x + 5 + logical.x - 4
                rounded_rect(cr, lx, ty + logical.y - 1, logical.width + 8, logical.height + 2, 5)
                cr.set_source_rgba(0.18, 0.47, 0.95, 0.95)
                cr.fill()
            else:   # soft shadow so names stay readable on any wallpaper
                cr.move_to(it.x + 5, ty + 1)
                cr.set_source_rgba(0, 0, 0, 0.75)
                PangoCairo.show_layout(cr, lay)
            cr.move_to(it.x + 5, ty)
            cr.set_source_rgba(1, 1, 1, 1)
            PangoCairo.show_layout(cr, lay)
        if self.band:
            x0, y0, x1, y1 = self.band
            cr.rectangle(min(x0, x1) + 0.5, min(y0, y1) + 0.5, abs(x1 - x0), abs(y1 - y0))
            cr.set_source_rgba(0.6, 0.75, 1, 0.25)
            cr.fill_preserve()
            cr.set_source_rgba(0.8, 0.9, 1, 0.8)
            cr.set_line_width(1)
            cr.stroke()

    def _hit(self, x, y):
        for it in reversed(self.items):
            ix = it.x + (CELL_W - ICON) / 2.0
            if ix - 6 <= x <= ix + ICON + 6 and it.y <= y <= it.y + CELL_H - 4:
                return it
        return None

    # -- events -----------------------------------------------------------------------------------
    def handle_press(self, ev):
        """Called by the desktop window; True if an icon (or the desktop menu) handled it."""
        # the desktop takes the keyboard when clicked (Delete, F2, Ctrl+C...)
        gw = self.win.get_window()
        if gw is not None:
            gw.focus(ev.time)
        self.win.grab_focus()
        it = self._hit(ev.x, ev.y)
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        if ev.type == Gdk.EventType._2BUTTON_PRESS and ev.button == 1 and it is not None:
            self.open_items([it])
            return True
        if ev.button == 1:
            if it is None:
                if not ctrl:
                    self.selected.clear()
                self.band = (ev.x, ev.y, ev.x, ev.y)
            elif ctrl:
                self.selected.symmetric_difference_update({it.key})
            elif it.key not in self.selected:
                self.selected = {it.key}
            self.press = (it, ev.x, ev.y) if it is not None else None
            self.win.queue_draw()
            return True
        if ev.button == 3:
            if it is None:
                self.selected.clear()
                self.win.queue_draw()
                self._desktop_menu(ev)
            else:
                if it.key not in self.selected:
                    self.selected = {it.key}
                    self.win.queue_draw()
                self._item_menu(ev)
            return True
        return False

    def _motion(self, w, ev):
        if self.band is not None:
            x0, y0 = self.band[:2]
            self.band = (x0, y0, ev.x, ev.y)
            lx, hx, ly, hy = min(x0, ev.x), max(x0, ev.x), min(y0, ev.y), max(y0, ev.y)
            self.selected = {it.key for it in self.items
                             if it.x + CELL_W > lx and it.x < hx and it.y + CELL_H > ly and it.y < hy}
            self.win.queue_draw()
            return True
        if self.press is not None and ev.state & Gdk.ModifierType.BUTTON1_MASK:
            it, x0, y0 = self.press
            if self.win.drag_check_threshold(int(x0), int(y0), int(ev.x), int(ev.y)):
                self._drag_origin = (it, x0, y0)
                self.press = None
                targets = Gtk.TargetList.new(URI_TARGET)
                self.win.drag_begin_with_coordinates(
                    targets, Gdk.DragAction.MOVE | Gdk.DragAction.COPY | Gdk.DragAction.LINK,
                    1, ev, int(ev.x), int(ev.y))
            return True
        return False

    def _release(self, w, ev):
        if self.band is not None:
            self.band = None
            self.win.queue_draw()
        self.press = None
        return False

    def _key(self, w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        k = Gdk.keyval_to_lower(ev.keyval)
        sel = self.selection()
        if k in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) or (ctrl and k == Gdk.KEY_BackSpace):
            self.trash_items(sel)
        elif k in (Gdk.KEY_F2, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and len(sel) == 1:
            self.rename_item(sel[0])
        elif ctrl and k == Gdk.KEY_o:
            self.open_items(sel)
        elif ctrl and k == Gdk.KEY_a:
            self.selected = {it.key for it in self.items}
            self.win.queue_draw()
        elif ctrl and k in (Gdk.KEY_c, Gdk.KEY_x) and sel:
            files.FileClipboard.get().set([it.gfile for it in sel if it.mount is None], cut=k == Gdk.KEY_x)
        elif ctrl and k == Gdk.KEY_v:
            files.FileClipboard.get().paste_into(self.dir, self._report)
        elif ctrl and ev.state & Gdk.ModifierType.SHIFT_MASK and k == Gdk.KEY_n:
            self.new_folder()
        else:
            return False
        return True

    # -- drag and drop -----------------------------------------------------------------------------
    def _drag_begin(self, w, ctx):
        it = getattr(self, "_drag_origin", (None,))[0]
        if it is not None and it.icon() is not None:
            Gtk.drag_set_icon_pixbuf(ctx, it.icon(), ICON // 2, ICON // 2)

    def _drag_get(self, w, ctx, data, info, time_):
        data.set_uris([it.uri for it in self.selection()])

    def _drop(self, w, ctx, x, y, data, info, time_):
        uris = data.get_uris() or []
        if Gtk.drag_get_source_widget(ctx) is self.win:
            # rearranging icons on the desktop
            origin = getattr(self, "_drag_origin", None)
            if origin is None:
                return
            it0, x0, y0 = origin
            c0 = it0.cell
            c1 = self._cell_at(x, y)
            dc, dr = c1[0] - c0[0], c1[1] - c0[1]
            rows, cols = self._rows()
            moving = self.selection()
            occupied = {it.cell for it in self.items if it.key not in self.selected}
            for it in moving:
                c = (max(0, min(cols - 1, it.cell[0] + dc)), max(0, min(rows - 1, it.cell[1] + dr)))
                while c in occupied:                           # next free place below
                    c = (c[0], c[1] + 1) if c[1] + 1 < rows else (c[0] + 1, 0)
                occupied.add(c)
                self.positions[it.key] = c
            self._save_positions()
            self.layout()
            return
        sources = [Gio.File.new_for_uri(u) for u in uris]
        if not sources:
            return
        cell = self._cell_at(x, y)
        move = ctx.get_selected_action() == Gdk.DragAction.MOVE and files.same_filesystem(sources[0], self.dir)
        for i, s in enumerate(sources):           # dropped files land where they were dropped
            self.positions[s.get_basename()] = (cell[0], min(cell[1] + i, self._rows()[0] - 1))
        self._save_positions()
        files.transfer(sources, self.dir, move=move, on_done=self._report)

    # -- actions ---------------------------------------------------------------------------------------
    def selection(self):
        return [it for it in self.items if it.key in self.selected]

    def open_items(self, items):
        for it in items:
            if it.mount is not None:
                files.open_folder(it.gfile)
            else:
                files.open_file(it.gfile, it.info)

    def trash_items(self, items):
        targets = [it.gfile for it in items if it.mount is None]
        failed = files.trash(targets)
        self._report(failed)

    def new_folder(self):
        try:
            f = files.new_folder(self.dir)
        except GLib.Error as e:
            self._report([(self.dir, e.message)])
            return
        self.selected = {f.get_basename()}
        self.reload()
        for it in self.items:
            if it.key == f.get_basename():
                self.rename_item(it)
                break

    def rename_item(self, it):
        if it.mount is not None:
            return
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_wmclass("slope-desktop-rename", "Slope")
        win.set_decorated(False)
        win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        win.set_skip_taskbar_hint(True)
        win.set_keep_above(True)
        entry = Gtk.Entry()
        entry.set_text(it.name)
        entry.set_width_chars(16)
        entry.set_alignment(0.5)
        win.add(entry)
        ox, oy = self.win.get_window().get_origin()[1:]
        win.move(int(ox + it.x - 6), int(oy + it.y + ICON + 6))
        done = {"v": False}

        def commit(*_):
            if done["v"]:
                return
            done["v"] = True
            text = entry.get_text()
            win.destroy()
            try:
                new = files.rename(it.gfile, text)
                if new.get_basename() != it.gfile.get_basename() and it.key in self.positions:
                    self.positions[new.get_basename()] = self.positions.pop(it.key)
                    self._save_positions()
                self.selected = {new.get_basename()}
            except GLib.Error as e:
                self._report([(it.gfile, e.message)])
            self.queue_reload()

        def key(w, ev):
            if ev.keyval == Gdk.KEY_Escape:
                done["v"] = True
                win.destroy()
                return True
            return False
        entry.connect("activate", commit)
        entry.connect("key-press-event", key)
        win.connect("focus-out-event", commit)
        win.show_all()
        win.present()
        entry.grab_focus()
        stem, ext = os.path.splitext(it.name)
        is_dir = it.info.get_file_type() == Gio.FileType.DIRECTORY
        entry.select_region(0, len(it.name if is_dir or not ext else stem))

    def _report(self, errors):
        if not errors:
            return False
        dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK,
                                text="Some items could not be changed")
        dlg.set_wmclass("slope-dialog", "Slope")
        dlg.format_secondary_text("\n".join("%s: %s" % (f.get_basename(), msg) for f, msg in errors[:6]))
        dlg.run()
        dlg.destroy()
        return False

    # -- menus -------------------------------------------------------------------------------------------
    @staticmethod
    def _mi(label, cb, sensitive=True):
        mi = Gtk.MenuItem(label=label)
        mi.set_sensitive(sensitive)
        mi.connect("activate", lambda *_: cb())
        return mi

    def _popup(self, menu, ev):
        menu.show_all()
        menu.attach_to_widget(self.win, None)
        menu.popup_at_pointer(ev)

    def _desktop_menu(self, ev):
        menu = Gtk.Menu()
        for mi in (
            self._mi("New Folder", self.new_folder),
            self._mi("Paste", lambda: files.FileClipboard.get().paste_into(self.dir, self._report)),
            Gtk.SeparatorMenuItem(),
            self._mi("Clean Up", self.clean_up),
            self._mi("Show Desktop in Files", lambda: files.open_folder(self.dir)),
            self._mi("Open Terminal", lambda: open_app("xfce4-terminal.desktop")),
            Gtk.SeparatorMenuItem(),
            self._mi("Change Wallpaper…", lambda: spawn(["slope-settings", "--page", "appearance"])),
            self._mi("Display Settings…", lambda: spawn(["slope-settings", "--page", "displays"])),
        ):
            menu.append(mi)
        self._popup(menu, ev)

    def _item_menu(self, ev):
        sel = self.selection()
        if not sel:
            return
        menu = Gtk.Menu()
        if any(it.mount is not None for it in sel):
            it = sel[0]
            menu.append(self._mi("Open", lambda: self.open_items([it])))
            m = it.mount
            if m.can_eject():
                menu.append(self._mi("Eject “%s”" % it.name, lambda: m.eject_with_operation(
                    Gio.MountUnmountFlags.NONE, None, None, None, None)))
            elif m.can_unmount():
                menu.append(self._mi("Eject “%s”" % it.name, lambda: m.unmount_with_operation(
                    Gio.MountUnmountFlags.NONE, None, None, None, None)))
            self._popup(menu, ev)
            return
        menu.append(self._mi("Open", lambda: self.open_items(sel)))
        if len(sel) == 1 and sel[0].info.get_file_type() != Gio.FileType.DIRECTORY:
            it = sel[0]
            sub = Gtk.Menu()
            for app in files.apps_for(it.info)[:10]:
                sub.append(self._mi(app.get_name(), lambda a=app: a.launch(
                    [it.gfile], Gdk.Display.get_default().get_app_launch_context())))
            sub.append(Gtk.SeparatorMenuItem())
            sub.append(self._mi("Other…", lambda: files.open_with_dialog(it.gfile, it.info)))
            ow = Gtk.MenuItem(label="Open With")
            ow.set_submenu(sub)
            menu.append(ow)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._mi("Rename…", lambda: self.rename_item(sel[0]), len(sel) == 1))
        menu.append(self._mi("Copy", lambda: files.FileClipboard.get().set([it.gfile for it in sel])))
        menu.append(self._mi("Move to Trash", lambda: self.trash_items(sel)))
        dirs = [it.gfile for it in sel if it.info.get_file_type() == Gio.FileType.DIRECTORY]
        if dirs:
            menu.append(Gtk.SeparatorMenuItem())
            sub = Gtk.Menu()
            for mi in files.color_menu_items(dirs, self.reload):
                sub.append(mi)
            cm = Gtk.MenuItem(label="Folder Color")
            cm.set_submenu(sub)
            menu.append(cm)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._mi("Show in Files", lambda: files.open_folder(self.dir)))
        self._popup(menu, ev)
