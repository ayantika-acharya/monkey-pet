#!/usr/bin/env python3
"""Grab the pet window off the screen and describe what is in it.

Used to verify rendering without opening an image viewer:
  python tools/capture.py [path.png]
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import zlib

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32

u32.FindWindowW.restype = wt.HWND
u32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u32.GetDC.restype = wt.HDC
u32.GetDC.argtypes = [wt.HWND]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleBitmap.restype = wt.HBITMAP
g32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g32.SelectObject.restype = wt.HGDIOBJ
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(hwnd):
    r = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    src = u32.GetDC(0)
    mem = g32.CreateCompatibleDC(src)
    bmp = g32.CreateCompatibleBitmap(src, w, h)
    g32.SelectObject(mem, bmp)
    g32.BitBlt(mem, 0, 0, w, h, src, r.left, r.top, 0x00CC0020)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    return (r.left, r.top, w, h), bytes(buf)


def write_png(path, w, h, bgra):
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            b, g, r = bgra[(y * w + x) * 4], bgra[(y * w + x) * 4 + 1], bgra[(y * w + x) * 4 + 2]
            rows += bytes((r, g, b))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def main():
    hwnd = u32.FindWindowW("MonkeyPetWindow", None)
    if not hwnd:
        print("window not found")
        return 1
    (x, y, w, h), bgra = grab(hwnd)
    print(f"window at {x},{y} size {w}x{h}")

    # count pixels matching sprite palette colours (they survive as-is at alpha=255)
    pal = {
        "outline": (38, 24, 15), "fur_dark": (92, 56, 32), "fur": (124, 80, 44),
        "fur_light": (160, 110, 64), "skin": (226, 179, 131), "muzzle": (243, 213, 176),
        "inner_ear": (205, 137, 107), "eye": (28, 19, 13), "banana": (247, 214, 74),
    }
    counts = {k: 0 for k in pal}
    other = 0
    for i in range(w * h):
        b, g, r = bgra[i * 4], bgra[i * 4 + 1], bgra[i * 4 + 2]
        hit = False
        for k, (pr, pg, pb) in pal.items():
            if abs(r - pr) <= 2 and abs(g - pg) <= 2 and abs(b - pb) <= 2:
                counts[k] += 1
                hit = True
                break
        if not hit:
            other += 1
    total = sum(counts.values())
    print("sprite pixels:", total, " background/other:", other)
    for k, v in counts.items():
        if v:
            print(f"  {k:10s} {v}")

    if len(sys.argv) > 1:
        write_png(sys.argv[1], w, h, bgra)
        print("wrote", sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
