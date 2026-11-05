# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#ascii fbx 7.x writer + human readable structure dumps
# write_ascii makes sdk style ascii (tests use it, also handy for interop)
# dump_structure writes a trimmed readable tree, the diagnostics panel uses it to peek inside fbx files


from __future__ import annotations

import base64

from .elements import FbxArray, FbxBlob, decode_name


def _ascii_string(raw, convert_names=True):
    if isinstance(raw, FbxBlob):
        raw = raw.tobytes()
    if convert_names and b"\x00\x01" in raw:
        name, cls = raw.split(b"\x00\x01", 1)
        raw = cls + b"::" + name
    return '"' + decode_name(raw).replace('"', "&quot;") + '"'


def _num(value, is_float):
    if is_float:
        v = float(value)
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        r = repr(v)
        return r[:-2] if r.endswith(".0") else r
    return str(int(value))


def _format_props(node, indent, array_limit, blob_mode):
    parts = []
    tail = []
    types = node.types
    for i, value in enumerate(node.props):
        t = chr(types[i]) if i < len(types) else "S"
        if t in "fdilb":
            arr = value if isinstance(value, FbxArray) else FbxArray.from_values(t, value)
            vals = arr.values()
            is_float = t in "fd"
            count = len(vals)
            if array_limit is not None and count > array_limit:
                shown = ",".join(_num(v, is_float) for v in vals[:array_limit])
                body = "%s,... (%d values)" % (shown, count)
            else:
                body = ",".join(_num(v, is_float) for v in vals)
            parts.append("*%d" % count)
            tail.append("%sa: %s\n%s} " % ("\t" * (indent + 1), body, "\t" * indent))
        elif t in "SR":
            if t == "R" or (node.name == b"Content"):
                data = value.tobytes() if isinstance(value, FbxBlob) else bytes(value)
                if blob_mode == "dump":
                    parts.append("<%d bytes>" % len(data))
                else:
                    b64 = base64.b64encode(data).decode("ascii")
                    parts.append("")  #fbx writes "Content: ," then the chunks, dont ask
                    chunks = [b64[j:j + 4096] for j in range(0, len(b64), 4096)] or [""]
                    parts.append(("\n%s," % ("\t" * (indent + 1))).join('"%s"' % c for c in chunks))
            else:
                parts.append(_ascii_string(value))
        elif t == "C":
            parts.append("Y" if value else "N")
        elif t in "FD":
            parts.append(_num(value, True))
        else:
            parts.append(_num(value, False))
    return parts, tail


def _write_node(write, node, indent, array_limit, blob_mode):
    parts, tail = _format_props(node, indent, array_limit, blob_mode)
    head = "%s%s: %s" % ("\t" * indent, decode_name(node.name), ", ".join(parts))
    children = node.children
    if tail:
        #array value looks like  Name: *N {  a: ...  }
        write(head + " {\n")
        write(tail[0])
        write("\n")
        return
    if children or not node.props:
        write(head + (" {\n" if parts else " {\n"))
        for child in children:
            _write_node(write, child, indent + 1, array_limit, blob_mode)
        write("%s}\n" % ("\t" * indent))
    else:
        write(head + "\n")


def write_ascii(stream, root, version=7400, creator="HJT FBX+"):
    #root (binary typed tree) -> ascii fbx text into stream
    major, minor, patch = version // 1000, (version // 100) % 10, version % 100
    stream.write("; FBX %d.%d.%d project file\n" % (major, minor, patch))
    stream.write("; Written by %s\n" % creator)
    stream.write("; ----------------------------------------------------\n\n")
    for child in root.children:
        _write_node(stream.write, child, 0, None, "base64")
        stream.write("\n")


def dump_structure(stream, root, array_limit=8, max_children=200):
    #readable dump, arrays get cut short n blobs just show their size
    def rec(node, indent):
        parts, tail = _format_props(node, indent, array_limit, "dump")
        line = "%s%s: %s" % ("  " * indent, decode_name(node.name), ", ".join(parts))
        if tail:
            line += "  " + tail[0].strip().replace("\n", " ")
        stream.write(line.rstrip() + "\n")
        children = node.children
        for i, child in enumerate(children):
            if i >= max_children:
                stream.write("%s... %d more children\n" % ("  " * (indent + 1), len(children) - i))
                break
            rec(child, indent + 1)
    for child in root.children:
        rec(child, 0)
