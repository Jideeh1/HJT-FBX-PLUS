# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#.uemodel reader (UEFormat, the thing fmodel exports). no bpy so it runs on the worker thread
# layouts:
#   v0-3   flat sections, one lod, bones next to the verts
#   v4-9   "LODS"/"SKELETON"/"COLLISION" sections w name + array size + byte size headers
#   v10+   attribute blocks: count, then name + byte size per block
#v8+ also switched to raw ue space (left handed, Y mirrored) so we flip Y there. everything leaves
# this file in blender space n meters
#numpy frombuffer everywhere. the per-float struct.unpack way is like 6s for one body mesh, ewan ko ba

from __future__ import annotations

import gzip
import struct

import numpy as np

MAGIC = b"UEFORMAT"
LATEST = 10
V_BINORMAL_SIGN = 1
V_MULTI_COLORS = 2
V_LOD_RESTRUCTURE = 4
V_MATERIAL_PATH = 6
V_ORIGINAL_TRANSFORMS = 8
V_ATTRIBUTES = 10

_W_NEW = np.dtype([("bone", "<u2"), ("vert", "<i4"), ("w", "<f4")])
_W_OLD = np.dtype([("bone", "<i2"), ("vert", "<i4"), ("w", "<f4")])
_DELTA = np.dtype([("pos", "<f4", 3), ("nrm", "<f4", 3), ("vert", "<i4")])


class UEFormatError(ValueError):
    pass


class UELod:
    __slots__ = ("name", "verts", "normals", "uvs", "indices", "colors", "materials", "weights", "morphs")

    def __init__(self, name):
        self.name = name
        self.verts = np.zeros((0, 3), np.float32)
        self.normals = None
        self.uvs = []        #[(name, (N,2))]
        self.indices = np.zeros((0, 3), np.int32)
        self.colors = []     #[(name, (N,4) uint8)]
        self.materials = []  #[(name, path, first_index, num_faces)]
        self.weights = None  #structured: bone, vert, w
        self.morphs = []     #[(name, vert idx, (K,3) position deltas)]


class UEBone:
    __slots__ = ("name", "parent", "pos", "rot", "scale")

    def __init__(self, name, parent, pos, rot, scale):
        self.name = name
        self.parent = parent
        self.pos = pos
        self.rot = rot      #x y z w
        self.scale = scale


class UEModel:
    def __init__(self):
        self.identifier = ""
        self.version = 0
        self.name = ""
        self.object_path = ""
        self.skeleton_path = ""
        self.lods = []
        self.bones = []
        self.sockets = []   #[(name, parent name, pos, rot)]
        self.collisions = []
        self.compression = ""

    @property
    def mirrored(self):
        return self.version >= V_ORIGINAL_TRANSFORMS


class _R:
    __slots__ = ("b", "o", "end", "v")

    def __init__(self, buf, version, start=0, end=None):
        self.b = buf
        self.o = start
        self.end = len(buf) if end is None else end
        self.v = version

    def eof(self):
        return self.o >= self.end

    def _need(self, n):
        if self.o + n > self.end:
            raise UEFormatError("file is truncated or not a model (wanted %d bytes at %d)" % (n, self.o))

    def i32(self):
        self._need(4)
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        self._need(1)
        v = self.b[self.o]
        self.o += 1
        return v

    def fstring(self):
        n = self.i32()
        if n < 0 or n > 1 << 20:
            raise UEFormatError("bad string length %d at %d" % (n, self.o - 4))
        self._need(n)
        s = bytes(self.b[self.o:self.o + n])
        self.o += n
        return s.rstrip(b"\x00").decode("utf-8", "replace")

    def arr(self, dtype, count):
        dtype = np.dtype(dtype)
        if count < 0:
            raise UEFormatError("negative array size")
        self._need(dtype.itemsize * count)
        a = np.frombuffer(self.b, dtype=dtype, count=count, offset=self.o)
        self.o += dtype.itemsize * count
        return a

    def floats(self, count):
        return self.arr("<f4", count)

    def sub(self, size):
        self._need(size)
        r = _R(self.b, self.v, self.o, self.o + size)
        self.o += size
        return r

    def skip_to(self, pos):
        self.o = pos


def _decompress(kind, payload, size):
    if kind == "GZIP":
        return gzip.decompress(payload)
    if kind == "ZSTD":
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(payload, max_output_size=max(size, 0) or 0)
        except ImportError:
            pass
        try:
            from compression import zstd  # python 3.14+
            return zstd.decompress(payload)
        except ImportError:
            raise UEFormatError("this .uemodel is ZSTD compressed and the 'zstandard' Python package is missing "
                                "(Blender ships it since 3.0; reinstall Blender or pip install zstandard)") from None
    raise UEFormatError("unknown compression '%s'" % kind)


def read_uemodel(data):
    #bytes -> UEModel in blender space (meters, Z up, mirrored right)
    if bytes(data[:8]) != MAGIC:
        raise UEFormatError("not a UEFormat file")
    hdr = _R(data, 0, 8)
    m = UEModel()
    m.identifier = hdr.fstring()
    m.version = hdr.u8()
    if m.version > LATEST:
        raise UEFormatError("UEFormat version %d is newer than this add-on understands (max %d)" % (m.version, LATEST))
    m.name = hdr.fstring()
    if m.version >= V_ATTRIBUTES:
        m.object_path = hdr.fstring()
    if m.identifier != "UEMODEL":
        raise UEFormatError("%s files are animations/poses, not models" % m.identifier)
    if hdr.u8():
        kind = hdr.fstring()
        usize = hdr.i32()
        hdr.i32()
        m.compression = kind
        body = _R(_decompress(kind, bytes(data[hdr.o:]), usize), m.version)
    else:
        body = _R(data, m.version, hdr.o)
    if m.version >= V_ATTRIBUTES:
        _model_attrs(body, m)
    elif m.version >= V_LOD_RESTRUCTURE:
        _model_sections(body, m)
    else:
        _model_flat(body, m)
    _to_blender(m)
    return m


#v10+

def _attrs(r):
    for _ in range(r.i32()):
        name = r.fstring()
        size = r.i32()
        yield name, r.sub(size)


def _model_attrs(r, m):
    for name, a in _attrs(r):
        if name == "LODS":
            m.lods = [_lod_attrs(a) for _ in range(a.i32())]
        elif name == "SKELETON":
            _skeleton_attrs(a, m)
        elif name == "COLLISION":
            m.collisions = [_collision(a) for _ in range(a.i32())]


def _lod_attrs(r):
    lod = UELod(r.fstring())
    for name, a in _attrs(r):
        if name == "VERTICES":
            n = a.i32()
            lod.verts = a.floats(n * 3).reshape(n, 3)
        elif name == "NORMALS":
            n = a.i32()
            lod.normals = a.floats(n * 4).reshape(n, 4)[:, 1:]
        elif name == "TEXCOORDS":
            for _ in range(a.i32()):
                uvname = a.fstring()
                n = a.i32()
                lod.uvs.append((uvname, a.floats(n * 2).reshape(n, 2)))
        elif name == "INDICES":
            n = a.i32()
            lod.indices = a.arr("<u4", n).astype(np.int32).reshape(-1, 3)
        elif name == "VERTEXCOLORS":
            lod.colors = [_color(a) for _ in range(a.i32())]
        elif name == "MATERIALS":
            lod.materials = [(a.fstring(), a.fstring(), a.i32(), a.i32()) for _ in range(a.i32())]
        elif name == "WEIGHTS":
            lod.weights = a.arr(_W_NEW, a.i32())
        elif name == "MORPHTARGETS":
            lod.morphs = [_morph(a) for _ in range(a.i32())]
    return lod


def _skeleton_attrs(r, m):
    for name, a in _attrs(r):
        if name == "METADATA":
            m.skeleton_path = a.fstring()
        elif name == "BONES":
            m.bones = [_bone(a, True) for _ in range(a.i32())]
        elif name == "SOCKETS":
            m.sockets = [_socket(a) for _ in range(a.i32())]


#v4-9

def _sections(r):
    while not r.eof():
        name = r.fstring()
        count = r.i32()
        size = r.i32()
        start = r.o
        yield name, count, r.sub(size)
        r.skip_to(start + size)


def _model_sections(r, m):
    while not r.eof():
        name = r.fstring()
        count = r.i32()
        size = r.i32()
        start = r.o
        if name == "LODS":
            m.lods = [_lod_sections(r) for _ in range(count)]
        elif name == "SKELETON":
            s = r.sub(size)
            for sname, scount, a in _sections(s):
                if sname == "METADATA":
                    m.skeleton_path = a.fstring()
                elif sname == "BONES":
                    m.bones = [_bone(a, False) for _ in range(scount)]
                elif sname == "SOCKETS":
                    m.sockets = [_socket(a) for _ in range(scount)]
        elif name == "COLLISION":
            m.collisions = [_collision(r) for _ in range(count)]
        if name != "LODS":
            r.skip_to(start + size)


def _lod_sections(r):
    lod = UELod(r.fstring())
    body = r.sub(r.i32())
    for name, count, a in _sections(body):
        _lod_section(lod, name, count, a)
    return lod


def _lod_section(lod, name, count, a):
    if name == "VERTICES":
        lod.verts = a.floats(count * 3).reshape(count, 3)
    elif name == "INDICES":
        lod.indices = a.arr("<u4", count).astype(np.int32).reshape(-1, 3)
    elif name == "NORMALS":
        if a.v >= V_BINORMAL_SIGN:
            lod.normals = a.floats(count * 4).reshape(count, 4)[:, 1:]
        else:
            lod.normals = a.floats(count * 3).reshape(count, 3)
    elif name == "VERTEXCOLORS":
        if a.v >= V_MULTI_COLORS:
            lod.colors = [_color(a) for _ in range(count)]
        else:
            lod.colors = [("COL0", a.arr("u1", count * 4).reshape(count, 4))]
    elif name == "TEXCOORDS":
        for i in range(count):
            n = a.i32()
            lod.uvs.append(("UV%d" % i, a.floats(n * 2).reshape(n, 2)))
    elif name == "MATERIALS":
        out = []
        for _ in range(count):
            nm = a.fstring()
            path = a.fstring() if a.v >= V_MATERIAL_PATH else ""
            out.append((nm, path, a.i32(), a.i32()))
        lod.materials = out
    elif name == "WEIGHTS":
        lod.weights = a.arr(_W_OLD, count)
    elif name == "MORPHTARGETS":
        lod.morphs = [_morph(a) for _ in range(count)]


#v0-3, everything in one flat list

def _model_flat(r, m):
    lod = UELod("LOD0")
    for name, count, a in _sections(r):
        if name == "BONES":
            m.bones = [_bone(a, False) for _ in range(count)]
        elif name == "SOCKETS":
            m.sockets = [_socket(a) for _ in range(count)]
        elif name == "COLLISION":
            m.collisions = [_collision(a) for _ in range(count)]
        else:
            _lod_section(lod, name, count, a)
    m.lods = [lod]


#shared bits

def _color(a):
    name = a.fstring()
    n = a.i32()
    return name, a.arr("u1", n * 4).reshape(n, 4)


def _morph(a):
    name = a.fstring()
    d = a.arr(_DELTA, a.i32())
    return name, d["vert"].astype(np.int64), np.array(d["pos"], dtype=np.float32)


def _bone(a, has_scale):
    name = a.fstring()
    parent = a.i32()
    pos = np.array(a.floats(3), dtype=np.float64)
    rot = np.array(a.floats(4), dtype=np.float64)
    scale = np.array(a.floats(3), dtype=np.float64) if has_scale else np.ones(3)
    return UEBone(name, parent, pos, rot, scale)


def _socket(a):
    name = a.fstring()
    parent = a.fstring()
    pos = np.array(a.floats(3), dtype=np.float64)
    rot = np.array(a.floats(4), dtype=np.float64)
    a.floats(3)
    return name, parent, pos, rot


def _collision(a):
    name = a.fstring()
    n = a.i32()
    verts = a.floats(n * 3).reshape(n, 3)
    k = a.i32()
    return name, verts, a.arr("<u4", k).astype(np.int32).reshape(-1, 3)


def _to_blender(m, scale=0.01):
    flip = m.mirrored
    mirror = np.array((1.0, -1.0, 1.0), dtype=np.float32)
    for lod in m.lods:
        v = lod.verts.astype(np.float32) * scale
        lod.verts = v * mirror if flip else v
        if lod.normals is not None:
            n = lod.normals.astype(np.float32)
            lod.normals = n * mirror if flip else n
        if flip:
            lod.uvs = [(nm, np.column_stack((uv[:, 0], 1.0 - uv[:, 1])).astype(np.float32)) for nm, uv in lod.uvs]
        morphs = []
        for name, idx, pos in lod.morphs:
            p = pos * scale
            morphs.append((name, idx, p * mirror if flip else p))
        lod.morphs = morphs
    qflip = np.array((1.0, -1.0, 1.0, -1.0))
    for b in m.bones:
        b.pos = b.pos * scale * (mirror if flip else 1.0)
        if flip:
            b.rot = b.rot * qflip
    socks = []
    for name, parent, pos, rot in m.sockets:
        socks.append((name, parent, pos * scale * (mirror if flip else 1.0), rot * qflip if flip else rot))
    m.sockets = socks
    m.collisions = [(n, (v * scale * (mirror if flip else 1.0)).astype(np.float32), i) for n, v, i in m.collisions]


def summarize(parts, label=""):
    #parts = [(part label, UEModel)] -> same shape as fbx.inspect_document so profiles,
    # texture planning n the report dont care what file type it was
    mats = {}
    meshes = []
    bones = []
    seen_bones = set()
    verts = tris = morphs = 0
    for plabel, m in parts:
        lod = m.lods[0] if m.lods else None
        if lod is not None:
            obj = plabel or m.name
            meshes.append(obj)
            verts += len(lod.verts)
            tris += len(lod.indices)
            morphs += len(lod.morphs)
            for name, _path, _first, _n in lod.materials:
                mats.setdefault(name, {"name": name, "uid": "ue:" + name, "users": [], "props": {}, "textures": []})
                if obj not in mats[name]["users"]:
                    mats[name]["users"].append(obj)
        for b in m.bones:
            if b.name not in seen_bones:
                seen_bones.add(b.name)
                bones.append(b.name)
    issues = []
    for plabel, m in parts:
        if len(m.lods) > 1:
            issues.append({"level": "info", "code": "lods", "message": "%s has %d LODs; LOD0 is imported." % (plabel or m.name, len(m.lods))})
        if m.lods and not len(m.lods[0].verts):
            issues.append({"level": "warning", "code": "empty", "message": "%s has no geometry." % (plabel or m.name)})
    first = parts[0][1] if parts else None
    return {
        "format": "uemodel",
        "version": first.version if first else 0,
        "header": {"creator": "UEFormat", "object_path": first.object_path if first else ""},
        "global_settings": {"UnitScaleFactor": 1.0},
        "materials": list(mats.values()),
        "material_names": list(mats),
        "object_names": meshes + ([label] if label else []),
        "bone_names": bones,
        "texture_files": [],
        "issues": issues,
        "counts": {"meshes": len(meshes), "materials": len(mats), "bones": len(bones), "vertices": verts,
                   "triangles": tris, "blend_shapes": morphs},
    }
