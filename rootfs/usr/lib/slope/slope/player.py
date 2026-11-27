"""slope-player: Slope's media player (GStreamer: plays almost any video or audio).

A dark window with the picture, and QuickTime-style glass controls that
float over it: play / pause, 10 s back and forward, a position slider with
elapsed and remaining time, volume and full screen.  The controls fade away
while a video plays and come back when the mouse moves.  Music shows its
album art (or a glossy note) with the title and artist.

Keys: Space play/pause, Left/Right 5 s, Up/Down volume, F full screen,
Esc leave full screen, Cmd+O open.  Drop files on the window to play them.
"""
import os
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gst, Gtk  # noqa: E402

APP_ID = "org.slope.Player"
DESKTOP = "/usr/share/applications/slope-player.desktop"
HIDE_AFTER = 2.5          # seconds without mouse movement

CSS = b"""
window.slope-player, window.slope-player.background {
  background-color: #0d0d0f;
  background-image: linear-gradient(to bottom, #232327, #0b0b0d);
}
.slope-player .hud {
  border-radius: 14px;
  padding: 8px 14px;
  background-color: rgba(28, 28, 32, 0.78);
  background-image: linear-gradient(to bottom, rgba(255,255,255,0.16), rgba(255,255,255,0.02) 45%, rgba(0,0,0,0.0));
  border: 1px solid rgba(255, 255, 255, 0.16);
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.45);
}
.slope-player .hud label { color: rgba(255, 255, 255, 0.92); font-feature-settings: "tnum"; font-size: 9.5pt; }
.slope-player .hud button {
  background: none; border: none; box-shadow: none; padding: 4px; min-width: 0; min-height: 0;
  color: #ffffff; -gtk-icon-shadow: 0 1px 2px rgba(0,0,0,0.6);
}
.slope-player .hud button:hover { background-color: rgba(255, 255, 255, 0.12); border-radius: 999px; }
.slope-player .hud scale trough { min-height: 4px; background-image: none; background-color: rgba(255,255,255,0.22);
  border: none; box-shadow: none; }
.slope-player .hud scale highlight { min-height: 4px; background-image: none; background-color: #ffffff; border: none; }
.slope-player .hud scale slider { min-width: 12px; min-height: 12px; margin: -5px; background-image: none;
  background-color: #ffffff; border: none; box-shadow: 0 1px 3px rgba(0,0,0,0.5); }
.slope-player image, .slope-player stack, .slope-player box { background: none; }
.slope-player .music-title { color: #ffffff; font-size: 17pt; font-weight: bold; }
.slope-player .music-artist { color: rgba(255,255,255,0.7); font-size: 12pt; }
.slope-player .hint { color: rgba(255,255,255,0.55); font-size: 12pt; }
"""


def fmt(ns):
    if ns is None or ns < 0:
        return "--:--"
    s = int(ns // Gst.SECOND)
    h, m, s = s // 3600, (s // 60) % 60, s % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def note_art(size):
    """A glossy blue gel tile with a music note, for songs without cover art."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    r = size * 0.18
    w = size
    cr.new_sub_path()
    cr.arc(w - r, r, r, -1.5708, 0)
    cr.arc(w - r, w - r, r, 0, 1.5708)
    cr.arc(r, w - r, r, 1.5708, 3.1416)
    cr.arc(r, r, r, 3.1416, 4.7124)
    cr.close_path()
    g = cairo.LinearGradient(0, 0, 0, w)
    g.add_color_stop_rgb(0, 0.45, 0.72, 1.0)
    g.add_color_stop_rgb(1, 0.10, 0.36, 0.86)
    cr.set_source(g)
    cr.fill_preserve()
    cr.clip()                       # the gloss stays inside the rounded tile
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.arc(w * 0.38, w * 0.68, w * 0.1, 0, 6.2832)
    cr.arc(w * 0.66, w * 0.62, w * 0.1, 0, 6.2832)
    cr.fill()
    cr.set_line_width(w * 0.045)
    cr.move_to(w * 0.47, w * 0.68)
    cr.line_to(w * 0.47, w * 0.28)
    cr.line_to(w * 0.75, w * 0.22)
    cr.line_to(w * 0.75, w * 0.62)
    cr.stroke()
    hl = cairo.LinearGradient(0, 0, 0, w * 0.5)
    hl.add_color_stop_rgba(0, 1, 1, 1, 0.45)
    hl.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.rectangle(0, 0, w, w * 0.5)
    cr.set_source(hl)
    cr.fill()
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size)


class PlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Slope Player")
        self.app = app
        self.set_default_size(860, 520)
        self.set_icon_name("multimedia-video-player")
        self.get_style_context().add_class("slope-player")
        self.playlist, self.index = [], -1
        self.duration = Gst.CLOCK_TIME_NONE
        self._seeking = False
        self._last_motion = GLib.get_monotonic_time()
        self.is_video = False

        self.play = Gst.ElementFactory.make("playbin", "play")
        sink = Gst.ElementFactory.make("gtksink", "sink")
        self.video = sink.props.widget
        self.play.set_property("video-sink", sink)
        bus = self.play.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._bus)

        overlay = Gtk.Overlay()
        self.add(overlay)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.video, "video")
        # music: cover art and the song's name
        music = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        music.set_valign(Gtk.Align.CENTER)
        self.art = Gtk.Image()
        music.pack_start(self.art, False, False, 6)
        self.title_lbl = Gtk.Label()
        self.title_lbl.get_style_context().add_class("music-title")
        self.artist_lbl = Gtk.Label()
        self.artist_lbl.get_style_context().add_class("music-artist")
        music.pack_start(self.title_lbl, False, False, 0)
        music.pack_start(self.artist_lbl, False, False, 0)
        self.stack.add_named(music, "music")
        hint = Gtk.Label(label="Drop a video or a song here, or choose File › Open…")
        hint.get_style_context().add_class("hint")
        self.stack.add_named(hint, "empty")
        overlay.add(self.stack)

        # floating glass controls
        self.hud = Gtk.Box(spacing=8)
        self.hud.get_style_context().add_class("hud")
        self.hud.set_halign(Gtk.Align.CENTER)
        self.hud.set_valign(Gtk.Align.END)
        self.hud.set_margin_bottom(22)
        self.hud.set_size_request(min(560, 860 - 60), -1)

        def button(icon, tip, cb):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            b.set_tooltip_text(tip)
            b.set_can_focus(False)
            b.connect("clicked", lambda *_: cb())
            return b
        self.hud.pack_start(button("media-seek-backward-symbolic", "Back 10 seconds", lambda: self.seek_by(-10)),
                            False, False, 0)
        self.play_btn = button("media-playback-start-symbolic", "Play", self.toggle)
        self.hud.pack_start(self.play_btn, False, False, 0)
        self.hud.pack_start(button("media-seek-forward-symbolic", "Forward 10 seconds", lambda: self.seek_by(10)),
                            False, False, 0)
        self.pos_lbl = Gtk.Label(label="0:00")
        self.hud.pack_start(self.pos_lbl, False, False, 4)
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.001)
        self.slider.set_draw_value(False)
        self.slider.set_can_focus(False)
        self.slider.connect("button-press-event", lambda *_: setattr(self, "_seeking", True) or False)
        self.slider.connect("button-release-event", self._slider_release)
        self.hud.pack_start(self.slider, True, True, 0)
        self.left_lbl = Gtk.Label(label="--:--")
        self.hud.pack_start(self.left_lbl, False, False, 4)
        self.vol = Gtk.VolumeButton()
        self.vol.set_value(1.0)
        self.vol.set_can_focus(False)
        self.vol.connect("value-changed", lambda w, v: self.play.set_property("volume", v))
        self.hud.pack_start(self.vol, False, False, 0)
        self.hud.pack_start(button("view-fullscreen-symbolic", "Full Screen", self.toggle_fullscreen), False, False, 0)
        self.hud_reveal = Gtk.Revealer()
        self.hud_reveal.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.hud_reveal.set_transition_duration(250)
        self.hud_reveal.add(self.hud)
        self.hud_reveal.set_reveal_child(True)
        self.hud_reveal.set_halign(Gtk.Align.CENTER)
        self.hud_reveal.set_valign(Gtk.Align.END)
        overlay.add_overlay(self.hud_reveal)

        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("motion-notify-event", self._motion)
        self.connect("key-press-event", self._key)
        self.connect("destroy", lambda *_: self.play.set_state(Gst.State.NULL))
        self.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._dropped)
        for name, cb in (("open", lambda *_: self.open_dialog()), ("toggle", lambda *_: self.toggle()),
                         ("forward", lambda *_: self.seek_by(10)), ("back", lambda *_: self.seek_by(-10)),
                         ("next", lambda *_: self.next(1)), ("previous", lambda *_: self.next(-1)),
                         ("fullscreen", lambda *_: self.toggle_fullscreen()),
                         ("close", lambda *_: self.close())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self.stack.show_all()
        self.stack.set_visible_child_name("empty")
        self.fullscreened = False
        self.connect("window-state-event", self._state)
        GLib.timeout_add(200, self._tick)
        self.show_all()

    # -- playing -------------------------------------------------------------------
    def load(self, files):
        self.playlist = [f for f in files]
        self.index = -1
        self.next(1)

    def next(self, step):
        if not self.playlist:
            return
        self.index = max(0, min(len(self.playlist) - 1, self.index + step))
        f = self.playlist[self.index]
        self.play.set_state(Gst.State.NULL)
        self.play.set_property("uri", f.get_uri())
        self.duration = Gst.CLOCK_TIME_NONE
        self.is_video = False
        name = f.get_basename() or "Slope Player"
        self.set_title(name)
        self.title_lbl.set_text(os.path.splitext(name)[0])
        self.artist_lbl.set_text("")
        self.art.set_from_pixbuf(note_art(220))
        self.play.set_state(Gst.State.PLAYING)
        self.stack.set_visible_child_name("music")

    def toggle(self):
        _r, state, _p = self.play.get_state(0)
        if state == Gst.State.PLAYING:
            self.play.set_state(Gst.State.PAUSED)
        elif self.playlist:
            self.play.set_state(Gst.State.PLAYING)
        else:
            self.open_dialog()

    def seek_by(self, seconds):
        ok, pos = self.play.query_position(Gst.Format.TIME)
        if ok:
            self.seek_to(max(0, pos + seconds * Gst.SECOND))

    def seek_to(self, ns):
        self.play.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, ns)

    def _slider_release(self, *_):
        self._seeking = False
        if self.duration and self.duration != Gst.CLOCK_TIME_NONE:
            self.seek_to(int(self.slider.get_value() * self.duration))
        return False

    def _tick(self):
        if self.duration in (None, Gst.CLOCK_TIME_NONE) or self.duration <= 0:
            ok, d = self.play.query_duration(Gst.Format.TIME)
            if ok:
                self.duration = d
        ok, pos = self.play.query_position(Gst.Format.TIME)
        if ok:
            self.pos_lbl.set_text(fmt(pos))
            if self.duration and self.duration not in (Gst.CLOCK_TIME_NONE,) and self.duration > 0:
                self.left_lbl.set_text("-" + fmt(self.duration - pos))
                if not self._seeking:
                    self.slider.set_value(pos / self.duration)
        # the controls fade away while a video plays
        _r, state, _p = self.play.get_state(0)
        idle = (GLib.get_monotonic_time() - self._last_motion) / 1e6
        show = not (self.is_video and state == Gst.State.PLAYING and idle > HIDE_AFTER)
        if show != self.hud_reveal.get_reveal_child():
            self.hud_reveal.set_reveal_child(show)
            gw = self.get_window()
            if gw is not None:
                gw.set_cursor(None if show else Gdk.Cursor.new_from_name(gw.get_display(), "none"))
        return True

    def _bus(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.EOS:
            if self.index < len(self.playlist) - 1:
                self.next(1)
            else:
                self.play.set_state(Gst.State.PAUSED)
                self.seek_to(0)
        elif t == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self.play.set_state(Gst.State.NULL)
            self.stack.set_visible_child_name("music")
            self.title_lbl.set_text("This file can't be played")
            self.artist_lbl.set_text(err.message)
        elif t == Gst.MessageType.STATE_CHANGED and msg.src == self.play:
            _old, new, _pend = msg.parse_state_changed()
            playing = new == Gst.State.PLAYING
            img = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic" if playing else
                                               "media-playback-start-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
            self.play_btn.set_image(img)
            self.play_btn.set_tooltip_text("Pause" if playing else "Play")
            if new == Gst.State.PAUSED or playing:
                nvideo = self.play.get_property("n-video")
                self.is_video = nvideo > 0
                self.stack.set_visible_child_name("video" if self.is_video else "music")
        elif t == Gst.MessageType.TAG:
            tags = msg.parse_tag()
            ok, title = tags.get_string(Gst.TAG_TITLE)
            if ok and title:
                self.title_lbl.set_text(title)
                if not self.is_video:          # a video keeps its file name
                    self.set_title(title)
            ok, artist = tags.get_string(Gst.TAG_ARTIST)
            if ok and artist:
                self.artist_lbl.set_text(artist)
            ok, sample = tags.get_sample(Gst.TAG_IMAGE)
            if ok and sample:
                self._cover(sample)
        elif t == Gst.MessageType.DURATION_CHANGED:
            self.duration = Gst.CLOCK_TIME_NONE

    def _cover(self, sample):
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(bytes(info.data))
            loader.close()
            pb = loader.get_pixbuf()
            if pb:
                s = 260.0 / max(pb.get_width(), pb.get_height())
                self.art.set_from_pixbuf(pb.scale_simple(int(pb.get_width() * s), int(pb.get_height() * s),
                                                         GdkPixbuf.InterpType.BILINEAR))
        except GLib.Error:
            pass
        finally:
            buf.unmap(info)

    # -- window ------------------------------------------------------------------------
    def _motion(self, *_):
        self._last_motion = GLib.get_monotonic_time()
        return False

    def _key(self, w, ev):
        k = ev.keyval
        if k == Gdk.KEY_space:
            self.toggle()
        elif k == Gdk.KEY_Left:
            self.seek_by(-5)
        elif k == Gdk.KEY_Right:
            self.seek_by(5)
        elif k in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.vol.set_value(max(0, min(1, self.vol.get_value() + (0.05 if k == Gdk.KEY_Up else -0.05))))
        elif k in (Gdk.KEY_f, Gdk.KEY_F):
            self.toggle_fullscreen()
        elif k == Gdk.KEY_Escape and self.fullscreened:
            self.unfullscreen()
        else:
            return False
        self._motion()
        return True

    def _state(self, w, ev):
        self.fullscreened = bool(ev.new_window_state & Gdk.WindowState.FULLSCREEN)
        return False

    def toggle_fullscreen(self):
        if self.fullscreened:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _dropped(self, w, ctx, x, y, data, info, time):
        files = [Gio.File.new_for_uri(u) for u in data.get_uris()]
        if files:
            self.load(files)

    def open_dialog(self):
        dlg = Gtk.FileChooserNative.new("Open", self, Gtk.FileChooserAction.OPEN, "Open", "Cancel")
        dlg.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name("Videos and music")
        flt.add_mime_type("video/*")
        flt.add_mime_type("audio/*")
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            self.load(dlg.get_files())


class PlayerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gst.init(None)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb in (("new-window", lambda *_: self.window()), ("quit", lambda *_: self.quit())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)

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
        bar.append_submenu("File", menu(section(("New Window", "app.new-window"), ("Open…", "win.open")),
                                        section(("Close", "win.close"))))
        bar.append_submenu("View", menu(section(("Enter Full Screen", "win.fullscreen"))))
        bar.append_submenu("Playback", menu(section(("Play / Pause", "win.toggle")),
                                            section(("Back 10 Seconds", "win.back"),
                                                    ("Forward 10 Seconds", "win.forward")),
                                            section(("Previous", "win.previous"), ("Next", "win.next"))))
        self.set_menubar(bar)
        for action, keys in (("win.open", ["<Primary>o"]), ("win.close", ["<Primary>w"]),
                             ("app.new-window", ["<Primary>n"]), ("win.fullscreen", ["<Primary><Super>f"]),
                             ("win.previous", ["<Primary>Left"]), ("win.next", ["<Primary>Right"]),
                             ("app.quit", ["<Primary>q"])):
            self.set_accels_for_action(action, keys)

    def window(self):
        w = PlayerWindow(self)
        w.present()
        return w

    def do_activate(self):
        wins = self.get_windows()
        (wins[0] if wins else self.window()).present()

    def do_open(self, files, n, hint):
        self.window().load(files)


def main(argv=None):
    GLib.set_prgname("slope-player")
    GLib.set_application_name("Slope Player")
    return PlayerApp().run(sys.argv if argv is None else argv)
