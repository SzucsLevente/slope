"""Procedural artwork for Slope: wallpapers, the logo and glossy helpers.

Everything is drawn with cairo so it scales to any resolution.  The same
code renders the live desktop wallpaper and, at ISO build time, the PNGs
used by GRUB, Plymouth and the login screen.
"""
import math
import sys

import cairo

# name -> (sky stops, hill colour pairs (top, bottom), glow colour)
PALETTES = {
    "aqua": (
        [(0.0, "#06103a"), (0.45, "#1b3fa8"), (0.8, "#3f8ef0"), (1.0, "#9fe0ff")],
        [("#2d6cf0", "#0c2a86"), ("#24a5f5", "#0b4fb0"), ("#48d1f0", "#0d6fc0"), ("#9be7ff", "#1d86d8")],
        "#8fe3ff",
    ),
    "sunset": (
        [(0.0, "#1c0b3a"), (0.45, "#6a1f7a"), (0.8, "#e0517a"), (1.0, "#ffc27a")],
        [("#8a2f9a", "#3a0f5a"), ("#d2447f", "#6a1d6e"), ("#ff7a6a", "#a32d63"), ("#ffc07a", "#e2566b")],
        "#ffd29a",
    ),
    "aurora": (
        [(0.0, "#020d1a"), (0.5, "#06324a"), (0.85, "#0d7a73"), (1.0, "#7ef0c2")],
        [("#11706f", "#03303f"), ("#1aa38a", "#05505a"), ("#3fd49c", "#0a7a6e"), ("#a6f7cf", "#22a88a")],
        "#9dffd9",
    ),
    "graphite": (
        [(0.0, "#0b0c10"), (0.5, "#262a33"), (0.85, "#4a5262"), (1.0, "#a8b2c4")],
        [("#3a404c", "#15171c"), ("#545c6c", "#262a33"), ("#7a8496", "#3a404c"), ("#c2cad8", "#6a7284")],
        "#dfe8ff",
    ),
}

PALETTE_LABELS = {
    "aqua": "Aqua Slopes",
    "sunset": "Sunset Ridge",
    "aurora": "Aurora",
    "graphite": "Graphite",
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rounded_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def squircle(cr, x, y, s):
    """A superellipse-ish rounded square, like a modern app icon."""
    rounded_rect(cr, x, y, s, s, s * 0.225)


def _hill_y(x, w, h, layer):
    t = x / max(1.0, w)
    base = h * (0.52 + layer * 0.115)
    amp = h * (0.10 - layer * 0.012)
    return (base
            - amp * math.sin(t * math.pi * (1.15 + layer * 0.35) + layer * 1.7)
            - amp * 0.45 * math.sin(t * math.pi * (3.1 + layer * 0.6) + layer * 0.9)
            + amp * 0.18 * math.cos(t * math.pi * 7.0 + layer))


def draw_wallpaper(cr, w, h, palette="aqua"):
    sky, hills, glow = PALETTES.get(palette, PALETTES["aqua"])

    g = cairo.LinearGradient(0, 0, 0, h)
    for off, col in sky:
        g.add_color_stop_rgb(off, *hex_rgb(col))
    cr.set_source(g)
    cr.paint()

    # soft glow behind the hills
    gr, gg, gb = hex_rgb(glow)
    rg = cairo.RadialGradient(w * 0.62, h * 0.55, 0, w * 0.62, h * 0.55, max(w, h) * 0.6)
    rg.add_color_stop_rgba(0, gr, gg, gb, 0.55)
    rg.add_color_stop_rgba(0.4, gr, gg, gb, 0.18)
    rg.add_color_stop_rgba(1, gr, gg, gb, 0.0)
    cr.set_source(rg)
    cr.paint()

    # a few stars at the top
    cr.save()
    for i in range(140):
        sx = (math.sin(i * 12.9898) * 43758.5453) % 1.0
        sy = (math.sin(i * 78.233) * 12345.678) % 1.0
        sy = abs(sy) * 0.45
        a = 0.15 + 0.6 * abs(math.sin(i * 3.3))
        cr.set_source_rgba(1, 1, 1, a * (1 - sy * 1.8))
        cr.arc(abs(sx) * w, sy * h, 0.6 + (i % 3) * 0.45 * (w / 1920.0 + 0.4), 0, 2 * math.pi)
        cr.fill()
    cr.restore()

    step = max(2.0, w / 480.0)
    for layer, (top, bottom) in enumerate(hills):
        cr.new_path()
        cr.move_to(0, h)
        x = 0.0
        pts = []
        while x <= w + step:
            y = _hill_y(x, w, h, layer)
            pts.append((x, y))
            x += step
        for px, py in pts:
            cr.line_to(px, py)
        cr.line_to(w, h)
        cr.close_path()
        miny = min(p[1] for p in pts)
        lg = cairo.LinearGradient(0, miny, 0, h)
        tr, tg, tb = hex_rgb(top)
        br, bg_, bb = hex_rgb(bottom)
        lg.add_color_stop_rgba(0, tr, tg, tb, 0.92)
        lg.add_color_stop_rgba(1, br, bg_, bb, 0.98)
        cr.set_source(lg)
        cr.fill()

        # glassy sheen just below the ridge
        cr.new_path()
        for i, (px, py) in enumerate(pts):
            (cr.move_to if i == 0 else cr.line_to)(px, py)
        for px, py in reversed(pts):
            cr.line_to(px, py + h * 0.05)
        cr.close_path()
        sg = cairo.LinearGradient(0, miny, 0, miny + h * 0.25)
        sg.add_color_stop_rgba(0, 1, 1, 1, 0.22)
        sg.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(sg)
        cr.fill()

        # bright ridge line
        cr.new_path()
        for i, (px, py) in enumerate(pts):
            (cr.move_to if i == 0 else cr.line_to)(px, py)
        cr.set_line_width(max(1.0, h / 700.0))
        cr.set_source_rgba(1, 1, 1, 0.45 - layer * 0.06)
        cr.stroke()


def mark_path(cr, x, y, s):
    """The Slope mark: a rounded hill with a little sun."""
    cr.new_path()
    cr.move_to(x + s * 0.04, y + s * 0.92)
    cr.curve_to(x + s * 0.18, y + s * 0.92, x + s * 0.26, y + s * 0.30, x + s * 0.50, y + s * 0.30)
    cr.curve_to(x + s * 0.70, y + s * 0.30, x + s * 0.74, y + s * 0.62, x + s * 0.83, y + s * 0.70)
    cr.curve_to(x + s * 0.89, y + s * 0.76, x + s * 0.93, y + s * 0.84, x + s * 0.96, y + s * 0.92)
    cr.close_path()
    cr.new_sub_path()
    cr.arc(x + s * 0.80, y + s * 0.24, s * 0.13, 0, 2 * math.pi)


def draw_mark(cr, x, y, s, rgba=(0.1, 0.1, 0.12, 1.0)):
    mark_path(cr, x, y, s)
    cr.set_source_rgba(*rgba)
    cr.fill()


def draw_logo(cr, x, y, s):
    """Glossy app-icon style logo."""
    # drop shadow
    cr.save()
    for i in range(6):
        cr.set_source_rgba(0, 0, 0, 0.05)
        squircle(cr, x - i * 0.5, y + s * 0.03 + i * 0.6, s + i)
        cr.fill()
    cr.restore()

    squircle(cr, x, y, s)
    g = cairo.LinearGradient(0, y, 0, y + s)
    g.add_color_stop_rgb(0, *hex_rgb("#7fd8ff"))
    g.add_color_stop_rgb(0.5, *hex_rgb("#2f8cf5"))
    g.add_color_stop_rgb(1, *hex_rgb("#1338b8"))
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0.1, 0.4, 0.6)
    cr.set_line_width(max(1, s / 90.0))
    cr.stroke()

    # the mark in white
    cr.save()
    squircle(cr, x, y, s)
    cr.clip()
    mark_path(cr, x + s * 0.14, y + s * 0.16, s * 0.72)
    mg = cairo.LinearGradient(0, y + s * 0.3, 0, y + s)
    mg.add_color_stop_rgba(0, 1, 1, 1, 1)
    mg.add_color_stop_rgba(1, 0.80, 0.92, 1, 1)
    cr.set_source(mg)
    cr.fill()
    cr.restore()

    # glossy top highlight
    cr.save()
    squircle(cr, x, y, s)
    cr.clip()
    cr.new_path()
    cr.move_to(x, y)
    cr.line_to(x + s, y)
    cr.line_to(x + s, y + s * 0.42)
    cr.curve_to(x + s * 0.7, y + s * 0.52, x + s * 0.3, y + s * 0.52, x, y + s * 0.42)
    cr.close_path()
    hg = cairo.LinearGradient(0, y, 0, y + s * 0.5)
    hg.add_color_stop_rgba(0, 1, 1, 1, 0.75)
    hg.add_color_stop_rgba(1, 1, 1, 1, 0.08)
    cr.set_source(hg)
    cr.fill()
    cr.restore()


def gel(cr, x, y, w, h, r, color="#3d8ff0", alpha=1.0):
    """An Aqua-style glossy gel pill."""
    cr_, cg, cb = hex_rgb(color)
    rounded_rect(cr, x, y, w, h, r)
    g = cairo.LinearGradient(0, y, 0, y + h)
    g.add_color_stop_rgba(0, min(1, cr_ + 0.35), min(1, cg + 0.35), min(1, cb + 0.35), alpha)
    g.add_color_stop_rgba(0.5, cr_, cg, cb, alpha)
    g.add_color_stop_rgba(0.51, cr_ * 0.85, cg * 0.85, cb * 0.85, alpha)
    g.add_color_stop_rgba(1, min(1, cr_ + 0.25), min(1, cg + 0.25), min(1, cb + 0.25), alpha)
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(cr_ * 0.5, cg * 0.5, cb * 0.5, 0.7 * alpha)
    cr.set_line_width(1)
    cr.stroke()
    rounded_rect(cr, x + h * 0.18, y + 1.5, w - h * 0.36, h * 0.45, r * 0.8)
    hg = cairo.LinearGradient(0, y, 0, y + h * 0.5)
    hg.add_color_stop_rgba(0, 1, 1, 1, 0.85 * alpha)
    hg.add_color_stop_rgba(1, 1, 1, 1, 0.15 * alpha)
    cr.set_source(hg)
    cr.fill()


# ---------------------------------------------------------------------------
# Glossy "traffic light" title-bar buttons (rendered to PNG for Openbox)
# ---------------------------------------------------------------------------
LIGHTS = {
    "close": ("#ff5f57", "#c92a20", "#ffb0a8"),
    "iconify": ("#ffc12e", "#c98a06", "#ffe6a0"),
    "max": ("#2fd04a", "#139228", "#aaf5b0"),
}
INACTIVE = ("#d9dbe0", "#a2a6ae", "#f4f5f8")


def draw_light(cr, size, colors, glyph=None, pressed=False):
    """A glossy Aqua gel sphere filling a size x size canvas."""
    base, deep, glow = (hex_rgb(c) for c in colors)
    if pressed:
        base = tuple(c * 0.8 for c in base)
        deep = tuple(c * 0.75 for c in deep)
    cx = cy = size / 2.0
    r = size * 0.44

    # soft drop shadow
    sh = cairo.RadialGradient(cx, cy + r * 0.12, r * 0.8, cx, cy + r * 0.12, r * 1.18)
    sh.add_color_stop_rgba(0, 0, 0, 0, 0.35)
    sh.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.set_source(sh)
    cr.arc(cx, cy + r * 0.12, r * 1.18, 0, 2 * math.pi)
    cr.fill()

    # dark rim
    rim = cairo.LinearGradient(0, cy - r, 0, cy + r)
    rim.add_color_stop_rgb(0, *(c * 0.55 for c in deep))
    rim.add_color_stop_rgb(1, *(c * 0.85 for c in deep))
    cr.set_source(rim)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()

    # body: deep at the top, bright and glowing towards the bottom
    ri = r * 0.9
    body = cairo.RadialGradient(cx, cy + ri * 0.55, ri * 0.1, cx, cy + ri * 0.2, ri * 1.25)
    body.add_color_stop_rgb(0, *glow)
    body.add_color_stop_rgb(0.45, *base)
    body.add_color_stop_rgb(1, *deep)
    cr.set_source(body)
    cr.arc(cx, cy, ri, 0, 2 * math.pi)
    cr.fill()

    # inner shading ring for depth
    ring = cairo.RadialGradient(cx, cy, ri * 0.7, cx, cy, ri)
    ring.add_color_stop_rgba(0, 0, 0, 0, 0)
    ring.add_color_stop_rgba(1, 0, 0, 0, 0.22)
    cr.set_source(ring)
    cr.arc(cx, cy, ri, 0, 2 * math.pi)
    cr.fill()

    # specular highlight
    cr.save()
    cr.translate(cx, cy - ri * 0.46)
    cr.scale(ri * 0.72, ri * 0.44)
    hl = cairo.LinearGradient(0, -1, 0, 1)
    hl.add_color_stop_rgba(0, 1, 1, 1, 0.98)
    hl.add_color_stop_rgba(1, 1, 1, 1, 0.12)
    cr.set_source(hl)
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.fill()
    cr.restore()

    # a tiny sparkle
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.arc(cx - ri * 0.38, cy - ri * 0.5, ri * 0.09, 0, 2 * math.pi)
    cr.fill()

    if glyph:
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(max(1.5, size * 0.075))
        cr.set_source_rgba(*(c * 0.35 for c in deep), 0.85)
        g = ri * 0.42
        if glyph == "x":
            cr.move_to(cx - g, cy - g); cr.line_to(cx + g, cy + g)
            cr.move_to(cx + g, cy - g); cr.line_to(cx - g, cy + g)
        elif glyph == "-":
            cr.move_to(cx - g * 1.15, cy); cr.line_to(cx + g * 1.15, cy)
        elif glyph == "+":
            cr.move_to(cx - g * 1.1, cy); cr.line_to(cx + g * 1.1, cy)
            cr.move_to(cx, cy - g * 1.1); cr.line_to(cx, cy + g * 1.1)
        cr.stroke()
        cr.restore()


def render_titlebuttons(out, size=68):
    """Write <button>[_state][_inactive].png files for the patched Openbox."""
    import os
    os.makedirs(out, exist_ok=True)
    glyphs = {"close": "x", "iconify": "-", "max": "+"}

    def save(name, colors, glyph=None, pressed=False):
        render_png(os.path.join(out, name + ".png"), size, size,
                   lambda cr, w, h: draw_light(cr, w, colors, glyph, pressed))

    for btn, colors in LIGHTS.items():
        g = glyphs[btn]
        for suffix in ("", "_toggled"):
            if suffix and btn != "max":
                continue
            save(btn + suffix, colors)
            save(btn + suffix + "_hover", colors, g)
            save(btn + suffix + "_pressed", colors, g, pressed=True)
            save(btn + suffix + "_inactive", INACTIVE)
            save(btn + suffix + "_hover_inactive", colors, g)
        save(btn + "_disabled", INACTIVE)


def render_png(path, w, h, painter):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    painter(cr, w, h)
    surf.write_to_png(path)


def _caption(cr, w, y, text, size, bold=False):
    cr.select_font_face("Inter", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    x = (w - ext.width) / 2 - ext.x_bearing
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.move_to(x + 1, y + 2)
    cr.show_text(text)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.move_to(x, y)
    cr.show_text(text)


def _glossy_tile(cr, x, y, s, top, bottom):
    squircle(cr, x, y, s)
    g = cairo.LinearGradient(0, y, 0, y + s)
    g.add_color_stop_rgb(0, *hex_rgb(top))
    g.add_color_stop_rgb(1, *hex_rgb(bottom))
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.set_line_width(max(1, s / 90.0))
    cr.stroke()


def _gloss(cr, x, y, s):
    cr.save()
    squircle(cr, x, y, s)
    cr.clip()
    cr.new_path()
    cr.move_to(x, y)
    cr.line_to(x + s, y)
    cr.line_to(x + s, y + s * 0.42)
    cr.curve_to(x + s * 0.7, y + s * 0.52, x + s * 0.3, y + s * 0.52, x, y + s * 0.42)
    cr.close_path()
    hg = cairo.LinearGradient(0, y, 0, y + s * 0.5)
    hg.add_color_stop_rgba(0, 1, 1, 1, 0.7)
    hg.add_color_stop_rgba(1, 1, 1, 1, 0.06)
    cr.set_source(hg)
    cr.fill()
    cr.restore()


def draw_launchpad_icon(cr, x, y, s):
    """Silver glossy tile with a 3x3 grid of candy-coloured app squares."""
    _glossy_tile(cr, x, y, s, "#f4f6fa", "#a9b2c2")
    colors = ["#ff5f57", "#febc2e", "#28c840", "#2f86f3", "#a35cf0", "#ff7ac0",
              "#25c2d9", "#ff9a3d", "#5b6cff"]
    pad = s * 0.17
    cell = (s - 2 * pad) / 3.0
    for i, col in enumerate(colors):
        cx = x + pad + (i % 3) * cell + cell * 0.1
        cy = y + pad + (i // 3) * cell + cell * 0.1
        sz = cell * 0.8
        art_r, art_g, art_b = hex_rgb(col)
        rounded_rect(cr, cx, cy, sz, sz, sz * 0.28)
        g = cairo.LinearGradient(0, cy, 0, cy + sz)
        g.add_color_stop_rgb(0, min(1, art_r + 0.12), min(1, art_g + 0.12), min(1, art_b + 0.12))
        g.add_color_stop_rgb(1, art_r * 0.72, art_g * 0.72, art_b * 0.72)
        cr.set_source(g)
        cr.fill()
        rounded_rect(cr, cx + sz * 0.1, cy + sz * 0.06, sz * 0.8, sz * 0.4, sz * 0.2)
        cr.set_source_rgba(1, 1, 1, 0.45)
        cr.fill()
    _gloss(cr, x, y, s)


def draw_wifi_icon(cr, x, y, s, bars=4, active=True):
    """Glossy blue tile with white Wi-Fi arcs (dimmed arcs above the signal level)."""
    if active:
        _glossy_tile(cr, x, y, s, "#7fd8ff", "#1351c8")
    else:
        _glossy_tile(cr, x, y, s, "#d9dde5", "#8c94a3")
    cx, cy = x + s / 2.0, y + s * 0.74
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for i, r in enumerate((0.42, 0.30, 0.18)):
        level = 3 - i            # outer arc needs 4 bars, inner needs 2
        on = bars >= level + 1
        cr.new_path()
        cr.arc(cx, cy, s * r, math.pi * 1.25, math.pi * 1.75)
        cr.set_line_width(s * 0.075)
        cr.set_source_rgba(1, 1, 1, 1.0 if on else 0.35)
        cr.stroke()
    cr.arc(cx, cy, s * 0.06, 0, 2 * math.pi)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()
    cr.restore()
    _gloss(cr, x, y, s)


def draw_installer_icon(cr, x, y, s):
    """Blue glossy tile with a hard drive and a downward arrow."""
    _glossy_tile(cr, x, y, s, "#7fd8ff", "#1338b8")
    # drive
    dx, dy, dw, dh = x + s * 0.2, y + s * 0.58, s * 0.6, s * 0.22
    rounded_rect(cr, dx, dy, dw, dh, dh * 0.3)
    g = cairo.LinearGradient(0, dy, 0, dy + dh)
    g.add_color_stop_rgb(0, 0.97, 0.98, 1)
    g.add_color_stop_rgb(1, 0.72, 0.76, 0.84)
    cr.set_source(g)
    cr.fill()
    cr.set_source_rgb(0.2, 0.85, 0.35)
    cr.arc(dx + dw * 0.85, dy + dh / 2, dh * 0.13, 0, 2 * math.pi)
    cr.fill()
    # arrow
    ax = x + s / 2
    cr.new_path()
    cr.move_to(ax - s * 0.08, y + s * 0.16)
    cr.line_to(ax + s * 0.08, y + s * 0.16)
    cr.line_to(ax + s * 0.08, y + s * 0.34)
    cr.line_to(ax + s * 0.18, y + s * 0.34)
    cr.line_to(ax, y + s * 0.52)
    cr.line_to(ax - s * 0.18, y + s * 0.34)
    cr.line_to(ax - s * 0.08, y + s * 0.34)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    _gloss(cr, x, y, s)


def _cli():
    """python3 -m slope.art <outdir>: render static PNGs used at boot."""
    import os
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for name in PALETTES:
        render_png(os.path.join(out, "%s.png" % name), 1920, 1080,
                   lambda cr, w, h, n=name: draw_wallpaper(cr, w, h, n))
        render_png(os.path.join(out, "%s-thumb.png" % name), 240, 135,
                   lambda cr, w, h, n=name: draw_wallpaper(cr, w, h, n))

    def grub(cr, w, h):
        draw_wallpaper(cr, w, h, "aqua")
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.paint()
        draw_logo(cr, w - 64, 10, 48)   # small, top-right: the menu text covers the middle
    render_png(os.path.join(out, "grub.png"), 1024, 768, grub)

    def logo(cr, w, h):
        draw_logo(cr, 8, 8, w - 16)
    render_png(os.path.join(out, "logo-256.png"), 256, 256, logo)
    render_png(os.path.join(out, "logo-128.png"), 128, 128, logo)
    render_png(os.path.join(out, "logo-64.png"), 64, 64, logo)

    def mark(cr, w, h):
        draw_mark(cr, 0, 0, w, (1, 1, 1, 1))
    render_png(os.path.join(out, "mark-white-96.png"), 96, 96, mark)

    def dot(cr, w, h):
        cr.arc(w / 2, h / 2, w / 2 - 1, 0, 2 * math.pi)
        g = cairo.RadialGradient(w / 2, h / 3, 0, w / 2, h / 2, w / 2)
        g.add_color_stop_rgba(0, 1, 1, 1, 1)
        g.add_color_stop_rgba(1, 0.5, 0.8, 1, 0.9)
        cr.set_source(g)
        cr.fill()
    render_png(os.path.join(out, "dot.png"), 14, 14, dot)

    render_titlebuttons(os.path.join(out, "titlebuttons"))

    render_png(os.path.join(out, "launchpad-256.png"), 256, 256,
               lambda cr, w, h: draw_launchpad_icon(cr, 8, 8, w - 16))
    render_png(os.path.join(out, "installer-256.png"), 256, 256,
               lambda cr, w, h: draw_installer_icon(cr, 8, 8, w - 16))
    render_png(os.path.join(out, "wifi-256.png"), 256, 256,
               lambda cr, w, h: draw_wifi_icon(cr, 8, 8, w - 16))

    def welcome(cr, w, h):
        draw_wallpaper(cr, w, h, "aqua")
        draw_logo(cr, w / 2 - 70, h * 0.18, 140)
        _caption(cr, w, h * 0.72, "Welcome to Slope", h * 0.085, bold=True)
    render_png(os.path.join(out, "calamares-welcome.png"), 640, 400, welcome)

    slides = [
        ("aqua", "A desktop that shines", "Glossy buttons, a glass Dock and a global menu bar."),
        ("aurora", "Close a window, keep the app", "Apps stay in the Dock until you choose Quit (⌘Q)."),
        ("sunset", "Find everything with Launchpad", "Click the Launchpad icon in the Dock or press F4."),
        ("graphite", "Almost done", "Slope is being installed on your computer."),
    ]
    for i, (pal, title, sub) in enumerate(slides, 1):
        def slide(cr, w, h, pal=pal, title=title, sub=sub):
            draw_wallpaper(cr, w, h, pal)
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.paint()
            _caption(cr, w, h * 0.42, title, h * 0.09, bold=True)
            _caption(cr, w, h * 0.56, sub, h * 0.045)
        render_png(os.path.join(out, "calamares-slide-%d.png" % i), 800, 440, slide)


if __name__ == "__main__":
    _cli()
