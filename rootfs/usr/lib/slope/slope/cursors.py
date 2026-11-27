"""Slope's cursor theme, drawn with cairo and written as Xcursor files.

    python3 -m slope.cursors /usr/share/icons/Slope-Cursors

Classic Mac OS X spirit, original drawings: crisp black arrows with a white
outline and a soft shadow, a white pointing hand, an Aqua-blue spinning gel
for "busy".  Every cursor comes in 24, 32, 48, 64 and 96 pixels, so it stays
sharp on Retina screens.  The Xcursor files are written directly (no
xcursorgen needed).
"""
import math
import os
import struct
import sys

import cairo

SIZES = (24, 32, 48, 64, 96)
BLUE = (0.18, 0.53, 0.95)


# -- drawing helpers (in 24-unit coordinates) --------------------------------
def _shadow(cr, path, u):
    """A soft drop shadow: the shape drawn a few times, offset and faint."""
    for i, a in ((2.2, 0.06), (1.6, 0.08), (1.0, 0.10)):
        cr.save()
        cr.translate(0.5 * u, 1.1 * u)
        path(cr)
        cr.set_line_width(i * u)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(0, 0, 0, a)
        cr.stroke_preserve()
        cr.fill()
        cr.restore()


def _outlined(cr, path, u, fill=(0, 0, 0), outline=(1, 1, 1), width=1.7, shadow=True):
    if shadow:
        _shadow(cr, path, u)
    path(cr)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(width * u)
    cr.set_source_rgb(*outline)
    cr.stroke_preserve()
    cr.set_source_rgb(*fill)
    cr.fill()


def _poly(points, u, close=True):
    def p(cr):
        cr.new_path()
        cr.move_to(points[0][0] * u, points[0][1] * u)
        for x, y in points[1:]:
            cr.line_to(x * u, y * u)
        if close:
            cr.close_path()
    return p


ARROW = [(4, 2.5), (4, 19), (7.9, 15.4), (10.6, 21.4), (13.3, 20.2), (10.7, 14.4), (15.8, 14.4)]


def arrow(cr, u):
    _outlined(cr, _poly(ARROW, u), u)


def ibeam(cr, u):
    def p(cr):
        cr.new_path()
        # serifs and stem as one outline
        pts = [(8.5, 4), (11, 4), (12, 5), (13, 4), (15.5, 4), (15.5, 5.4), (13.6, 5.4), (12.8, 6.2),
               (12.8, 17.8), (13.6, 18.6), (15.5, 18.6), (15.5, 20), (13, 20), (12, 19), (11, 20), (8.5, 20),
               (8.5, 18.6), (10.4, 18.6), (11.2, 17.8), (11.2, 6.2), (10.4, 5.4), (8.5, 5.4)]
        cr.move_to(pts[0][0] * u, pts[0][1] * u)
        for x, y in pts[1:]:
            cr.line_to(x * u, y * u)
        cr.close_path()
    _outlined(cr, p, u, width=1.5)


def _rr(cr, x, y, w, h, r, u):
    x, y, w, h, r = x * u, y * u, w * u, h * u, r * u
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _hand(cr, u, parts):
    """A white hand from rounded rectangles: the outline of their union."""
    def shapes(cr):
        cr.new_path()
        for x, y, w, h, r in parts:
            _rr(cr, x, y, w, h, r, u)
    _shadow(cr, shapes, u)
    shapes(cr)
    cr.set_line_width(2.4 * u)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgb(0.08, 0.08, 0.09)
    cr.stroke()
    shapes(cr)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    # finger creases
    cr.set_source_rgba(0, 0, 0, 0.55)
    cr.set_line_width(0.7 * u)
    return cr


def pointer(cr, u):
    _hand(cr, u, [(8.2, 2.2, 3.2, 12, 1.6),        # index finger
                  (11, 8.6, 2.9, 6.5, 1.4), (13.6, 9.3, 2.9, 6, 1.4), (16.2, 10.2, 2.7, 5.6, 1.35),
                  (8, 11.6, 11, 9.6, 3.2),         # palm
                  (4.4, 11.6, 5.6, 2.8, 1.4)])     # thumb
    for x in (11.05, 13.65, 16.25):
        cr.move_to(x * u, 11.6 * u)
        cr.line_to(x * u, 13.8 * u)
    cr.stroke()


def grab(cr, u):
    _hand(cr, u, [(5.8, 6.6, 2.9, 8, 1.45), (8.6, 4.6, 2.9, 9, 1.45), (11.4, 4.2, 2.9, 9.2, 1.45),
                  (14.2, 5.6, 2.8, 8.4, 1.4), (6.4, 11.2, 11, 9.4, 3.4), (16.4, 11, 3.6, 2.8, 1.4)])


def grabbing(cr, u):
    _hand(cr, u, [(6.4, 9.2, 2.9, 4.4, 1.45), (9.2, 8.6, 2.9, 4.8, 1.45), (12, 8.6, 2.9, 4.8, 1.45),
                  (14.8, 9.2, 2.8, 4.4, 1.4), (6.4, 11, 11, 9.4, 3.4), (16.6, 11.4, 3.2, 2.6, 1.3)])


def _double_arrow(cr, u, angle):
    """A black double-headed arrow with a white outline, rotated by angle."""
    pts = [(3, 12), (7.5, 7.5), (7.5, 10.6), (16.5, 10.6), (16.5, 7.5), (21, 12), (16.5, 16.5),
           (16.5, 13.4), (7.5, 13.4), (7.5, 16.5)]
    c, s = math.cos(angle), math.sin(angle)
    rot = [(12 + (x - 12) * c - (y - 12) * s, 12 + (x - 12) * s + (y - 12) * c) for x, y in pts]
    _outlined(cr, _poly(rot, u), u, width=1.6)


def ew(cr, u):
    _double_arrow(cr, u, 0)


def ns(cr, u):
    _double_arrow(cr, u, math.pi / 2)


def nwse(cr, u):
    _double_arrow(cr, u, math.pi / 4)


def nesw(cr, u):
    _double_arrow(cr, u, -math.pi / 4)


def _split(cr, u, angle):
    _double_arrow(cr, u, angle)
    c, s = math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2)
    bar = [(12 + 7 * c - 1.1 * math.cos(angle), 12 + 7 * s - 1.1 * math.sin(angle)),
           (12 + 7 * c + 1.1 * math.cos(angle), 12 + 7 * s + 1.1 * math.sin(angle)),
           (12 - 7 * c + 1.1 * math.cos(angle), 12 - 7 * s + 1.1 * math.sin(angle)),
           (12 - 7 * c - 1.1 * math.cos(angle), 12 - 7 * s - 1.1 * math.sin(angle))]
    _outlined(cr, _poly(bar, u), u, width=1.4, shadow=False)


def col_resize(cr, u):
    _split(cr, u, 0)


def row_resize(cr, u):
    _split(cr, u, math.pi / 2)


def move(cr, u):
    pts = []
    for k in range(4):
        a = k * math.pi / 2
        c, s = math.cos(a), math.sin(a)
        for x, y in ((1.3, 3.8), (1.3, 7.6), (4.0, 7.6), (0, 11.2), (-4.0, 7.6), (-1.3, 7.6), (-1.3, 3.8)):
            # shapes along +y, rotated
            pts.append((12 + x * c - y * s, 12 + x * s + y * c))
    # keep a nice star order: build from 4 arms joined at the centre square
    def p(cr):
        cr.new_path()
        for k in range(4):
            a = k * math.pi / 2
            c, s = math.cos(a), math.sin(a)
            arm = [(1.3, 1.3), (1.3, 7.0), (3.8, 7.0), (0, 11), (-3.8, 7.0), (-1.3, 7.0), (-1.3, 1.3)]
            for i, (x, y) in enumerate(arm):
                X, Y = (12 + x * c - y * s) * u, (12 + x * s + y * c) * u
                if k == 0 and i == 0:
                    cr.move_to(X, Y)
                else:
                    cr.line_to(X, Y)
        cr.close_path()
    _outlined(cr, p, u, width=1.6)


def crosshair(cr, u):
    def p(cr):
        cr.new_path()
        cr.move_to(12 * u, 3 * u)
        cr.line_to(12 * u, 21 * u)
        cr.move_to(3 * u, 12 * u)
        cr.line_to(21 * u, 12 * u)
    for w, col in ((3.2, (1, 1, 1)), (1.2, (0, 0, 0))):
        p(cr)
        cr.set_line_width(w * u)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgb(*col)
        cr.stroke()


def cell(cr, u):
    pts = [(10, 4), (14, 4), (14, 10), (20, 10), (20, 14), (14, 14), (14, 20), (10, 20), (10, 14), (4, 14),
           (4, 10), (10, 10)]
    _outlined(cr, _poly(pts, u), u, fill=(1, 1, 1), outline=(0, 0, 0), width=1.3)


def not_allowed(cr, u):
    def ring(cr):
        cr.new_path()
        cr.arc(12 * u, 12 * u, 7.6 * u, 0, 2 * math.pi)
    _shadow(cr, ring, u)
    for w, col in ((4.6, (1, 1, 1)), (2.6, (0.08, 0.08, 0.09))):
        ring(cr)
        cr.move_to((12 - 5.3) * u, (12 + 5.3) * u)
        cr.line_to((12 + 5.3) * u, (12 - 5.3) * u)
        cr.set_line_width(w * u)
        cr.set_source_rgb(*col)
        cr.stroke()


def _badge(cr, u, color, glyph):
    """A small round badge at the arrow's lower right (copy, alias, help...)."""
    cx, cy, r = 17.2, 17.6, 4.6
    cr.arc(cx * u, cy * u, (r + 0.9) * u, 0, 2 * math.pi)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    g = cairo.RadialGradient(cx * u, (cy - 2) * u, 0.5 * u, cx * u, cy * u, r * u)
    g.add_color_stop_rgb(0, *(min(1, c + 0.35) for c in color))
    g.add_color_stop_rgb(1, *color)
    cr.arc(cx * u, cy * u, r * u, 0, 2 * math.pi)
    cr.set_source(g)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(1.4 * u)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if glyph == "+":
        cr.move_to((cx - 2.3) * u, cy * u); cr.line_to((cx + 2.3) * u, cy * u)
        cr.move_to(cx * u, (cy - 2.3) * u); cr.line_to(cx * u, (cy + 2.3) * u)
        cr.stroke()
    elif glyph == "alias":
        cr.arc(cx * u, (cy + 1.4) * u, 2.4 * u, math.pi, 1.75 * math.pi)
        cr.stroke()
        cr.move_to((cx + 2.6) * u, (cy - 2.4) * u)
        cr.line_to((cx + 0.2) * u, (cy - 2.6) * u)
        cr.line_to((cx + 1.6) * u, (cy - 0.4) * u)
        cr.close_path()
        cr.fill()
    elif glyph == "?":
        cr.select_font_face("Inter", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(7 * u)
        ext = cr.text_extents("?")
        cr.move_to(cx * u - ext.width / 2 - ext.x_bearing, cy * u + ext.height / 2)
        cr.show_text("?")
    elif glyph == "menu":
        for dy in (-1.7, 0, 1.7):
            cr.move_to((cx - 2.2) * u, (cy + dy) * u)
            cr.line_to((cx + 2.2) * u, (cy + dy) * u)
        cr.set_line_width(1.0 * u)
        cr.stroke()


def copy(cr, u):
    arrow(cr, u)
    _badge(cr, u, (0.16, 0.7, 0.3), "+")


def alias(cr, u):
    arrow(cr, u)
    _badge(cr, u, (0.35, 0.37, 0.42), "alias")


def help_(cr, u):
    arrow(cr, u)
    _badge(cr, u, BLUE, "?")


def context_menu(cr, u):
    arrow(cr, u)
    _badge(cr, u, (0.35, 0.37, 0.42), "menu")


def _magnifier(cr, u, glyph):
    def lens(cr):
        cr.new_path()
        cr.arc(10 * u, 10 * u, 6 * u, 0, 2 * math.pi)
    _shadow(cr, lens, u)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for w, col in ((5.0, (1, 1, 1)), (3.0, (0.08, 0.08, 0.09))):
        cr.move_to(14.4 * u, 14.4 * u)
        cr.line_to(20 * u, 20 * u)
        cr.set_line_width(w * u)
        cr.set_source_rgb(*col)
        cr.stroke()
    lens(cr)
    cr.set_source_rgba(1, 1, 1, 0.92)
    cr.fill_preserve()
    cr.set_line_width(1.8 * u)
    cr.set_source_rgb(0.08, 0.08, 0.09)
    cr.stroke()
    cr.set_line_width(1.4 * u)
    cr.move_to(7.4 * u, 10 * u)
    cr.line_to(12.6 * u, 10 * u)
    if glyph == "+":
        cr.move_to(10 * u, 7.4 * u)
        cr.line_to(10 * u, 12.6 * u)
    cr.stroke()


def zoom_in(cr, u):
    _magnifier(cr, u, "+")


def zoom_out(cr, u):
    _magnifier(cr, u, "-")


def vertical_text(cr, u):
    cr.save()
    cr.translate(12 * u, 12 * u)
    cr.rotate(math.pi / 2)
    cr.translate(-12 * u, -12 * u)
    ibeam(cr, u)
    cr.restore()


def _spinner(cr, u, cx, cy, r, frame, frames):
    """An Aqua-blue gel ring with a bright sweep that turns frame by frame."""
    def ring(cr):
        cr.new_path()
        cr.arc(cx * u, cy * u, r * u, 0, 2 * math.pi)
    _shadow(cr, ring, u)
    ring(cr)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    segs = 12
    for i in range(segs):
        a0 = 2 * math.pi * i / segs - math.pi / 2
        a1 = a0 + 2 * math.pi / segs + 0.02
        k = ((i - frame * segs / frames) % segs) / segs        # 0 = head of the sweep
        t = 1 - k
        w = 0.3 + 0.7 * t ** 1.5                               # bright head, pale tail
        col = tuple(c * w + (1 - w) * 0.93 for c in BLUE)
        cr.move_to(cx * u, cy * u)
        cr.arc(cx * u, cy * u, (r - 0.9) * u, a0, a1)
        cr.close_path()
        cr.set_source_rgb(*col)
        cr.fill()
    # glossy highlight
    g = cairo.LinearGradient(0, (cy - r) * u, 0, cy * u)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.75)
    g.add_color_stop_rgba(1, 1, 1, 1, 0.05)
    cr.save()
    cr.translate(cx * u, (cy - r * 0.45) * u)
    cr.scale(r * 0.72 * u, r * 0.42 * u)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()
    cr.set_source(g)
    cr.fill()
    ring(cr)
    cr.set_line_width(1.0 * u)
    cr.set_source_rgba(0, 0, 0, 0.45)
    cr.stroke()


def wait_frame(frame, frames):
    def draw(cr, u):
        _spinner(cr, u, 12, 12, 8.2, frame, frames)
    return draw


def progress_frame(frame, frames):
    def draw(cr, u):
        arrow(cr, u)
        _spinner(cr, u, 17.4, 17.4, 4.6, frame, frames)
    return draw


# -- the theme ------------------------------------------------------------------
FRAMES = 12
CURSORS = {
    # name: (painter or [frames], (hot x, hot y) in 24-units, aliases)
    "default": (arrow, (4, 2.5), ["left_ptr", "arrow", "top_left_arrow", "X_cursor", "right_ptr",
                                   "center_ptr", "dnd-move", "dnd-ask", "draft", "default"]),
    "pointer": (pointer, (9.8, 2.6), ["hand1", "hand2", "pointing_hand", "e29285e634086352946a0e7090d73106",
                                       "9d800788f1b08800ae810202380a0822"]),
    "text": (ibeam, (12, 12), ["xterm", "ibeam"]),
    "vertical-text": (vertical_text, (12, 12), []),
    "wait": ([wait_frame(i, FRAMES) for i in range(FRAMES)], (12, 12), ["watch"]),
    "progress": ([progress_frame(i, FRAMES) for i in range(FRAMES)], (4, 2.5),
                 ["left_ptr_watch", "half-busy", "08e8e1c95fe2fc01f976f1e063a24ccd",
                  "3ecb610c1bf2410f44200f48c40d3599"]),
    "crosshair": (crosshair, (12, 12), ["cross", "tcross", "cross_reverse", "diamond_cross"]),
    "cell": (cell, (12, 12), ["plus"]),
    "move": (move, (12, 12), ["fleur", "all-scroll", "size_all", "4498f0e0c1937ffe01fd06f973665830",
                              "9081237383d90e509aa00f00170e968f"]),
    "not-allowed": (not_allowed, (12, 12), ["circle", "crossed_circle", "no-drop", "forbidden", "dnd-none",
                                            "03b6e0fcb3499374a867c041f52298f0"]),
    "grab": (grab, (12, 10), ["openhand", "hand", "5aca4d189052212118709018842178c0",
                              "9141b49c8149039304290b508d208c40"]),
    "grabbing": (grabbing, (12, 12), ["closedhand", "fcf21c00b30f7e3f83fe0dfd12e71cff"]),
    "ew-resize": (ew, (12, 12), ["sb_h_double_arrow", "h_double_arrow", "size_hor", "e-resize", "w-resize",
                                 "left_side", "right_side", "14fef782d02440884392942c11205230"]),
    "ns-resize": (ns, (12, 12), ["sb_v_double_arrow", "v_double_arrow", "size_ver", "n-resize", "s-resize",
                                 "top_side", "bottom_side", "2870a09082c103050810ffdffffe0204"]),
    "nwse-resize": (nwse, (12, 12), ["bd_double_arrow", "size_fdiag", "top_left_corner", "bottom_right_corner",
                                     "nw-resize", "se-resize", "c7088f0f3e6c8088236ef8e1e3e70000"]),
    "nesw-resize": (nesw, (12, 12), ["fd_double_arrow", "size_bdiag", "top_right_corner", "bottom_left_corner",
                                     "ne-resize", "sw-resize", "fcf1c3c7cd4491d801f1e1c78f100000"]),
    "col-resize": (col_resize, (12, 12), ["split_h", "028006030e0e7ebffc7f7070c0600140"]),
    "row-resize": (row_resize, (12, 12), ["split_v", "00008160000006810000408080010102"]),
    "help": (help_, (4, 2.5), ["question_arrow", "whats_this", "left_ptr_help",
                               "d9ce0ab605698f320427677b458ad60b", "5c6cd98b3f3ebcb1f9c7f1c204630408"]),
    "copy": (copy, (4, 2.5), ["dnd-copy", "1081e37283d90000800003c07f3ef6bf", "6407b0e94181790501fd1e167b474872"]),
    "alias": (alias, (4, 2.5), ["link", "dnd-link", "640fb0e74195791501fd1ed57b41487f",
                                "3085a0e285430894940527032f8b26df"]),
    "context-menu": (context_menu, (4, 2.5), []),
    "zoom-in": (zoom_in, (10, 10), []),
    "zoom-out": (zoom_out, (10, 10), []),
}


def _render(painter, size):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    painter(cr, size / 24.0)
    surf.flush()
    return surf


def _xcursor(images):
    """images: [(nominal size, width, height, xhot, yhot, delay, argb bytes)] -> Xcursor file bytes."""
    header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(images))
    pos = 16 + 12 * len(images)
    toc, chunks = b"", b""
    for nominal, w, h, xh, yh, delay, pixels in images:
        toc += struct.pack("<III", 0xFFFD0002, nominal, pos)
        chunk = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, nominal, 1, w, h, xh, yh, delay) + pixels
        chunks += chunk
        pos += len(chunk)
    return header + toc + chunks


def write_theme(root):
    out = os.path.join(root, "cursors")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(root, "index.theme"), "w") as f:
        f.write("[Icon Theme]\nName=Slope Cursors\nComment=Slope's glossy Mac-style cursors\n")
    for name, (painter, (hx, hy), aliases) in CURSORS.items():
        frames = painter if isinstance(painter, list) else [painter]
        images = []
        for size in SIZES:
            u = size / 24.0
            for fr in frames:
                surf = _render(fr, size)
                # cairo ARGB32 is premultiplied native-endian ARGB: what Xcursor wants
                images.append((size, size, size, int(round(hx * u)), int(round(hy * u)),
                               60 if len(frames) > 1 else 0, bytes(surf.get_data())))
        with open(os.path.join(out, name), "wb") as f:
            f.write(_xcursor(images))
        for a in aliases:
            link = os.path.join(out, a)
            if a == name:
                continue
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(name, link)


def preview(path, size=48):
    """A sheet of every cursor (for checking the drawings)."""
    names = list(CURSORS)
    cols = 6
    rows = (len(names) + cols - 1) // cols
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, cols * (size + 16), rows * (size + 16))
    cr = cairo.Context(surf)
    cr.set_source_rgb(0.55, 0.7, 0.9)
    cr.paint()
    for i, n in enumerate(names):
        painter = CURSORS[n][0]
        painter = painter[3] if isinstance(painter, list) else painter
        img = _render(painter, size)
        cr.set_source_surface(img, (i % cols) * (size + 16) + 8, (i // cols) * (size + 16) + 8)
        cr.paint()
    surf.write_to_png(path)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        preview(sys.argv[2])
    else:
        write_theme(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/icons/Slope-Cursors")
