# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#high level fbx doc: objects, connections, global settings
# binary + ascii, 6.x + 7.x, pure python no bpy


from __future__ import annotations

import os

from .elements import FbxError, FbxNode, decode_name, name_class_split
from . import binary as _binary
from . import ascii as _ascii

FBX_KTIME = 46186158000  #fbx time ticks per second. yes really, 46 billion, dont ask me

#nodes we dont need to understand a scene, parsed lazy only if someone pokes them
PREVIEW_LAZY_NODES = frozenset((b"AnimationCurve", b"Takes", b"References", b"Definitions"))
#prepare edits everything except the key arrays, those get copied through untouched (binary.py _raw)
PREPARE_LAZY_NODES = frozenset((b"AnimationCurve",))

#fbx TimeMode enum -> fps. 0 n 14 mean custom/default
TIME_MODE_FPS = {
    1: 120.0, 2: 100.0, 3: 60.0, 4: 50.0, 5: 48.0, 6: 30.0, 7: 30.0, 8: 29.97, 9: 29.97,
    10: 25.0, 11: 24.0, 12: 1000.0, 13: 23.976, 15: 96.0, 16: 72.0, 17: 59.94, 18: 119.88,
}


class PropValue:
    #one Properties70 / Properties60 entry

    __slots__ = ("name", "type", "label", "flags", "values")

    def __init__(self, name, ptype, label, flags, values):
        self.name = name
        self.type = ptype
        self.label = label
        self.flags = flags
        self.values = values

    @property
    def value(self):
        if not self.values:
            return None
        return self.values[0] if len(self.values) == 1 else tuple(self.values)

    @property
    def is_user(self):
        return b"U" in (self.flags or b"")


def read_props(node):
    #Properties70 (or the ancient Properties60) of node -> dict
    out = {}
    if node is None:
        return out
    p70 = node.find(b"Properties70")
    if p70 is not None:
        for p in p70.children:
            if p.name != b"P" or len(p.props) < 4:
                continue
            name = p.props[0]
            out[name] = PropValue(name, p.props[1], p.props[2], p.props[3], p.props[4:])
        return out
    p60 = node.find(b"Properties60")
    if p60 is not None:
        for p in p60.children:
            if p.name != b"Property" or len(p.props) < 3:
                continue
            name = p.props[0]
            out[name] = PropValue(name, p.props[1], b"", p.props[2], p.props[3:])
    return out


class FbxObject:
    #one thing from the Objects section

    __slots__ = ("uid", "cls", "name", "subtype", "node", "_props")

    def __init__(self, uid, cls, name, subtype, node):
        self.uid = uid
        self.cls = cls          #node name like b"Model" b"Geometry"
        self.name = name        # display name, str
        self.subtype = subtype  #b"Mesh" b"LimbNode" b"Skin" etc
        self.node = node
        self._props = None

    @property
    def props(self):
        if self._props is None:
            self._props = read_props(self.node)
        return self._props

    def prop(self, name, default=None):
        p = self.props.get(name)
        if p is None:
            return default
        v = p.value
        return default if v is None else v

    def __repr__(self):
        return "<FbxObject %s %r %s>" % (self.cls.decode(), self.name, self.subtype.decode("ascii", "replace"))


class Connection:
    __slots__ = ("kind", "child", "parent", "prop")

    def __init__(self, kind, child, parent, prop):
        self.kind = kind
        self.child = child
        self.parent = parent
        self.prop = prop


class FbxDocument:
    #parsed fbx + the object/connection graph

    def __init__(self, root, version, fmt, path=None, source=None, node_count=0):
        self.root = root
        self.version = version
        self.format = fmt  #"binary" or "ascii"
        self.path = path
        self._source = source
        self.node_count = node_count
        self.objects = {}
        self.by_class = {}
        self.connections = []
        self._parents = {}
        self._children = {}
        self.root_uid = 0
        self._build()

    @classmethod
    def open(cls, path, lazy=True, cancel=None, use_mmap=None, lazy_names=None):
        #opens binary or ascii. remember to close() it or the mmap hangs around
        with open(path, "rb") as fh:
            head = fh.read(4096)
        if _binary.is_binary_fbx(head):
            if lazy_names is None:
                lazy_names = PREVIEW_LAZY_NODES if lazy else None
            res = _binary.read_binary(path, lazy_names=lazy_names,
                                      use_mmap=use_mmap)
            return cls(res.root, res.version, "binary", path, res.source, res.node_count)
        if _ascii.is_ascii_fbx(head):
            res = _ascii.read_ascii(path, cancel=cancel)
            return cls(res.root, res.version, "ascii", path, None, res.node_count)
        raise FbxError("not an FBX file (unrecognised header)")

    @classmethod
    def from_bytes(cls, data, lazy=False):
        if _binary.is_binary_fbx(data[:64]):
            res = _binary.read_binary(data=data, lazy_names=PREVIEW_LAZY_NODES if lazy else None)
            return cls(res.root, res.version, "binary", None, res.source, res.node_count)
        res = _ascii.read_ascii(data=data)
        return cls(res.root, res.version, "ascii", None, None, res.node_count)

    def close(self):
        if self._source is not None:
            self._source.close()
            self._source = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def is_v6(self):
        return 0 < self.version < 7000

    def _build(self):
        objects_node = self.root.find(b"Objects")
        v6 = self.is_v6
        if objects_node is not None:
            for node in objects_node.children:
                props = node.props
                if not props:
                    continue
                if v6 or not isinstance(props[0], int):
                    raw = props[0] if isinstance(props[0], bytes) else b""
                    name, cls_name = name_class_split(raw)
                    uid = raw if raw else id(node)
                    subtype = props[1] if len(props) > 1 and isinstance(props[1], bytes) else b""
                else:
                    uid = props[0]
                    raw = props[1] if len(props) > 1 and isinstance(props[1], bytes) else b""
                    name, cls_name = name_class_split(raw)
                    subtype = props[2] if len(props) > 2 and isinstance(props[2], bytes) else b""
                obj = FbxObject(uid, node.name, decode_name(name), subtype, node)
                self.objects[uid] = obj
                self.by_class.setdefault(node.name, []).append(obj)
        conns = self.root.find(b"Connections")
        if conns is not None:
            for c in conns.children:
                props = c.props
                if c.name == b"C" and len(props) >= 3:
                    kind, child, parent = props[0], props[1], props[2]
                    prop = props[3] if len(props) > 3 else None
                elif c.name == b"Connect" and len(props) >= 3:
                    kind = props[0]
                    child = props[1]
                    parent = props[2]
                    prop = props[3] if len(props) > 3 else None
                    if v6:
                        child = _v6_uid(child)
                        parent = _v6_uid(parent)
                else:
                    continue
                conn = Connection(kind, child, parent, prop)
                self.connections.append(conn)
                self._parents.setdefault(child, []).append(conn)
                self._children.setdefault(parent, []).append(conn)
        if v6:
            self.root_uid = b"Scene\x00\x01Model"


    #graph queries
    def get(self, uid):
        return self.objects.get(uid)

    def objects_of(self, cls_name, subtype=None):
        objs = self.by_class.get(cls_name, ())
        if subtype is None:
            return list(objs)
        if isinstance(subtype, (set, frozenset, tuple, list)):
            return [o for o in objs if o.subtype in subtype]
        return [o for o in objs if o.subtype == subtype]

    def parents(self, uid, cls_name=None):
        out = []
        for c in self._parents.get(uid, ()):
            obj = self.objects.get(c.parent)
            if obj is not None and (cls_name is None or obj.cls == cls_name):
                out.append(obj)
        return out

    def parent_links(self, uid):
        return self._parents.get(uid, ())

    def children(self, uid, cls_name=None):
        out = []
        for c in self._children.get(uid, ()):
            obj = self.objects.get(c.child)
            if obj is not None and (cls_name is None or obj.cls == cls_name):
                out.append(obj)
        return out

    def child_links(self, uid):
        return self._children.get(uid, ())

    def model_parent(self, uid):
        #parent Model of a Model, None if its parented to the scene root
        for c in self._parents.get(uid, ()):
            if c.kind == b"OO":
                obj = self.objects.get(c.parent)
                if obj is not None and obj.cls == b"Model":
                    return obj
        return None

    #doc level stuff

    def global_settings(self):
        gs = self.root.find(b"GlobalSettings")
        if gs is None and self.is_v6:
            objs = self.root.find(b"Objects")
            gs = objs.find(b"GlobalSettings") if objs is not None else None
        props = read_props(gs)
        out = {}
        for key in (b"UpAxis", b"UpAxisSign", b"FrontAxis", b"FrontAxisSign", b"CoordAxis",
                    b"CoordAxisSign", b"OriginalUpAxis", b"OriginalUpAxisSign", b"UnitScaleFactor",
                    b"OriginalUnitScaleFactor", b"TimeMode", b"CustomFrameRate", b"TimeSpanStart",
                    b"TimeSpanStop", b"DefaultCamera"):
            p = props.get(key)
            if p is not None:
                out[key.decode()] = p.value
        return out

    def header_info(self):
        info = {}
        hdr = self.root.find(b"FBXHeaderExtension")
        if hdr is not None:
            creator = hdr.value(b"Creator")
            if creator:
                info["creator"] = decode_name(creator)
            scene_info = hdr.find(b"SceneInfo")
            if scene_info is not None:
                props = read_props(scene_info)
                for key, label in ((b"Original|ApplicationVendor", "vendor"),
                                   (b"Original|ApplicationName", "application"),
                                   (b"Original|ApplicationVersion", "application_version"),
                                   (b"LastSaved|ApplicationName", "last_saved_by")):
                    p = props.get(key)
                    if p is not None and p.value:
                        info[label] = decode_name(p.value)
            ts = hdr.find(b"CreationTimeStamp")
            if ts is not None:
                try:
                    info["created"] = "%04d-%02d-%02d %02d:%02d:%02d" % tuple(
                        int(ts.value(k, 0, 0)) for k in (b"Year", b"Month", b"Day", b"Hour", b"Minute", b"Second"))
                except (TypeError, ValueError):
                    pass
        top_creator = self.root.value(b"Creator")
        if top_creator and "creator" not in info:
            info["creator"] = decode_name(top_creator)
        return info

    def frame_rate(self):
        gs = self.global_settings()
        mode = gs.get("TimeMode")
        if mode in (0, 14) or mode is None:
            fps = gs.get("CustomFrameRate")
            if isinstance(fps, (int, float)) and fps > 0:
                return float(fps)
            return TIME_MODE_FPS.get(mode, 24.0) if mode not in (None, 0, 14) else 24.0
        return TIME_MODE_FPS.get(mode, 24.0)


def _v6_uid(raw):
    #fbx 6 connections use Class::Name instead of ids, boomer format
    if isinstance(raw, bytes):
        if b"\x00\x01" in raw:
            return raw
        name, cls_name = name_class_split(raw)
        if cls_name:
            return name + b"\x00\x01" + cls_name
    return raw


def sniff_fbx(path, size_limit=None):
    #cheap header sniff -> (kind, version), kind is binary ascii or None
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return None, 0
    if _binary.is_binary_fbx(head):
        import struct
        if len(head) >= 27:
            return "binary", struct.unpack_from("<I", head, 23)[0]
        return "binary", 0
    if _ascii.is_ascii_fbx(head):
        return "ascii", _ascii.detect_version(head)
    return None, 0


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
