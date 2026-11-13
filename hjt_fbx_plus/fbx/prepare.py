# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#makes an "import ready" binary copy of an fbx before blender ever sees it
#
# what it does (all optional, all counted in the result so diagnostics can show it):
#  - ascii 7.x -> binary (blenders python importer only eats binary)
#  - stamps a hidden user prop w the fbx uid on every model/geometry/material/video/attribute
#    so after import we know EXACTLY which blender thing came from which fbx thing. no name guessing
#  - fixes props that would trip the importers asserts (it asserts on labels too, insane)
#  - splits in-between shapes into their own channels, the python importer straight up
#    raises RuntimeError on those n kills the whole import lmao
#  - pads missing FullWeights, adds missing GlobalSettings/Objects/Connections
#  - can repoint texture paths to files we found ourselves
#
#the input doc gets modified in place, its still fine to read afterwards

from __future__ import annotations

import array
import os

from .elements import FbxArray, FbxError, FbxNode, decode_name, name_class_split
from .document import PREPARE_LAZY_NODES, FbxDocument
from . import binary as _binary

UID_PROP = "hjt_fbx_uid"
UID_PROP_B = UID_PROP.encode()

#classes that turn into blender datablocks w custom props (checked in io_scene_fbx 3.6 -> 5.2)
_UID_CLASSES = frozenset((b"Model", b"Geometry", b"Material", b"Video", b"NodeAttribute"))

#canonical (label, value types) per declared P type, exactly what the importer asserts
_CANON_STD = {
    b"Color": (b"", "DDD"),
    b"ColorRGB": (b"Color", "DDD"),
    b"Vector3D": (None, "DDD"),
    b"Vector": (None, "DDD"),
    b"Lcl Translation": (None, "DDD"),
    b"Lcl Rotation": (None, "DDD"),
    b"Lcl Scaling": (None, "DDD"),
    b"double": (b"Number", "D"),
    b"Number": (b"", "D"),
    b"int": (b"Integer", "I"),
    b"bool": (b"", "I"),
    b"Bool": (b"", "I"),
    b"enum": (b"", "I"),
    b"Visibility": (b"", "D"),
    b"KTime": (None, "L"),
}
#what blen_read_custom_properties asserts for user (U) props
_CANON_USER = {
    b"Vector": "DDD", b"Vector3D": "DDD", b"Color": "DDD", b"ColorRGB": "DDD",
    b"Vector4": "DDDD", b"ColorRGBA": "DDDD", b"Vector2D": "DD",
    b"Integer": "I", b"int": "I", b"KString": "S", b"Number": "D", b"double": "D",
    b"Double": "D", b"Float": "F", b"float": "F", b"Bool": "I", b"bool": "I",
    b"Enum": "IS", b"enum": "IS",
}


class PrepareOptions:
    def __init__(self, inject_uids=True, sanitize=True, split_inbetweens=True,
                 strip_user_props=False, texture_paths=None, uid_prop=UID_PROP):
        self.inject_uids = inject_uids
        self.sanitize = sanitize
        self.split_inbetweens = split_inbetweens
        self.strip_user_props = strip_user_props  #user turned custom props off but we still need our uid ones
        self.texture_paths = texture_paths or {}  #texture/video uid str -> new absolute path
        self.uid_prop = uid_prop


class PrepareResult:
    def __init__(self, path):
        self.path = path
        self.uid_count = 0
        self.fixes = {}
        self.inbetweens = []
        self.converted_from_ascii = False
        self.version = 0

    def fix(self, code, n=1):
        if n:
            self.fixes[code] = self.fixes.get(code, 0) + n

    def as_dict(self):
        return {"path": self.path, "uid_count": self.uid_count, "fixes": dict(self.fixes),
                "inbetweens": self.inbetweens, "converted_from_ascii": self.converted_from_ascii,
                "version": self.version}


def uid_key(uid):
    #the string we stamp into blender. ints for 7.x, Class::Name for 6.x
    if isinstance(uid, bytes):
        name, cls = name_class_split(uid)
        return decode_name(cls) + "::" + decode_name(name)
    return str(uid)



def _p70(node, create=True):
    p = node.find(b"Properties70")
    if p is None and create:
        p = FbxNode(b"Properties70")
        #put it right after Version if theres one, not that blender cares
        kids = node.children
        idx = 0
        for i, c in enumerate(kids):
            if c.name in (b"Version", b"GeometryVersion", b"ShadingModel", b"MultiLayer", b"TypeFlags", b"Type"):
                idx = i + 1
        kids.insert(idx, p)
    return p


def _user_prop_node(name, value):
    return FbxNode(b"P", [name, b"KString", b"", b"U", value], b"SSSSS")


def _num_convert(value, code):
    if code == "D" or code == "F":
        return float(value)
    if code in ("I", "L"):
        return int(round(value)) if isinstance(value, float) else int(value)
    return value


def _fix_values(p, want, start=4):
    #coerce p.props[start:] to the type codes in want. returns True if anything changed
    props = p.props
    types = bytearray(p.types)
    vals = props[start:]
    changed = False
    if len(vals) < len(want):
        #pad missing numbers w zeros, missing strings w empty
        for code in want[len(vals):]:
            props.append(b"" if code == "S" else (0.0 if code in "DF" else 0))
            types.append(ord(code))
        changed = True
    for i, code in enumerate(want):
        j = start + i
        t = chr(types[j])
        if t == code:
            continue
        v = props[j]
        if code in "DFIL" and t in "DFILYC" and isinstance(v, (int, float)):
            props[j] = _num_convert(v, code)
            types[j] = ord(code)
            changed = True
        elif code == "S" and isinstance(v, (bytes, bytearray)):
            types[j] = ord("S")
            changed = True
        else:
            return None  #cant convert this, caller decides what to do
    if changed:
        p.types = bytes(types)
    return changed


def _sanitize_p70(p70, result, strip_user):
    kids = p70.children
    keep = []
    for p in kids:
        if p.name != b"P" or len(p.props) < 4:
            keep.append(p)
            continue
        types = p.types
        #first 4 must be strings or the importer chokes
        if any(chr(types[i]) != "S" for i in range(4)):
            if all(isinstance(p.props[i], (bytes, bytearray)) for i in range(4)):
                p.types = b"SSSS" + bytes(types[4:])
            else:
                result.fix("dropped_bad_property")
                continue
        ptype = p.props[1]
        flags = p.props[3]
        is_user = b"U" in flags
        if is_user:
            if strip_user and p.props[0] != UID_PROP_B:
                result.fix("stripped_user_property")
                continue
            want = _CANON_USER.get(ptype)
            if want is not None:
                ok = _fix_values(p, want)
                if ok is None:
                    #retype as a string so it survives instead of exploding
                    v = p.props[4] if len(p.props) > 4 else b""
                    sval = v if isinstance(v, (bytes, bytearray)) else str(v).encode()
                    p.props = [p.props[0], b"KString", p.props[2], flags, bytes(sval)]
                    p.types = b"SSSSS"
                    result.fix("retyped_user_property")
                elif ok:
                    result.fix("fixed_user_property")
            keep.append(p)
            continue
        canon = _CANON_STD.get(ptype)
        if canon is None:
            keep.append(p)
            continue
        label, want = canon
        changed = False
        if label is not None and p.props[2] != label:
            p.props[2] = label
            changed = True
        if ptype == b"enum" and p.props[3] != b"":
            p.props[3] = b""
            changed = True
        ok = _fix_values(p, want)
        if ok is None:
            result.fix("dropped_bad_property")
            continue
        if ptype in (b"bool", b"Bool") and len(p.props) > 4 and p.props[4] not in (0, 1):
            p.props[4] = 1 if p.props[4] else 0
            changed = True
        if changed or ok:
            result.fix("fixed_property")
        keep.append(p)
    if len(keep) != len(kids):
        p70.children = keep


def _sanitize_tree(root, result, strip_user):
    #every Properties70 in Objects, GlobalSettings n Definitions templates (templates get asserted too, ugh)
    for top in (root.find(b"Objects"), root.find(b"GlobalSettings"), root.find(b"Definitions")):
        if top is None:
            continue
        for node in top.walk(load=False):
            if node.name == b"Properties70":
                _sanitize_p70(node, result, strip_user)


def _ensure_sections(root, result, version):
    names = [c.name for c in root.children]
    if b"GlobalSettings" not in names:
        gs = FbxNode(b"GlobalSettings", [], b"", [
            FbxNode(b"Version", [1000], b"I"),
            FbxNode(b"Properties70", [], b"", [
                FbxNode(b"P", [b"UpAxis", b"int", b"Integer", b"", 1], b"SSSSI"),
                FbxNode(b"P", [b"UpAxisSign", b"int", b"Integer", b"", 1], b"SSSSI"),
                FbxNode(b"P", [b"FrontAxis", b"int", b"Integer", b"", 2], b"SSSSI"),
                FbxNode(b"P", [b"FrontAxisSign", b"int", b"Integer", b"", 1], b"SSSSI"),
                FbxNode(b"P", [b"CoordAxis", b"int", b"Integer", b"", 0], b"SSSSI"),
                FbxNode(b"P", [b"CoordAxisSign", b"int", b"Integer", b"", 1], b"SSSSI"),
                FbxNode(b"P", [b"UnitScaleFactor", b"double", b"Number", b"", 1.0], b"SSSSD"),
                FbxNode(b"P", [b"OriginalUnitScaleFactor", b"double", b"Number", b"", 1.0], b"SSSSD"),
            ]),
        ])
        idx = names.index(b"Documents") if b"Documents" in names else min(len(names), 1)
        root.children.insert(idx, gs)
        result.fix("added_global_settings")
    if b"Objects" not in names:
        root.children.append(FbxNode(b"Objects"))
        result.fix("added_objects")
    if b"Connections" not in [c.name for c in root.children]:
        root.children.append(FbxNode(b"Connections"))
        result.fix("added_connections")
    takes = root.find(b"Takes")
    if takes is not None and not takes.children:
        #a totally empty Takes node makes blenders c++ (ufbx) importer bail w "Failed to load".
        # the sdk always writes Current: "" in there, trident exports (afk journey rips) dont. fixed
        takes.children = [FbxNode(b"Current", [b""], b"S")]
        result.fix("filled_empty_takes")


def _set_string_child(node, name, value):
    child = node.find(name)
    if child is None:
        node.children.append(FbxNode(name, [value], b"S"))
    else:
        child.props = [value]
        child.types = b"S"


def _rewrite_texture_paths(doc, mapping, result):
    if not mapping:
        return
    for obj in doc.objects_of(b"Texture") + doc.objects_of(b"Video"):
        new_path = mapping.get(uid_key(obj.uid))
        if not new_path:
            continue
        raw = os.fsencode(new_path) if not isinstance(new_path, bytes) else new_path
        if obj.cls == b"Texture":
            _set_string_child(obj.node, b"FileName", raw)
            _set_string_child(obj.node, b"RelativeFilename", raw)
        else:
            _set_string_child(obj.node, b"Filename", raw)
            _set_string_child(obj.node, b"RelativeFilename", raw)
        result.fix("texture_path_rewritten")


def _fmt_weight(w):
    s = ("%.3f" % w).rstrip("0").rstrip(".")
    return s or "0"


def _split_inbetweens(doc, result):
    #ok this is the nasty one. fbx lets a BlendShapeChannel have several Shapes (in-betweens)
    # at diff FullWeights. blenders python importer raises on that n the whole import dies
    #so: keep the last (100%) shape on the channel, move every in-between onto a brand new
    # channel of its own. data survives, post-process can rebuild the blending w drivers later
    objects_node = doc.root.find(b"Objects")
    conns_node = doc.root.find(b"Connections")
    if objects_node is None or conns_node is None or doc.is_v6:
        return
    channels = doc.objects_of(b"Deformer", b"BlendShapeChannel")
    if not channels:
        return
    next_uid = max([o.uid for o in doc.objects.values() if isinstance(o.uid, int)] + [0]) + 1
    remove_links = set()
    new_conns = []
    for ch in channels:
        shapes = doc.children(ch.uid, b"Geometry")
        if len(shapes) <= 1:
            continue
        fw_node = ch.node.find(b"FullWeights")
        weights = []
        if fw_node is not None and fw_node.props and isinstance(fw_node.props[0], FbxArray):
            weights = list(fw_node.props[0].values())
        if len(weights) < len(shapes):
            n = len(shapes)
            weights = [100.0 * (i + 1) / n for i in range(n)]
        pairs = sorted(zip(weights[:len(shapes)], range(len(shapes))), key=lambda t: t[0])
        ordered = [(w, shapes[i]) for w, i in pairs]
        main_w, main_shape = ordered[-1]
        blendshapes = doc.parents(ch.uid, b"Deformer")
        geoms = []
        for bs in blendshapes:
            geoms.extend(g for g in doc.parents(bs.uid, b"Geometry"))
        info = {
            "channel": ch.name,
            "main_key": main_shape.name,
            "main_weight": float(main_w),
            "geometry_uids": [uid_key(g.uid) for g in geoms],
            "inbetweens": [],
        }
        #channel keeps only the main shape now
        fw_new = FbxArray.from_values("d", array.array("d", [100.0]))
        if fw_node is None:
            ch.node.children.append(FbxNode(b"FullWeights", [fw_new], b"d"))
        else:
            fw_node.props = [fw_new]
            fw_node.types = b"d"
        for w, shape in ordered[:-1]:
            remove_links.add((shape.uid, ch.uid))
            new_uid = next_uid
            next_uid += 1
            key_name = "%s_inbetween_%s" % (ch.name, _fmt_weight(w))
            #rename the shape geometry so the blender key is easy to find n obvious to humans
            shape.node.props[1] = key_name.encode("utf-8") + b"\x00\x01Geometry"
            new_ch = FbxNode(b"Deformer", [new_uid, key_name.encode("utf-8") + b"\x00\x01SubDeformer",
                                           b"BlendShapeChannel"], b"LSS", [
                FbxNode(b"Version", [100], b"I"),
                FbxNode(b"DeformPercent", [0.0], b"D"),
                FbxNode(b"FullWeights", [FbxArray.from_values("d", array.array("d", [100.0]))], b"d"),
            ])
            objects_node.children.append(new_ch)
            new_conns.append(FbxNode(b"C", [b"OO", shape.uid, new_uid], b"SLL"))
            for bs in blendshapes:
                new_conns.append(FbxNode(b"C", [b"OO", new_uid, bs.uid], b"SLL"))
            info["inbetweens"].append({"key": key_name, "weight": float(w)})
        result.inbetweens.append(info)
        result.fix("split_inbetween_channel")
    if remove_links:
        kept = []
        for c in conns_node.children:
            if c.name == b"C" and len(c.props) >= 3 and (c.props[1], c.props[2]) in remove_links:
                continue
            kept.append(c)
        conns_node.children = kept + new_conns


def _pad_full_weights(doc, result):
    #importer asserts len(FullWeights) >= shapes per channel, some exporters skip it entirely
    for ch in doc.objects_of(b"Deformer", b"BlendShapeChannel"):
        n = len(doc.children(ch.uid, b"Geometry"))
        if n == 0:
            continue
        fw = ch.node.find(b"FullWeights")
        have = fw.props[0].count if (fw is not None and fw.props and isinstance(fw.props[0], FbxArray)) else 0
        if have >= n:
            continue
        vals = list(fw.props[0].values()) if have else []
        while len(vals) < n:
            vals.append(100.0)
        arr = FbxArray.from_values("d", array.array("d", vals))
        if fw is None:
            ch.node.children.append(FbxNode(b"FullWeights", [arr], b"d"))
        else:
            fw.props = [arr]
            fw.types = b"d"
        result.fix("padded_full_weights")


def _fix_shape_lengths(doc, result):
    #Indexes n Vertices count mismatch = assert in the importer. trim to whatever both agree on
    for shape in doc.objects_of(b"Geometry", b"Shape"):
        idx = shape.node.find(b"Indexes")
        verts = shape.node.find(b"Vertices")
        if idx is None or verts is None or not idx.props or not verts.props:
            continue
        a, b = idx.props[0], verts.props[0]
        if not isinstance(a, FbxArray) or not isinstance(b, FbxArray):
            continue
        n_idx = a.count
        n_vec = b.count // 3
        if n_idx == n_vec and b.count % 3 == 0:
            continue
        n = min(n_idx, n_vec)
        idx.props = [FbxArray.from_values("i", array.array("i", a.values()[:n]))]
        verts.props = [FbxArray.from_values("d", array.array("d", b.values()[:n * 3]))]
        normals = shape.node.find(b"Normals")
        if normals is not None and normals.props and isinstance(normals.props[0], FbxArray) and normals.props[0].count > n * 3:
            normals.props = [FbxArray.from_values("d", array.array("d", normals.props[0].values()[:n * 3]))]
        result.fix("trimmed_shape_data")


def _inject_uids(doc, opts, result):
    name = opts.uid_prop.encode("utf-8")
    for obj in doc.objects.values():
        if obj.cls not in _UID_CLASSES:
            continue
        if obj.cls == b"Geometry" and obj.subtype != b"Mesh":
            continue
        p70 = _p70(obj.node)
        #drop a stale stamp if this file was already prepared once
        p70.children = [p for p in p70.children if not (p.props and p.props[0] == name)]
        p70.children.append(_user_prop_node(name, uid_key(obj.uid).encode("utf-8")))
        result.uid_count += 1


def prepare_document(doc, out_path, options=None):
    #does the whole dance n writes binary to out_path. returns a PrepareResult
    opts = options or PrepareOptions()
    result = PrepareResult(out_path)
    if doc.is_v6:
        raise FbxError("FBX 6.x files cannot be converted for the Python importer; use the C++ importer (Blender 4.5+)")
    version = doc.version if doc.version >= 7100 else 7400
    if version > 7700:
        version = 7700
    result.version = version
    result.converted_from_ascii = doc.format == "ascii"
    root = doc.root
    if opts.sanitize:
        _ensure_sections(root, result, version)
        _pad_full_weights(doc, result)
        _fix_shape_lengths(doc, result)
    if opts.split_inbetweens:
        _split_inbetweens(doc, result)
    if opts.texture_paths:
        _rewrite_texture_paths(doc, opts.texture_paths, result)
    if opts.inject_uids:
        _inject_uids(doc, opts, result)
    if opts.sanitize or opts.strip_user_props:
        _sanitize_tree(root, result, opts.strip_user_props)
    _binary.write_binary_file(out_path, root, version)
    return result


def prepare_file(src_path, out_path, options=None, cancel=None):
    #convenience: open, prepare, close
    with FbxDocument.open(src_path, lazy=False, cancel=cancel, lazy_names=PREPARE_LAZY_NODES) as doc:
        return prepare_document(doc, out_path, options)
