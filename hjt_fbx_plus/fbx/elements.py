# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#the fbx node tree that both the binary n ascii readers spit out
# zero bpy in here on purpose so background threads n unit tests can use it
#
#how stuff is stored:
#  node names r bytes like b"Objects"
#  scalar props r plain python int float or bytes
#  arrays r FbxArray, they keep the raw (maybe zlib squished) payload n only decode when u ask
#  big raw blobs (embedded textures) r FbxBlob so we dont copy 40mb pngs around for no reason
#  types is bytes w one fbx type code per prop, b"LSS" for an object definiton for example

from __future__ import annotations

import array
import sys
import zlib

#fbx array code -> (python array typecode, item size)
ARRAY_TYPES = {
    "f": ("f", 4),
    "d": ("d", 8),
    "i": ("i", 4),
    "l": ("q", 8),
    "b": ("B", 1),
}

_BIG_ENDIAN = sys.byteorder == "big"

try:  #numpy is always there inside blender, outside its optional
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

_NUMPY_DTYPES = {"f": "<f4", "d": "<f8", "i": "<i4", "l": "<i8", "b": "u1"}



class FbxError(Exception):
    #broken or unsupported fbx data, autodesk pls
    pass


class FbxArray:
    #typed fbx array w lazy decoding
    # type is one of f d i l b. payload stays exactly how it was stored so the writer can copy it back w/o recompressing

    __slots__ = ("type", "count", "encoding", "_raw", "_values")

    def __init__(self, type_code, count, encoding=0, raw=None, values=None):
        if type_code not in ARRAY_TYPES:
            raise FbxError("unknown FBX array type %r" % (type_code,))
        self.type = type_code
        self.count = int(count)
        self.encoding = int(encoding)
        self._raw = raw
        self._values = values

    @classmethod
    def from_values(cls, type_code, values):
        typecode = ARRAY_TYPES[type_code][0]
        if isinstance(values, array.array) and values.typecode == typecode:
            arr = values
        else:
            arr = array.array(typecode, values)
        return cls(type_code, len(arr), 0, None, arr)

    #payload stuff
    def _decoded_bytes(self):
        raw = self._raw
        if raw is None:
            raise FbxError("array has no payload")
        if self.encoding == 0:
            return raw
        if self.encoding == 1:
            try:
                return zlib.decompress(raw)
            except zlib.error as exc:
                raise FbxError("corrupt compressed array: %s" % exc) from exc
        raise FbxError("unsupported array encoding %d" % self.encoding)

    def values(self):
        #decoded values as array.array, cached after the first time
        if self._values is None:
            typecode, size = ARRAY_TYPES[self.type]
            data = self._decoded_bytes()
            expected = self.count * size
            if len(data) < expected:
                raise FbxError(
                    "array payload too short (%d < %d bytes)" % (len(data), expected))
            arr = array.array(typecode)
            arr.frombytes(bytes(data[:expected]))
            if _BIG_ENDIAN:  # pragma: no cover
                arr.byteswap()  #who even runs blender on big endian lol
            self._values = arr
        return self._values

    def numpy(self):
        # same thing but numpy, needs numpy obv
        if _np is None:  # pragma: no cover
            raise FbxError("numpy is not available")
        if self._values is not None:
            return _np.asarray(self._values)
        data = self._decoded_bytes()
        dtype = _np.dtype(_NUMPY_DTYPES[self.type])
        return _np.frombuffer(bytes(data[: self.count * dtype.itemsize]), dtype=dtype)

    def raw_payload(self):
        #(encoding, bytes) ready to dump straight into a binary fbx
        if self._raw is not None:
            return self.encoding, self._raw
        typecode, _size = ARRAY_TYPES[self.type]
        arr = self._values
        if not isinstance(arr, array.array) or arr.typecode != typecode:
            arr = array.array(typecode, arr)
        if _BIG_ENDIAN:  # pragma: no cover
            arr = array.array(typecode, arr)
            arr.byteswap()
        return 0, arr.tobytes()

    def payload_size(self):
        #how many bytes raw_payload is gonna give u, w/o actually building it
        if self._raw is not None:
            return len(self._raw)
        return self.count * ARRAY_TYPES[self.type][1]

    def release(self):
        #forget the decoded copy, raw payload stays
        if self._raw is not None:
            self._values = None

    def detach(self):
        #copy the payload out of the source buffer so the file can be closed
        if self._raw is not None and not isinstance(self._raw, bytes):
            self._raw = bytes(self._raw)

    def __len__(self):
        return self.count

    def __repr__(self):
        return "<FbxArray %s[%d] enc=%d>" % (self.type, self.count, self.encoding)


class FbxBlob:
    #big raw R prop (embedded media mostly), lazy view into the source

    __slots__ = ("_view",)

    def __init__(self, view):
        self._view = view

    def tobytes(self):
        return bytes(self._view)

    def detach(self):
        if not isinstance(self._view, bytes):
            self._view = bytes(self._view)

    def __len__(self):
        return len(self._view)

    def __repr__(self):
        return "<FbxBlob %d bytes>" % len(self._view)


class FbxNode:
    #one fbx node w props n children
    # children can be lazy, the binary reader can skip a whole subtree n only parse it when someone touches .children

    __slots__ = ("name", "props", "types", "_children", "_lazy")

    def __init__(self, name, props=None, types=b"", children=None):
        self.name = name
        self.props = props if props is not None else []
        self.types = types
        self._children = children if children is not None else []
        self._lazy = None

    @property
    def children(self):
        if self._lazy is not None:
            loader = self._lazy
            self._lazy = None
            self._children = loader()
        return self._children

    @children.setter
    def children(self, value):
        self._lazy = None
        self._children = value

    @property
    def is_loaded(self):
        return self._lazy is None

    def find(self, name):
        #first direct child w that name or None
        for child in self.children:
            if child.name == name:
                return child
        return None

    def find_all(self, name):
        for child in self.children:
            if child.name == name:
                yield child

    def find_path(self, *names):
        node = self
        for name in names:
            node = node.find(name)
            if node is None:
                return None
        return node

    def prop(self, index=0, default=None):
        props = self.props
        if 0 <= index < len(props):
            return props[index]
        return default

    def value(self, name, index=0, default=None):
        #prop number index of the first child called name
        child = self.find(name)
        if child is None:
            return default
        return child.prop(index, default)

    def append(self, child):
        self.children.append(child)
        return child

    def add_prop(self, type_code, value):
        if isinstance(type_code, str):
            type_code = type_code.encode("ascii")
        self.props.append(value)
        self.types = self.types + type_code

    def walk(self):
        # depth first, this node + everything under it
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def __repr__(self):
        return "<FbxNode %s props=%d children=%s>" % (
            self.name.decode("ascii", "replace"), len(self.props),
            "lazy" if self._lazy is not None else len(self._children))


def name_class_split(raw):
    #splits an fbx object name into (name, class)
    # binary does b"Name\x00\x01Class", ascii does b"Class::Name" bc consistency is for the weak
    # plain names give (name, b"")
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "surrogateescape")
    sep = raw.find(b"\x00\x01")
    if sep >= 0:
        return raw[:sep], raw[sep + 2:]
    sep = raw.find(b"::")
    if sep >= 0:
        return raw[sep + 2:], raw[:sep]
    return raw, b""


def decode_name(raw):
    #bytes -> text for display, never throws. latin-1 fallback for the cursed files
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")
