#!/usr/bin/env python3
"""Generate the Openbox traffic-light button masks (XBM) for the Slope theme."""
import math
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "..", "rootfs", "usr", "share", "themes", "Slope", "openbox-3")
N = 14
C = (N - 1) / 2.0
R = 6.3


def circle(x, y):
    return (x - C) ** 2 + (y - C) ** 2 <= R * R


def glyph_x(x, y):
    d1 = abs((x - C) - (y - C))
    d2 = abs((x - C) + (y - C))
    return (d1 < 0.9 or d2 < 0.9) and abs(x - C) < 3.1 and abs(y - C) < 3.1


def glyph_minus(x, y):
    return abs(y - C) < 0.8 and abs(x - C) < 3.6


def glyph_plus(x, y):
    return (abs(y - C) < 0.8 and abs(x - C) < 3.6) or (abs(x - C) < 0.8 and abs(y - C) < 3.6)


def write(name, fn, w=N, h=N):
    rows = []
    for y in range(h):
        bits = 0
        for x in range(w):
            if fn(x, y):
                bits |= 1 << x
        nbytes = (w + 7) // 8
        rows.extend((bits >> (8 * i)) & 0xFF for i in range(nbytes))
    with open(os.path.join(OUT, name + ".xbm"), "w") as f:
        f.write("#define %s_width %d\n#define %s_height %d\n" % (name, w, name, h))
        f.write("static unsigned char %s_bits[] = {\n   " % name)
        f.write(", ".join("0x%02x" % b for b in rows))
        f.write(" };\n")


os.makedirs(OUT, exist_ok=True)
plain = circle
for btn, g in (("close", glyph_x), ("iconify", glyph_minus), ("max", glyph_plus)):
    carved = (lambda gg: lambda x, y: circle(x, y) and not gg(x, y))(g)
    write(btn, plain)
    write(btn + "_hover", carved)
    write(btn + "_pressed", carved)
    write(btn + "_disabled", plain)
    if btn == "max":
        write("max_toggled", plain)
        write("max_toggled_hover", lambda x, y: circle(x, y) and not glyph_minus(x, y))
        write("max_toggled_pressed", lambda x, y: circle(x, y) and not glyph_minus(x, y))
for extra in ("desk", "desk_toggled", "shade", "shade_toggled"):
    write(extra, plain)
write("bullet", lambda x, y: x >= 1 and abs(y - 3.5) <= (x - 1) * 0.0 + (4 - x) * 0.9 and x <= 4, 6, 8)
print("wrote xbm masks to", OUT)
