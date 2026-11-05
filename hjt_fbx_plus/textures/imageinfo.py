# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#reads JUST the header of an image to get size, alpha y/n n the block format
# way cheaper than letting blender decode a 4k png just to ask "does it have alpha"
# alpha = True / False / None (None = dunno, eg exr or dxt1 1 bit alpha maybe)

from __future__ import annotations

import struct

_DXGI = {71: "BC1", 72: "BC1", 74: "BC2", 75: "BC2", 77: "BC3", 78: "BC3", 80: "BC4", 81: "BC4",
         83: "BC5", 84: "BC5", 95: "BC6H", 96: "BC6H", 98: "BC7", 99: "BC7", 28: "RGBA8", 29: "RGBA8",
         87: "BGRA8", 88: "BGRX8", 61: "R8", 49: "RG8", 10: "RGBA16F", 2: "RGBA32F"}
_DXGI_ALPHA = {"BC2": True, "BC3": True, "BC7": None, "RGBA8": True, "BGRA8": True, "BC1": None,
               "BC4": False, "BC5": False, "BC6H": False, "BGRX8": False, "R8": False, "RG8": False,
               "RGBA16F": True, "RGBA32F": True}


def probe_bytes(head):
    #head = first ~64kb of the file
    try:
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return _png(head)
        if head[:4] == b"DDS ":
            return _dds(head)
        if head[:2] == b"\xff\xd8":
            return _jpeg(head)
        if head[:2] == b"BM":
            w, h = struct.unpack_from("<ii", head, 18)
            bpp = struct.unpack_from("<H", head, 28)[0]
            return {"format": "BMP", "width": w, "height": abs(h), "alpha": None if bpp == 32 else False}
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return _webp(head)
        if head[:4] == b"\x76\x2f\x31\x01":
            return {"format": "EXR", "width": 0, "height": 0, "alpha": None}
        if head[:2] in (b"II", b"MM"):
            return {"format": "TIFF", "width": 0, "height": 0, "alpha": None}
        if head[:4] == b"8BPS":
            h, w = struct.unpack_from(">II", head, 14)
            ch = struct.unpack_from(">H", head, 12)[0]
            return {"format": "PSD", "width": w, "height": h, "alpha": ch >= 4}
        if head[:10] in (b"#?RADIANCE", b"#?RGBE\n\n\n\n") or head[:2] == b"#?":
            return {"format": "HDR", "width": 0, "height": 0, "alpha": False}
        if len(head) >= 18 and head[2] in (1, 2, 3, 9, 10, 11):
            return _tga(head)
    except (struct.error, IndexError):
        pass
    return {"format": "?", "width": 0, "height": 0, "alpha": None}


def probe_file(path):
    try:
        with open(path, "rb") as fh:
            return probe_bytes(fh.read(65536))
    except OSError:
        return {"format": "?", "width": 0, "height": 0, "alpha": None}


def _png(b):
    w, h, depth, ctype = struct.unpack_from(">IIBB", b, 16)
    alpha = ctype in (4, 6)
    if not alpha:
        #palette / rgb pngs can still carry alpha in a tRNS chunk, go look before IDAT
        pos = 8
        while pos + 8 <= len(b):
            ln, typ = struct.unpack_from(">I4s", b, pos)
            if typ == b"tRNS":
                alpha = True
                break
            if typ in (b"IDAT", b"IEND"):
                break
            pos += 12 + ln
    return {"format": "PNG", "width": w, "height": h, "alpha": alpha, "bits": depth}


def _dds(b):
    h, w = struct.unpack_from("<II", b, 12)
    pf_flags = struct.unpack_from("<I", b, 80)[0]
    four = b[84:88]
    fmt = four.decode("ascii", "replace").strip("\x00") or "RGB"
    alpha = bool(pf_flags & 0x1)
    if four == b"DX10":
        dxgi = struct.unpack_from("<I", b, 128)[0]
        fmt = _DXGI.get(dxgi, "DXGI%d" % dxgi)
        alpha = _DXGI_ALPHA.get(fmt)
    elif four in (b"DXT2", b"DXT3", b"DXT4", b"DXT5"):
        alpha = True
    elif four == b"DXT1":
        alpha = None
    elif four in (b"ATI1", b"BC4U", b"BC4S"):
        fmt, alpha = "BC4", False
    elif four in (b"ATI2", b"BC5U", b"BC5S"):
        fmt, alpha = "BC5", False
    return {"format": "DDS-" + fmt, "width": w, "height": h, "alpha": alpha}


def _jpeg(b):
    pos = 2
    while pos + 9 < len(b):
        if b[pos] != 0xFF:
            pos += 1
            continue
        marker = b[pos + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack_from(">HH", b, pos + 5)
            return {"format": "JPEG", "width": w, "height": h, "alpha": False}
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        ln = struct.unpack_from(">H", b, pos + 2)[0]
        pos += 2 + ln
    return {"format": "JPEG", "width": 0, "height": 0, "alpha": False}


def _tga(b):
    w, h, depth, desc = struct.unpack_from("<HHBB", b, 12)
    alpha_bits = desc & 0x0F
    return {"format": "TGA", "width": w, "height": h, "alpha": bool(alpha_bits) or depth == 32}


def _webp(b):
    chunk = b[12:16]
    if chunk == b"VP8X":
        flags = b[20]
        w = 1 + (b[24] | b[25] << 8 | b[26] << 16)
        h = 1 + (b[27] | b[28] << 8 | b[29] << 16)
        return {"format": "WEBP", "width": w, "height": h, "alpha": bool(flags & 0x10)}
    if chunk == b"VP8L":
        bits = struct.unpack_from("<I", b, 21)[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return {"format": "WEBP", "width": w, "height": h, "alpha": bool((bits >> 28) & 1)}
    return {"format": "WEBP", "width": 0, "height": 0, "alpha": False}
