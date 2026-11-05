# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#type schema that gives ascii fbx values their binary type codes
#
# ascii only knows "string" n "number". binary fbx (n blenders importer which ASSERTS on types, ffs)
# needs the exact codes: L for object ids, I for versions/counts, D for doubles, i d f l arrays etc
#tables below copy what the autodesk sdk writes. if u find a file that breaks this, welp


from __future__ import annotations

import array

from .elements import FbxArray

#array element type per node name, 7.x n 6.x
ARRAY_TYPE_BY_NAME = {
    b"Vertices": "d", b"PolygonVertexIndex": "i", b"Edges": "i",
    b"Normals": "d", b"NormalsW": "d", b"NormalsIndex": "i",
    b"Binormals": "d", b"BinormalsW": "d", b"BinormalsIndex": "i",
    b"Tangents": "d", b"TangentsW": "d", b"TangentsIndex": "i",
    b"UV": "d", b"UVIndex": "i", b"Colors": "d", b"ColorIndex": "i",
    b"Materials": "i", b"Smoothing": "i", b"Visibility": "b",
    b"EdgeCrease": "d", b"VertexCrease": "d", b"Holes": "i",
    b"Indexes": "i", b"Weights": "d", b"Transform": "d", b"TransformLink": "d",
    b"TransformAssociateModel": "d", b"Matrix": "d", b"FullWeights": "d",
    b"KeyTime": "l", b"KeyValueFloat": "f", b"KeyAttrFlags": "i",
    b"KeyAttrDataFloat": "f", b"KeyAttrRefCount": "i",
    b"Points": "d", b"KnotVector": "d", b"KnotVectorU": "d", b"KnotVectorV": "d",
    b"MultiplicityU": "i", b"MultiplicityV": "i",
    b"BlendModes": "i", b"Alphas": "d", b"TextureId": "i", b"PolygonGroup": "i",
    b"UserData": "d", b"Vertices2": "d",
}

# 6.x writes arrays as plain comma lists so only these names count as arrays
ARRAY_NODES_6X = frozenset(ARRAY_TYPE_BY_NAME) | frozenset((
    b"TextureUV", b"TextureUVVerticeIndex", b"GeometryUVInfo", b"Shape",
))

#scalar overrides by node name
INT32_NODES = frozenset((
    b"Version", b"FBXHeaderVersion", b"FBXVersion", b"EncryptionType", b"Year", b"Month",
    b"Day", b"Hour", b"Minute", b"Second", b"Millisecond", b"Count", b"GeometryVersion",
    b"TypedIndex", b"Layer", b"MultiLayer", b"MultiTake", b"KeyVer", b"KeyCount",
    b"NbPoseNodes", b"UseMipMap", b"Cropping", b"Order", b"Dimension", b"Rational",
    b"ShowInfoOnMoving", b"ShowAudio", b"LayeredTexture", b"Step", b"Mode",
    b"NodeAttributeVersion", b"ContentVersion",
))
FLOAT64_NODES = frozenset((
    b"Default", b"DeformPercent", b"Link_DeformAcuracy", b"CameraOrthoZoom",
    b"ModelUVTranslation", b"ModelUVScaling", b"Position", b"Up", b"LookAt",
    b"AudioColor", b"Weight", b"Opacity",
))
INT64_NODES = frozenset((b"LocalTime", b"ReferenceTime", b"RootNode"))
#nodes whose value is a raw blob, ascii writes them as base64 text
RAW_NODES = frozenset((b"Content", b"FileId"))

#Properties70 values get typed by the declared prop type
P_INT32 = frozenset((b"int", b"Integer", b"enum", b"Enum", b"bool", b"Bool",
                     b"Visibility Inheritance", b"KUInt", b"UInteger", b"Short",
                     b"UShort", b"Char", b"UChar"))
P_INT64 = frozenset((b"KTime", b"ULongLong", b"LongLong", b"KTimeCode"))
P_FLOAT32 = frozenset((b"float", b"Float"))

_NAMED_CLASSES = (
    b"Model", b"Geometry", b"Material", b"Texture", b"Video", b"Deformer", b"SubDeformer",
    b"Pose", b"NodeAttribute", b"AnimationStack", b"AnimationLayer", b"AnimationCurveNode",
    b"AnimationCurve", b"SceneInfo", b"Implementation", b"BindingTable", b"LayeredTexture",
    b"Constraint", b"Character", b"CharacterPose", b"ControlSetPlug", b"Collection",
    b"CollectionExclusive", b"SelectionNode", b"SelectionSet", b"DisplayLayer", b"Folder",
    b"Container", b"Document", b"ObjectMetaData", b"Scene", b"Shader", b"ProceduralTexture",
    b"Light", b"Camera", b"Cache", b"LodGroup", b"Null",
)

_INT32_MIN = -(1 << 31)
_INT32_MAX = (1 << 31) - 1
_BOOL_WORDS = {b"Y": 1, b"T": 1, b"N": 0, b"F": 0}


_CLASS_PREFIX = None


def ascii_name_to_binary(raw, default_class):
    # b"Model::Cube" -> b"Cube\x00\x01Model"  blender splits on \x00\x01 n crashes if its missing lol
    # the class is whatever identifier sits before the FIRST "::" (AnimStack, AnimCurveNode, Model...)
    # so namespaced names like Model::Char1::Hips keep "Char1::Hips" as the name
    global _CLASS_PREFIX
    if _CLASS_PREFIX is None:
        import re
        _CLASS_PREFIX = re.compile(rb"^([A-Za-z][A-Za-z0-9_]*)::")
    if b"\x00\x01" in raw:
        return raw
    m = _CLASS_PREFIX.match(raw)
    if m:
        return raw[m.end():] + b"\x00\x01" + m.group(1)
    return raw + b"\x00\x01" + (default_class or b"")


def _scalar_code(value, forced):
    from .ascii import Word  #local import bc circular imports r the devil
    if isinstance(value, Word):
        if forced is None and value in _BOOL_WORDS:
            return "C", _BOOL_WORDS[value]
        return "S", bytes(value)
    if isinstance(value, (bytes, bytearray)):
        return "S", bytes(value)
    if isinstance(value, float):
        if forced == "F":
            return "F", value
        if forced in ("I", "L"):
            return forced, int(value)
        return "D", value
    if isinstance(value, int):
        if forced == "D" or forced == "F":
            return forced, float(value)
        if forced == "L":
            return "L", value
        if forced == "I" and _INT32_MIN <= value <= _INT32_MAX:
            return "I", value
        if _INT32_MIN <= value <= _INT32_MAX:
            return "I", value
        return "L", value
    raise TypeError("unsupported ASCII value %r" % (value,))


def _make_array(name, holder):
    vals = holder.vals
    target = ARRAY_TYPE_BY_NAME.get(name)
    if target is None:
        if vals.typecode == "d":
            target = "d"
        else:
            target = "i"
            for v in vals:
                if v < _INT32_MIN or v > _INT32_MAX:
                    target = "l"
                    break
    typecode = {"d": "d", "f": "f", "i": "i", "l": "q", "b": "B"}[target]
    if vals.typecode == typecode:
        arr = vals
    elif target in ("f", "d") and vals.typecode == "q":
        if name == b"KeyAttrDataFloat" and any(abs(v) > 16777216 for v in vals):
            #ascii writes KeyAttrDataFloat as raw float bit patterns as ints. why. just why
            arr = array.array("f")
            arr.frombytes(array.array("i", [((v + (1 << 31)) % (1 << 32)) - (1 << 31) for v in vals]).tobytes())
        else:
            arr = array.array(typecode, vals)
    elif target in ("i", "l", "b") and vals.typecode == "d":
        arr = array.array(typecode, (int(v) for v in vals))
    else:
        try:
            arr = array.array(typecode, vals)
        except OverflowError:
            arr = array.array("q", vals)
            target = "l"
    return FbxArray(target, len(arr), 0, None, arr)


def assign_types(node, parent_names, version):
    #gives node.props real binary type codes, in place
    # yes this function is a giant if chain, fbx is a giant if chain, deal w it
    from .ascii import _AsciiNumbers, decode_content_strings  #avoid the cycle again

    name = node.name
    props = node.props
    parent = parent_names[-1] if parent_names else b""
    is_v7 = version >= 7000 or version == 0
    out_props = []
    out_types = bytearray()

    def add(code, value):
        out_props.append(value)
        out_types.append(ord(code))

    if name in RAW_NODES:
        data = decode_content_strings([p for p in props if isinstance(p, (bytes, bytearray))])
        add("R", data)
        node.props = out_props
        node.types = bytes(out_types)
        return

    for i, value in enumerate(props):
        if isinstance(value, _AsciiNumbers):
            arr = _make_array(name, value)
            add(arr.type, arr)
            continue
        if isinstance(value, FbxArray):
            add(value.type, value)
            continue
        forced = None
        if parent == b"Objects":
            if is_v7:
                if i == 0:
                    forced = "L"
                elif i == 1 and isinstance(value, (bytes, bytearray)):
                    add("S", ascii_name_to_binary(bytes(value), name))
                    continue
            elif i == 0 and isinstance(value, (bytes, bytearray)):
                add("S", ascii_name_to_binary(bytes(value), name))
                continue
        elif name == b"P" and parent == b"Properties70" and i >= 4:
            ptype = props[1] if len(props) > 1 else b""
            if ptype in P_INT64:
                forced = "L"
            elif ptype in P_INT32:
                forced = "I"
            elif ptype in P_FLOAT32:
                forced = "F"
            else:
                forced = "D"
        elif name == b"Property" and parent == b"Properties60" and i >= 3:
            ptype = props[1] if len(props) > 1 else b""
            if ptype in P_INT64:
                forced = "L"
            elif ptype in P_INT32:
                forced = "I"
            elif ptype in P_FLOAT32:
                forced = "F"
            else:
                forced = "D"
        elif name == b"C" and parent == b"Connections" and i in (1, 2):
            forced = "L"
        elif name == b"Node" and parent == b"PoseNode" and is_v7:
            forced = "L"
        elif name == b"Document" and parent == b"Documents" and i == 0:
            forced = "L"
        elif name in INT64_NODES:
            forced = "L"
        elif name in INT32_NODES:
            forced = "I"
        elif name in FLOAT64_NODES:
            forced = "D"
        elif name.startswith(b"LayerElement") and isinstance(value, int):
            forced = "I"
        elif name in (b"TextureName", b"Media") and isinstance(value, (bytes, bytearray)):
            add("S", ascii_name_to_binary(bytes(value), b"Texture" if name == b"TextureName" else b"Video"))
            continue
        elif name == b"SceneInfo" and i == 0 and isinstance(value, (bytes, bytearray)):
            add("S", ascii_name_to_binary(bytes(value), b"SceneInfo"))
            continue
        code, val = _scalar_code(value, forced)
        add(code, val)

    node.props = out_props
    node.types = bytes(out_types)
