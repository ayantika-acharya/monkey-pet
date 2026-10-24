#!/usr/bin/env python3
"""
Pixel-art sprite baker for the Monkey desktop pet.

The monkey is drawn procedurally on a 40x40 pixel grid using flat shapes with
hard 1px outlines (no anti-aliasing, no gradients) so every animation frame
stays perfectly consistent. Frames are baked into:

  assets/monkey.atlas       raw BGRA, premultiplied -> loaded zero-copy by Rust
  assets/monkey-sheet.png   contact sheet, for humans

Run:  python tools/spritegen.py
      python tools/spritegen.py ascii    # dump frames to the console
"""

import math
import os
import struct
import sys
import zlib

W = H = 40

# --------------------------------------------------------------- palettes ---
# Every character uses the same palette keys, only the colours change, so the
# same procedural drawing code can render all of them.
# name -> (r, g, b, a)
PAL = {
    ".": (0, 0, 0, 0),            # transparent
    "k": (38, 24, 15, 255),       # outline / darkest fur
    "d": (92, 56, 32, 255),       # dark fur
    "f": (124, 80, 44, 255),      # mid fur
    "l": (160, 110, 64, 255),     # light fur (top-light)
    "s": (226, 179, 131, 255),    # face skin
    "S": (243, 213, 176, 255),    # muzzle light
    "r": (205, 137, 107, 255),    # inner ear
    "b": (228, 150, 122, 255),    # blush
    "e": (28, 19, 13, 255),       # eye
    "w": (255, 255, 255, 255),    # white
    "m": (96, 44, 38, 255),       # mouth
    "t": (214, 112, 108, 255),    # tongue
    "y": (247, 214, 74, 255),     # banana
    "Y": (203, 158, 40, 255),     # banana shade
    "z": (206, 224, 255, 235),    # sleep z
    "p": (255, 236, 150, 255),    # sparkle
    "a": (0, 0, 0, 40),           # soft ground shadow
    "g": (178, 186, 198, 255),    # shower metal
    "G": (104, 112, 126, 255),    # shower metal shade
    "u": (126, 198, 238, 255),    # water light
    "U": (64, 136, 196, 255),     # water deep
}

PAL_MONKEY = dict(PAL)

# Tom: grey cat, white muzzle/belly.
PAL_TOM = dict(PAL)
PAL_TOM.update({
    "k": (28, 30, 38, 255),
    "d": (88, 94, 108, 255),
    "f": (132, 140, 156, 255),
    "l": (178, 186, 200, 255),
    "s": (240, 236, 230, 255),
    "S": (252, 250, 246, 255),
    "r": (224, 154, 168, 255),
    "b": (236, 150, 170, 255),
})

# Jerry: small brown mouse.
PAL_JERRY = dict(PAL)
PAL_JERRY.update({
    "k": (40, 26, 16, 255),
    "d": (128, 86, 48, 255),
    "f": (172, 124, 74, 255),
    "l": (208, 164, 110, 255),
    "s": (240, 210, 170, 255),
    "S": (250, 228, 196, 255),
    "r": (226, 158, 150, 255),
    "b": (230, 150, 140, 255),
})

PALETTES = {"monkey": PAL_MONKEY, "tom": PAL_TOM, "jerry": PAL_JERRY}

ORDER = list(PAL.keys())
INDEX = {c: i for i, c in enumerate(ORDER)}
OUT = INDEX["k"]
NONE = 0


# ----------------------------------------------------------------- canvas ---
class Canvas:
    def __init__(self):
        self.px = bytearray(W * H)  # palette indices, 0 == transparent

    def set(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.px[y * W + x] = c

    def paint(self, mask, color, clip=None, outline=True, soften=None):
        """Fill a mask with a 1px dark outline, kept inside `clip` and skipped
        where `soften` is set (used to hide the seam at limb joints)."""
        if outline:
            for (x, y) in dilate(mask):
                i = y * W + x
                if mask[i]:
                    continue
                if clip is not None and not clip[i]:
                    continue
                if soften is not None and soften[i]:
                    continue
                self.px[i] = OUT
        for i in range(W * H):
            if mask[i] and (clip is None or clip[i]):
                self.px[i] = color

    def pset(self, x, y, c, clip=None):
        x, y = int(x), int(y)
        if not (0 <= x < W and 0 <= y < H):
            return
        if clip is not None and not clip[y * W + x]:
            return
        self.px[y * W + x] = c


# ------------------------------------------------------------------ masks ---
def empty():
    return bytearray(W * H)


def dilate(mask):
    out = []
    for y in range(H):
        base = y * W
        for x in range(W):
            if mask[base + x]:
                continue
            hit = False
            if x > 0 and mask[base + x - 1]:
                hit = True
            elif x < W - 1 and mask[base + x + 1]:
                hit = True
            elif y > 0 and mask[(y - 1) * W + x]:
                hit = True
            elif y < H - 1 and mask[(y + 1) * W + x]:
                hit = True
            if hit:
                out.append((x, y))
    return out


def m_ellipse(cx, cy, rx, ry):
    m = empty()
    if rx <= 0 or ry <= 0:
        return m
    y0 = max(0, int(cy - ry) - 1)
    y1 = min(H, int(cy + ry) + 2)
    for y in range(y0, y1):
        dy = (y + 0.5 - cy) / ry
        if abs(dy) > 1.0:
            continue
        span = rx * math.sqrt(max(0.0, 1.0 - dy * dy))
        x0 = max(0, int(cx - span) - 1)
        x1 = min(W, int(cx + span) + 2)
        for x in range(x0, x1):
            dx = (x + 0.5 - cx) / rx
            if dx * dx + dy * dy <= 1.0:
                m[y * W + x] = 1
    return m


def m_union(*masks):
    m = empty()
    for k in masks:
        for i in range(W * H):
            if k[i]:
                m[i] = 1
    return m


def m_intersect(a, b):
    m = empty()
    for i in range(W * H):
        if a[i] and b[i]:
            m[i] = 1
    return m


def m_sub(a, b):
    m = empty()
    for i in range(W * H):
        if a[i] and not b[i]:
            m[i] = 1
    return m


def m_not(a):
    m = empty()
    for i in range(W * H):
        if not a[i]:
            m[i] = 1
    return m


def m_triangle(p0, p1, p2):
    m = empty()
    xs = [p0[0], p1[0], p2[0]]
    ys = [p0[1], p1[1], p2[1]]
    x0, x1 = max(0, int(min(xs))), min(W - 1, int(max(xs)) + 1)
    y0, y1 = max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1)

    def edge(a, b, px, py):
        return (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])

    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            px, py = x + 0.5, y + 0.5
            d0 = edge(p0, p1, px, py)
            d1 = edge(p1, p2, px, py)
            d2 = edge(p2, p0, px, py)
            if (d0 >= 0 and d1 >= 0 and d2 >= 0) or (d0 <= 0 and d1 <= 0 and d2 <= 0):
                m[y * W + x] = 1
    return m


def m_scale_about(mask, cx, cy, f):
    """Shrink/expand a mask around a point (used for inner ears)."""
    out = empty()
    for y in range(H):
        for x in range(W):
            if mask[y * W + x]:
                sx = int(cx + (x + 0.5 - cx) * f)
                sy = int(cy + (y + 0.5 - cy) * f)
                if 0 <= sx < W and 0 <= sy < H:
                    out[sy * W + sx] = 1
    return out


def m_rect(x0, y0, x1, y1):
    """Inclusive axis-aligned rectangle mask."""
    m = empty()
    for y in range(max(0, y0), min(H, y1 + 1)):
        for x in range(max(0, x0), min(W, x1 + 1)):
            m[y * W + x] = 1
    return m


def m_capsule(x0, y0, x1, y1, r0, r1=None):
    """Thick line from p0 to p1 with tapering radius -> limb / bone."""
    if r1 is None:
        r1 = r0
    length = math.hypot(x1 - x0, y1 - y0)
    steps = max(2, int(length * 3) + 1)
    m = empty()
    rmax = int(max(r0, r1)) + 2
    for i in range(steps):
        t = i / (steps - 1)
        cx = x0 + (x1 - x0) * t
        cy = y0 + (y1 - y0) * t
        r = r0 + (r1 - r0) * t
        for y in range(max(0, int(cy) - rmax), min(H, int(cy) + rmax + 2)):
            for x in range(max(0, int(cx) - rmax), min(W, int(cx) + rmax + 2)):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    m[y * W + x] = 1
    return m


def m_polyline(points, r0, r1):
    m = empty()
    n = len(points) - 1
    for i in range(n):
        a, b = points[i], points[i + 1]
        r_a = r0 + (r1 - r0) * (i / n)
        r_b = r0 + (r1 - r0) * ((i + 1) / n)
        m = m_union(m, m_capsule(a[0], a[1], b[0], b[1], r_a, r_b))
    return m


def bezier(p0, p1, p2, p3, n=14):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


# ------------------------------------------------------------------ glyphs ---
def z_glyph(cx, cy, color):
    for dx, dy in ((0, 0), (1, 0), (2, 0), (1, 1), (0, 2), (1, 2), (2, 2)):
        yield (cx + dx, cy + dy), color


def sparkle(c, cx, cy, color, big=False):
    pts = [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]
    if big:
        pts += [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    for dx, dy in pts:
        c.set(int(cx + dx), int(cy + dy), color)


def draw_shower(c, phase):
    """Overhead shower head with falling, animated water."""
    # supply pipe (runs off the top edge) and head bar
    c.paint(m_rect(19, 0, 20, 1), INDEX["g"])
    c.paint(m_capsule(7.5, 2.0, 32.5, 2.0, 1.5, 1.5), INDEX["g"])

    nozzles = (9, 12, 15, 18, 21, 24, 27, 30)
    for nx in nozzles:
        c.set(nx, 4, INDEX["G"])

    # falling streams: short dashes, each stream with its own phase
    for j, nx in enumerate(nozzles):
        off = int(phase * 3 + j * 2.3) % 5
        for y in range(5, 38):
            k = (y + off) % 5
            if k in (0, 1):
                c.set(nx, y, INDEX["u"])
            elif k == 2:
                c.set(nx, y, INDEX["U"])

    # spray bouncing off the crown
    for j, nx in enumerate((11, 14, 17, 20, 23, 26, 29)):
        t = (phase * 0.8 + j * 0.37) % 1.0
        sy = 5 + int(t * 4)
        c.set(nx, sy, INDEX["w"])
        c.set(nx + (1 if j % 2 else -1), sy + 1, INDEX["u"])


# --------------------------------------------------------------- the pet ---
def draw_monkey(body_dy=0, head_dy=0, head_dx=0, squash=0.0,
                eye="open", mouth="smile", blush=False,
                hand_l=(9.6, 32.0), hand_r=(30.4, 32.0),
                foot_l=(15.5, 37.6), foot_r=(24.5, 37.6),
                tail_phase=0.0, tail_lift=0.0, ears=0,
                banana=None, zzz=None, sparkles=False, shadow=True,
                shower=None, species="monkey"):
    """Draw one frame of the pet. All coordinates are in the 40x40 grid.

    `species` only changes silhouette details (ears, tail, whiskers, build);
    the palette decides the colours, so all variants share this code.
    """
    c = Canvas()
    bd = round(body_dy)
    hd = round(head_dy)

    # --- ground shadow / shower puddle ------------------------------------
    if shadow:
        c.paint(m_ellipse(20, 39.0, 11.0, 1.7), INDEX["a"], outline=False)
    if shower is not None:
        c.paint(m_ellipse(20, 38.6, 13.0, 1.9), INDEX["U"], outline=False)
        c.paint(m_ellipse(17.5, 38.2, 5.5, 0.6), INDEX["u"], outline=False)
        c.paint(m_ellipse(24.5, 39.0, 3.5, 0.5), INDEX["u"], outline=False)

    # --- tail (behind everything) ----------------------------------------
    if species == "jerry":
        sway = math.sin(tail_phase) * 2.2
        ty = 33.0 + bd * 0.4
        pts = bezier((25.0, ty), (31.0, ty + 0.2),
                     (35.5 + sway, 24.0 + tail_lift), (30.0 + sway * 1.6, 18.0 + tail_lift))
        c.paint(m_polyline(pts, 1.6, 1.0), INDEX["d"])
    elif species == "tom":
        sway = math.sin(tail_phase) * 2.0
        ty = 33.0 + bd * 0.4
        pts = bezier((26.0, ty), (34.0, ty - 0.2),
                     (37.5 + sway, 23.5 + tail_lift + sway * 0.4), (29.5 + sway * 1.2, 17.5 + tail_lift))
        c.paint(m_polyline(pts, 2.0, 1.0), INDEX["d"])
    else:
        sway = math.sin(tail_phase) * 1.8
        ty = 33.0 + bd * 0.5
        pts = bezier((26.0, ty), (34.5, ty + 0.5),
                     (37.6 + sway, 25.0 + tail_lift), (28.6 + sway * 1.4, 19.6 + tail_lift))
        c.paint(m_polyline(pts, 2.0, 1.1), INDEX["d"])

    # --- legs / feet ------------------------------------------------------
    body_cy = 30.5 + bd
    body_rx = 8.4 + squash * 0.5
    body_ry = 6.2 - squash * 0.7
    hip_l, hip_r = (16.4, body_cy + 3.0), (23.6, body_cy + 3.0)
    feet = []
    for hip, foot in ((hip_l, foot_l), (hip_r, foot_r)):
        fy = foot[1] + bd * 0.3
        c.paint(m_capsule(hip[0], hip[1], foot[0], fy, 2.6, 2.4), INDEX["f"])
        c.paint(m_ellipse(foot[0], fy, 3.1, 2.1), INDEX["d"])
        feet.append((foot[0], fy))

    # --- body: two lobes so it has shoulders ------------------------------
    body = m_union(m_ellipse(20, body_cy - 4.4, 7.9, 3.2),
                   m_ellipse(20, body_cy, body_rx, body_ry))
    c.paint(body, INDEX["f"])
    inner = m_intersect(body, m_ellipse(20, body_cy, body_rx - 1, body_ry - 1))
    c.paint(m_sub(inner, m_ellipse(21.0, body_cy + 2.0, 8.0, 6.5)), INDEX["l"],
            outline=False)
    c.paint(m_sub(inner, m_ellipse(19.0, body_cy - 2.0, 8.0, 6.5)), INDEX["d"],
            outline=False)
    belly = m_intersect(m_ellipse(20, body_cy + 1.2, 5.1, 4.9), inner)
    c.paint(belly, INDEX["s"], outline=False)

    # --- ears (behind the head) -------------------------------------------
    hcy = 13.5 + hd  # head follows head_dy; anims pass body_dy too
    hcx = 20.0 + head_dx
    ears_mask = empty()
    if species == "jerry":
        # Round mouse ears
        for side in (-1, 1):
            ex = hcx + side * 10.0
            ey = hcy + 1.0 + ears * -0.6
            c.paint(m_ellipse(ex, ey, 5.0, 5.0), INDEX["f"])
            c.paint(m_ellipse(ex + side * 0.4, ey + 0.6, 2.8, 2.8), INDEX["r"], outline=False)
            ears_mask = m_union(ears_mask, m_ellipse(ex, ey, 5.0, 5.0))
    elif species == "tom":
        # Pointed triangular cat ears
        for side in (-1, 1):
            ex = hcx + side * 10.5
            ey = hcy + 0.5 + ears * -0.8
            c.paint(m_triangle((ex, ey + 3.5), (ex + side * 5.0, ey - 5.0), (ex + side * 0.5, ey + 3.0)), INDEX["f"])
            c.paint(m_triangle((ex, ey + 2.8), (ex + side * 3.2, ey - 2.4), (ex + side * 0.4, ey + 2.4)), INDEX["r"], outline=False)
            ears_mask = m_union(ears_mask, m_triangle((ex, ey + 3.5), (ex + side * 5.0, ey - 5.0), (ex + side * 0.5, ey + 3.0)))
    else:
        for side in (-1, 1):
            ex = hcx + side * 11.0
            ey = hcy + 2.0 + ears * -0.9
            c.paint(m_ellipse(ex, ey, 4.5, 4.6), INDEX["f"])
            c.paint(m_ellipse(ex + side * 0.7, ey + 0.4, 2.1, 2.2), INDEX["r"], outline=False)
            ears_mask = m_union(ears_mask,
                                m_ellipse(ex, ey, 4.5, 4.6),
                                m_ellipse(ex + side * 0.7, ey + 0.4, 2.1, 2.2))

    # --- head -------------------------------------------------------------
    if species == "jerry":
        head = m_ellipse(hcx, hcy, 9.4, 9.0)
    elif species == "tom":
        head = m_ellipse(hcx, hcy, 10.2, 9.8)
    else:
        head = m_ellipse(hcx, hcy, 10.5, 10.0)
    c.paint(head, INDEX["f"], soften=ears_mask)
    hinner = m_intersect(head, m_ellipse(hcx, hcy, 9.4 if species != "tom" else 9.1, 8.9 if species != "tom" else 8.7))
    c.paint(m_sub(hinner, m_ellipse(hcx + 1.5, hcy + 2.0, 9.4, 8.9)), INDEX["l"],
            outline=False)
    c.paint(m_sub(hinner, m_ellipse(hcx - 1.0, hcy - 2.5, 9.4, 8.9)), INDEX["d"],
            outline=False)

    if species == "tom":
        # Small tuft between ears
        c.paint(m_triangle((hcx - 0.8, hcy - 7.6), (hcx + 1.2, hcy - 9.6), (hcx + 0.4, hcy - 7.2)), INDEX["d"], soften=head)
    elif species == "monkey":
        # cowlick: a small tuft poking out of the skull
        c.paint(m_capsule(hcx - 0.6, hcy - 8.2, hcx + 1.4, hcy - 11.2, 1.5, 0.9),
                INDEX["d"], soften=head)
    else:
        # Jerry: tiny tufts
        c.paint(m_capsule(hcx - 0.4, hcy - 7.6, hcx + 0.8, hcy - 9.0, 1.0, 0.7),
                INDEX["d"], soften=head)

    # --- arms (after the head so raised paws are in front) ----------------
    for sh, hand in (((12.4, body_cy - 4.0), hand_l), ((27.6, body_cy - 4.0), hand_r)):
        hx, hy = hand[0], hand[1] + bd * 0.2
        arm = m_capsule(sh[0], sh[1], hx, hy, 2.5, 1.9)
        c.paint(arm, INDEX["f"], soften=body)
        c.paint(m_ellipse(hx, hy, 1.9, 1.8), INDEX["s"])

    # --- face -------------------------------------------------------------
    if species == "jerry":
        face = m_intersect(m_ellipse(hcx, hcy + 3.4, 6.6, 6.0), head)
        c.paint(face, INDEX["s"], outline=False)
        muzz = m_intersect(m_ellipse(hcx, hcy + 6.6, 5.2, 3.0), head)
        c.paint(muzz, INDEX["S"], outline=False)
        # Whiskers
        for side in (-1, 1):
            c.pset(hcx + side * 4.8, hcy + 6.4, INDEX["w"], clip=muzz)
            c.pset(hcx + side * 5.0, hcy + 6.8, INDEX["w"], clip=muzz)
            c.pset(hcx + side * 4.8, hcy + 7.2, INDEX["w"], clip=muzz)
    elif species == "tom":
        face = m_intersect(m_ellipse(hcx, hcy + 3.6, 6.8, 6.2), head)
        c.paint(face, INDEX["s"], outline=False)
        muzz = m_intersect(m_ellipse(hcx, hcy + 6.6, 5.0, 3.1), head)
        c.paint(muzz, INDEX["S"], outline=False)
        for side in (-1, 1):
            c.pset(hcx + side * 4.6, hcy + 6.6, INDEX["w"], clip=muzz)
            c.pset(hcx + side * 4.8, hcy + 7.0, INDEX["w"], clip=muzz)
            c.pset(hcx + side * 4.6, hcy + 7.4, INDEX["w"], clip=muzz)
    else:
        face = m_intersect(m_ellipse(hcx, hcy + 3.6, 7.0, 6.3), head)
        c.paint(face, INDEX["s"], outline=False)
        muzz = m_intersect(m_ellipse(hcx, hcy + 6.5, 4.9, 3.1), head)
        c.paint(muzz, INDEX["S"], outline=False)

    # brow
    if species == "jerry":
        for bx in (-4.8, 2.8):
            c.pset(hcx + bx, hcy + 0.8, INDEX["d"], clip=face)
            c.pset(hcx + bx + 1, hcy + 0.6, INDEX["d"], clip=face)
    elif species == "tom":
        for bx in (-5.0, 3.0):
            c.pset(hcx + bx, hcy + 0.6, INDEX["d"], clip=face)
            c.pset(hcx + bx + 1, hcy + 0.4, INDEX["d"], clip=face)
    else:
        for bx in (-5.6, 3.2):
            c.pset(hcx + bx, hcy + 0.6, INDEX["d"], clip=face)
            c.pset(hcx + bx + 1, hcy + 0.4, INDEX["d"], clip=face)

    # --- eyes -------------------------------------------------------------
    if species == "jerry":
        ey = hcy + 1.6
        for ex in (-4.0, 2.6):
            exx = hcx + ex
            if eye == "open":
                for dx in (0, 1):
                    for dy in range(3):
                        c.pset(exx + dx, ey + dy, INDEX["e"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
                c.pset(exx + 1, ey + 2, INDEX["d"], clip=face)
            elif eye == "wide":
                for dx in (-1, 0, 1):
                    for dy in range(4):
                        c.pset(exx + dx, ey + dy - 1, INDEX["e"], clip=face)
                c.pset(exx - 1, ey - 1, INDEX["w"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
            elif eye == "blink":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 1, INDEX["e"], clip=face)
            elif eye == "closed":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 1, INDEX["e"], clip=face)
            elif eye == "happy":
                c.pset(exx - 1, ey + 1, INDEX["e"], clip=face)
                c.pset(exx, ey, INDEX["e"], clip=face)
                c.pset(exx + 1, ey + 1, INDEX["e"], clip=face)
            elif eye == "sleepy":
                for dx in (0, 1):
                    c.pset(exx + dx, ey + 1, INDEX["e"], clip=face)
    elif species == "tom":
        ey = hcy + 1.6
        for ex in (-4.4, 3.0):
            exx = hcx + ex
            if eye == "open":
                for dx in (0, 1):
                    for dy in range(4):
                        c.pset(exx + dx, ey + dy, INDEX["e"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
                c.pset(exx + 1, ey + 3, INDEX["d"], clip=face)
            elif eye == "wide":
                for dx in (-1, 0, 1):
                    for dy in range(5):
                        c.pset(exx + dx, ey + dy - 1, INDEX["e"], clip=face)
                c.pset(exx - 1, ey - 1, INDEX["w"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
            elif eye == "blink":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
            elif eye == "closed":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
            elif eye == "happy":
                c.pset(exx - 1, ey + 2, INDEX["e"], clip=face)
                c.pset(exx, ey + 1, INDEX["e"], clip=face)
                c.pset(exx + 1, ey + 2, INDEX["e"], clip=face)
            elif eye == "sleepy":
                for dx in (0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
    else:
        ey = hcy + 1.6
        for ex in (-4.6, 3.2):
            exx = hcx + ex
            if eye == "open":
                for dx in (0, 1):
                    for dy in range(4):
                        c.pset(exx + dx, ey + dy, INDEX["e"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
                c.pset(exx + 1, ey + 3, INDEX["d"], clip=face)
            elif eye == "wide":
                for dx in (-1, 0, 1):
                    for dy in range(5):
                        c.pset(exx + dx, ey + dy - 1, INDEX["e"], clip=face)
                c.pset(exx - 1, ey - 1, INDEX["w"], clip=face)
                c.pset(exx, ey, INDEX["w"], clip=face)
            elif eye == "blink":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
            elif eye == "closed":
                for dx in (-1, 0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
                c.pset(exx - 1, ey + 3, INDEX["d"], clip=face)
            elif eye == "happy":
                c.pset(exx - 1, ey + 2, INDEX["e"], clip=face)
                c.pset(exx, ey + 1, INDEX["e"], clip=face)
                c.pset(exx + 1, ey + 2, INDEX["e"], clip=face)
            elif eye == "sleepy":
                for dx in (0, 1):
                    c.pset(exx + dx, ey + 2, INDEX["e"], clip=face)
                c.pset(exx + 1, ey + 1, INDEX["d"], clip=face)

    # --- nose + mouth -----------------------------------------------------
    ny = hcy + 5.2
    c.pset(hcx - 1, ny, INDEX["d"], clip=muzz)
    c.pset(hcx + 1, ny, INDEX["d"], clip=muzz)
    my = hcy + 7.3
    if mouth == "smile":
        c.pset(hcx - 1, my, INDEX["m"], clip=face)
        c.pset(hcx + 1, my, INDEX["m"], clip=face)
        c.pset(hcx, my + 1, INDEX["m"], clip=face)
    elif mouth == "grin":
        for dx in (-2, -1, 0, 1, 2):
            c.pset(hcx + dx, my, INDEX["m"], clip=face)
        for dx in (-1, 0, 1):
            c.pset(hcx + dx, my + 1, INDEX["t"], clip=face)
        c.pset(hcx - 2, my + 1, INDEX["m"], clip=face)
        c.pset(hcx + 2, my + 1, INDEX["m"], clip=face)
    elif mouth == "open":
        c.paint(m_ellipse(hcx, my + 1.2, 1.7, 1.7), INDEX["m"], clip=face)
        c.paint(m_ellipse(hcx, my + 2.1, 1.1, 0.8), INDEX["t"], clip=face, outline=False)
    elif mouth == "flat":
        for dx in (-1, 0, 1):
            c.pset(hcx + dx, my + 1, INDEX["m"], clip=face)
    elif mouth == "chew":
        c.pset(hcx - 1, my + 1, INDEX["m"], clip=face)
        c.pset(hcx, my + 1, INDEX["m"], clip=face)
        c.pset(hcx + 1, my, INDEX["m"], clip=face)

    if blush:
        if species == "jerry":
            for bx in (-6.0, 5.0):
                c.pset(hcx + bx, hcy + 5.0, INDEX["b"], clip=face)
                c.pset(hcx + bx + 1, hcy + 5.0, INDEX["b"], clip=face)
        elif species == "tom":
            for bx in (-6.4, 5.6):
                c.pset(hcx + bx, hcy + 5.0, INDEX["b"], clip=face)
                c.pset(hcx + bx + 1, hcy + 5.0, INDEX["b"], clip=face)
        else:
            for bx in (-7.0, 6.2):
                c.pset(hcx + bx, hcy + 5.0, INDEX["b"], clip=face)
                c.pset(hcx + bx + 1, hcy + 5.0, INDEX["b"], clip=face)

    # --- held item --------------------------------------------------------
    if banana:
        bx, by, ang, bite = banana
        pts = []
        n = 8
        for i in range(n + 1):
            t = i / n
            x = (1 - t) ** 2 * (-1.4) + 2 * (1 - t) * t * 4.4 + t * t * (-1.0)
            y = (1 - t) ** 2 * (-5.2) + 2 * (1 - t) * t * 0.0 + t * t * (5.2)
            ca, sa = math.cos(ang), math.sin(ang)
            pts.append((bx + x * ca - y * sa, by + x * sa + y * ca))
        if bite:
            pts = pts[: max(2, len(pts) - bite)]
        bm = m_polyline(pts, 1.7, 1.2)
        c.paint(bm, INDEX["y"])
        for p in (pts[0], pts[-1]):
            c.set(int(p[0]), int(p[1]), INDEX["Y"])

    # --- overlays ---------------------------------------------------------
    if zzz:
        for (zx, zy) in zzz:
            for (x, y), col in z_glyph(zx, zy, INDEX["z"]):
                c.set(x, y, col)
    if sparkles:
        sparkle(c, hcx - 15, hcy - 4, INDEX["p"])
        sparkle(c, hcx + 15, hcy - 8, INDEX["p"], big=True)
        sparkle(c, hcx + 13, hcy + 13, INDEX["p"])
    if shower is not None:
        draw_shower(c, shower)

    return c


# ------------------------------------------------------------- animations ---
def anim_idle(species="monkey"):
    out = []
    for i in range(8):
        breathe = (0, -1, -1, -1, 0, 0, 0, 0)[i]
        out.append(draw_monkey(
            body_dy=breathe, head_dy=breathe,
            eye="blink" if i == 5 else "open", mouth="smile",
            blush=(i in (2, 3)),
            hand_l=(9.6, 32.2 + breathe), hand_r=(30.4, 32.2 + breathe),
            tail_phase=i * 0.8, species=species))
    return out


def anim_walk(species="monkey"):
    frames = []
    bob = (0, -1, -1, 0, 0, -1)
    for i in range(6):
        b = bob[i]
        sw = math.sin(i / 6 * math.tau)
        fl = (15.5 - (1.8 if i in (1, 2) else 0), 37.6 - (1.6 if i in (0, 1) else 0))
        fr = (24.5 + (1.8 if i in (4, 5) else 0), 37.6 - (1.6 if i in (3, 4) else 0))
        frames.append(draw_monkey(
            body_dy=b, head_dy=b, eye="open", mouth="smile",
            hand_l=(9.4 - sw * 1.2, 31.8 + b + sw),
            hand_r=(30.6 + sw * 1.2, 31.8 + b - sw),
            foot_l=fl, foot_r=fr, tail_phase=i * 0.9, species=species))
    return frames


def anim_happy(species="monkey"):
    frames = []
    hop = (0, -2, -4, -4, -2, 0, 0, 0)
    for i in range(8):
        h = hop[i]
        frames.append(draw_monkey(
            body_dy=h, head_dy=h,
            eye="happy", mouth="grin" if h < 0 else "smile", blush=True,
            hand_l=(8.2, 18.5 + h), hand_r=(31.8, 18.5 + h),
            foot_l=(15.4, 37.6 + h), foot_r=(24.6, 37.6 + h),
            tail_phase=i * 1.4, shadow=(h == 0),
            sparkles=(i in (2, 3, 4, 5)), species=species))
    return frames


def anim_grab(species="monkey"):
    frames = []
    for i in range(4):
        sw = math.sin(i / 4 * math.tau)
        frames.append(draw_monkey(
            body_dy=1, head_dy=1, squash=-1.2, eye="wide", mouth="open",
            hand_l=(9.6 + sw * 0.8, 34.0), hand_r=(30.4 + sw * 0.8, 34.0),
            foot_l=(15.0 + sw * 1.6, 38.4), foot_r=(25.0 + sw * 1.6, 38.4),
            tail_phase=i * 1.1, shadow=False, ears=1, species=species))
    return frames


def anim_fall(species="monkey"):
    frames = []
    for i in range(4):
        sw = math.sin(i / 4 * math.tau)
        frames.append(draw_monkey(
            body_dy=1, squash=-0.8, eye="wide", mouth="open",
            hand_l=(7.6, 21.0 + sw * 1.8), hand_r=(32.4, 21.0 - sw * 1.8),
            foot_l=(13.2 + sw * 1.2, 36.4 - abs(sw) * 1.6),
            foot_r=(26.8 - sw * 1.2, 36.4 - abs(sw) * 1.6),
            tail_phase=i * 1.3, tail_lift=3.5, shadow=False, ears=1, species=species))
    return frames


def anim_land(species="monkey"):
    frames = []
    for i, sq in enumerate((2.2, 1.2, 0.4, 0.0)):
        frames.append(draw_monkey(
            body_dy=int(sq * 0.5), head_dy=-int(sq * 0.3), squash=sq,
            eye="blink" if i == 0 else "open", mouth="smile",
            hand_l=(9.4, 32.6), hand_r=(30.6, 32.6),
            foot_l=(14.6, 37.8), foot_r=(25.4, 37.8),
            tail_phase=i * 0.6, species=species))
    return frames


def anim_sleep(species="monkey"):
    frames = []
    for i in range(6):
        breathe = (0, -1, -1, 0, 0, 0)[i]
        zs = []
        if i >= 2:
            t = (i - 2) / 4.0
            zs.append((29 + int(t * 3), 11 - int(t * 4)))
        if i >= 4:
            t = (i - 4) / 2.0
            zs.append((34 + int(t * 1), 4 - int(t * 2)))
        frames.append(draw_monkey(
            body_dy=breathe, head_dy=2 + breathe, squash=0.8,
            eye="closed", mouth="flat",
            hand_l=(10.2, 34.4 + breathe), hand_r=(29.8, 34.4 + breathe),
            foot_l=(15.0, 37.4), foot_r=(25.0, 37.4),
            tail_phase=i * 0.5 - 1.0, tail_lift=-2.5,
            zzz=zs if zs else None, species=species))
    return frames


def anim_eat(species="monkey"):
    frames = []
    plan = [  # (bite, mouth, eye, hand_l)
        (0, "open", "open", (9.4, 31.8)),
        (0, "open", "open", (10.4, 29.6)),
        (1, "open", "open", (11.4, 26.6)),
        (2, "chew", "open", (12.4, 23.4)),
        (3, "chew", "open", (13.0, 21.6)),
        (4, "chew", "happy", (13.4, 22.0)),
        (5, "smile", "happy", (13.0, 23.4)),
        (6, "smile", "happy", (12.0, 27.4)),
    ]
    for i, (bite, mouth, eye, hl) in enumerate(plan):
        b = None
        if bite < 6:
            b = (hl[0] + 1.6, hl[1] - 1.0, -0.5, bite)
        frames.append(draw_monkey(
            body_dy=0, eye=eye, mouth=mouth, blush=bite >= 3,
            hand_l=hl, hand_r=(30.2, 32.0),
            tail_phase=i * 1.2, banana=b, species=species))
    return frames


def anim_shower(species="monkey"):
    frames = []
    for i in range(8):
        breathe = (0, -1, -1, 0, 0, 0, -1, -1)[i]
        scrub = math.sin(i / 8 * math.tau) * 1.4
        frames.append(draw_monkey(
            body_dy=breathe, head_dy=breathe,
            eye="closed" if i < 4 else "happy",
            mouth="smile", blush=True,
            hand_l=(11.4 - scrub, 31.6 + breathe),
            hand_r=(28.6 + scrub, 31.6 + breathe),
            tail_phase=i * 0.7, shadow=False,
            shower=i * 0.55, species=species))
    return frames


ANIMS = [
    ("idle", anim_idle, 8, True),
    ("walk", anim_walk, 10, True),
    ("happy", anim_happy, 12, False),
    ("grab", anim_grab, 8, True),
    ("fall", anim_fall, 12, True),
    ("land", anim_land, 14, False),
    ("sleep", anim_sleep, 4, True),
    ("eat", anim_eat, 9, False),
    ("shower", anim_shower, 12, True),
]


# ------------------------------------------------------------------ output ---
def premul_bgra(canvas):
    out = bytearray(W * H * 4)
    for i, ci in enumerate(canvas.px):
        r, g, b, a = PAL[ORDER[ci]]
        out[i * 4 + 0] = b * a // 255
        out[i * 4 + 1] = g * a // 255
        out[i * 4 + 2] = r * a // 255
        out[i * 4 + 3] = a
    return out


def write_atlas(path, frames, meta):
    blob = bytearray()
    blob += b"MPA1"
    blob += struct.pack("<III", W, H, len(frames))
    for f in frames:
        blob += premul_bgra(f)
    blob += struct.pack("<I", len(meta))
    for name, fps, loop, idxs in meta:
        nb = name.encode()
        blob += struct.pack("<I", len(nb)) + nb
        blob += struct.pack("<II", fps, 1 if loop else 0)
        blob += struct.pack("<I", len(idxs))
        blob += struct.pack("<" + "I" * len(idxs), *idxs)
    with open(path, "wb") as fh:
        fh.write(blob)
    return len(blob)


def write_png(path, canvases, scale=4, cols=8, pad=4):
    rows = (len(canvases) + cols - 1) // cols
    cw, ch = W * scale, H * scale
    iw = cols * (cw + pad) + pad
    ih = rows * (ch + pad) + pad
    out = bytearray()
    bg1, bg2 = (62, 62, 68), (48, 48, 54)
    for y in range(ih):
        out.append(0)
        for x in range(iw):
            r, g, b = bg1 if ((x // 8) + (y // 8)) % 2 else bg2
            out += bytes((r, g, b))
    stride = iw * 3 + 1
    for n, cv in enumerate(canvases):
        ox = pad + (n % cols) * (cw + pad)
        oy = pad + (n // cols) * (ch + pad)
        for y in range(ch):
            for x in range(cw):
                r, g, b, a = PAL[ORDER[cv.px[(y // scale) * W + (x // scale)]]]
                if a == 0:
                    continue
                base = (oy + y) * stride + 1 + (ox + x) * 3
                out[base + 0] = (r * a + out[base + 0] * (255 - a)) // 255
                out[base + 1] = (g * a + out[base + 1] * (255 - a)) // 255
                out[base + 2] = (b * a + out[base + 2] * (255 - a)) // 255

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", iw, ih, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(out), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    return iw, ih


def generate_for_species(species, out_atlas, out_sheet=None):
    global PAL, ORDER, INDEX
    orig_pal = PAL
    orig_order = ORDER
    orig_index = INDEX
    try:
        pal = PALETTES.get(species, PAL_MONKEY)
        PAL = pal
        ORDER = list(PAL.keys())
        INDEX = {c: i for i, c in enumerate(ORDER)}

        frames, meta = [], []
        for name, fn, fps, loop in ANIMS:
            fs = fn(species)
            meta.append((name, fps, loop, list(range(len(frames), len(frames) + len(fs)))))
            frames.extend(fs)

        size = write_atlas(out_atlas, frames, meta)
        if out_sheet:
            iw, ih = write_png(out_sheet, frames)
            print(f"{species}: {len(frames)} frames, atlas {size} bytes, sheet {iw}x{ih}")
        else:
            print(f"{species}: {len(frames)} frames, atlas {size} bytes -> {out_atlas}")
        return size
    finally:
        PAL = orig_pal
        ORDER = orig_order
        INDEX = orig_index


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "assets")
    os.makedirs(assets, exist_ok=True)

    # Generate all variants
    generate_for_species("monkey", os.path.join(assets, "monkey.atlas"), os.path.join(assets, "monkey-sheet.png"))
    generate_for_species("tom", os.path.join(assets, "tom.atlas"))
    generate_for_species("jerry", os.path.join(assets, "jerry.atlas"))
    # Also drop next to common locations for convenience
    try:
        import shutil
        for name in ["tom.atlas", "jerry.atlas"]:
            src = os.path.join(assets, name)
            for dst in [os.path.join(root, name), os.path.join(root, "installer", "dist", name)]:
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
    except Exception:
        pass

    if len(sys.argv) > 1:
        want = sys.argv[1]
        global PAL, ORDER, INDEX
        pal = PALETTES.get("monkey", PAL_MONKEY)
        PAL = pal
        ORDER = list(PAL.keys())
        INDEX = {c: i for i, c in enumerate(ORDER)}
        frames, meta = [], []
        for name, fn, fps, loop in ANIMS:
            fs = fn("monkey")
            meta.append((name, fps, loop, list(range(len(frames), len(frames) + len(fs)))))
            frames.extend(fs)
        for name, fps, loop, idxs in meta:
            if want in ("all", name):
                for k, i in enumerate(idxs):
                    print(f"--- {name} [{k}] ---")
                    for y in range(H):
                        print("".join(ORDER[frames[i].px[y * W + x]] for x in range(W)))


if __name__ == "__main__":
    main()
