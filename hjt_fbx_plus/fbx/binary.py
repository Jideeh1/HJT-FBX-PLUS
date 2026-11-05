# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#binary fbx reader + writer, pure python no bpy
#
#reader
#  works on bytes or a readonly mmap so a 600mb fbx never gets slurped into ram all at once
#  arrays r just referenced, not decoded (see FbxArray)
#  some node names can be parsed lazy, we jump over them using the stored end offsets
#  n only parse when touched. makes preview scans of chonky files way faster
#
#writer
# two passes, sizes first then one straight write so it works on any stream
# array payloads from the source get copied as is (no recompress) so rewriting is cheap af


from __future__ import annotations

import mmap
import os
import struct

from .elements import FbxArray, FbxBlob, FbxError, FbxNode

HEAD_MAGIC = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"
HEADER_SIZE = len(HEAD_MAGIC) + 4  #magic + uint32 version

_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
#the sdk always slaps a NULL record after these even if empty, blender's encoder copies that so we do too
_ALWAYS_SENTINEL = frozenset((b"AnimationStack", b"AnimationLayer"))

_U32 = struct.Struct("<I")
_U32x3 = struct.Struct("<III")
_U64x3 = struct.Struct("<QQQ")
_I16 = struct.Struct("<h")
_I32 = struct.Struct("<i")
_I64 = struct.Struct("<q")
_F32 = struct.Struct("<f")
_F64 = struct.Struct("<d")

#R blobs bigger than this stay lazy
BLOB_LAZY_THRESHOLD = 64 * 1024
# files bigger than this get mmapped instead of read in one go
MMAP_THRESHOLD = 16 * 1024 * 1024


def is_binary_fbx(prefix):
    return bytes(prefix[: len(HEAD_MAGIC)]) == HEAD_MAGIC


class RawRef:
    #points at length bytes at off inside a BinarySource
    # only turns into real bytes when needed. we dont use memoryview slices bc
    # then closing the mmap explodes w "exported pointers exist" n i lost an hour to that lmao

    __slots__ = ("src", "off", "length")

    def __init__(self, src, off, length):
        self.src = src
        self.off = off
        self.length = length

    def __len__(self):
        return self.length

    def __bytes__(self):
        return self.src.read_at(self.off, self.length)


class _LazyRawArray(FbxArray):
    #FbxArray but the payload is a RawRef, gets real bytes on demand

    __slots__ = ()

    def _decoded_bytes(self):
        raw = self._raw
        if isinstance(raw, RawRef):
            self._raw = raw = bytes(raw)
        return FbxArray._decoded_bytes(self)

    def raw_payload(self):
        raw = self._raw
        if isinstance(raw, RawRef):
            return self.encoding, bytes(raw)
        return FbxArray.raw_payload(self)

    def detach(self):
        if isinstance(self._raw, RawRef):
            self._raw = bytes(self._raw)


class _LazyBlob(FbxBlob):
    __slots__ = ()

    def tobytes(self):
        view = self._view
        return bytes(view)

    def detach(self):
        if isinstance(self._view, RawRef):
            self._view = bytes(self._view)


class BinarySource:
    #owns the file handle / mmap behind a parsed binary fbx. close it or windows wont let u delete the file

    def __init__(self, path=None, data=None, use_mmap=None):
        self.path = path
        self._fh = None
        self._mm = None
        if data is not None:
            self.buf = data
        else:
            size = os.path.getsize(path)
            if size == 0:
                raise FbxError("file is empty")
            if use_mmap is None:
                use_mmap = size >= MMAP_THRESHOLD
            if use_mmap:
                self._fh = open(path, "rb")
                try:
                    self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
                except (OSError, ValueError):
                    self._fh.seek(0)
                    self.buf = self._fh.read()
                    self._fh.close()
                    self._fh = None
                else:
                    self.buf = self._mm
            else:
                with open(path, "rb") as fh:
                    self.buf = fh.read()
        self.size = len(self.buf)
        self.closed = False

    def read_at(self, off, length):
        if self.closed:
            raise FbxError("FBX source was closed")
        return self.buf[off:off + length]

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._mm is not None:
            try:
                self._mm.close()
            except BufferError:  # pragma: no cover
                pass
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self.buf = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Parser:
    __slots__ = ("src", "buf", "size", "wide", "hstruct", "hsize", "sentinel",
                 "lazy_names", "nodes_parsed")

    def __init__(self, src, version, lazy_names):
        self.src = src
        self.buf = src.buf
        self.size = src.size
        self.wide = version >= 7500
        self.hstruct = _U64x3 if self.wide else _U32x3
        self.hsize = 24 if self.wide else 12
        self.sentinel = self.hsize + 1
        self.lazy_names = lazy_names or frozenset()
        self.nodes_parsed = 0

    def parse_children(self, pos, end):
        children = []
        limit = end - self.sentinel
        append = children.append
        parse_node = self.parse_node
        while pos < limit:
            node, pos = parse_node(pos)
            if node is None:
                break
            append(node)
        return children

    def parse_node(self, pos):
        buf = self.buf
        if pos + self.hsize + 1 > self.size:
            raise FbxError("unexpected end of file at offset %d" % pos)
        end, nprops, plen = self.hstruct.unpack_from(buf, pos)
        pos += self.hsize
        if end == 0:
            return None, pos + 1
        if end > self.size or end < pos:
            raise FbxError("corrupt node header at offset %d (end=%d, size=%d)" % (pos, end, self.size))
        nlen = buf[pos]
        pos += 1
        name = bytes(buf[pos:pos + nlen])
        pos += nlen
        props_end = pos + plen
        if props_end > end:
            raise FbxError("corrupt property list in node %r" % name)
        props = []
        types = bytearray()
        src = self.src
        for _ in range(nprops):
            t = buf[pos]
            pos += 1
            if t == 83 or t == 82:  #S / R
                (n,) = _U32.unpack_from(buf, pos)
                pos += 4
                if t == 82 and n > BLOB_LAZY_THRESHOLD:
                    val = _LazyBlob(RawRef(src, pos, n))
                else:
                    val = bytes(buf[pos:pos + n])
                pos += n
            elif t == 73:  # 'I'
                val = _I32.unpack_from(buf, pos)[0]
                pos += 4
            elif t == 68:  # 'D'
                val = _F64.unpack_from(buf, pos)[0]
                pos += 8
            elif t == 76:  # 'L'
                val = _I64.unpack_from(buf, pos)[0]
                pos += 8
            elif t == 70:  # 'F'
                val = _F32.unpack_from(buf, pos)[0]
                pos += 4
            elif t == 89:  # 'Y'
                val = _I16.unpack_from(buf, pos)[0]
                pos += 2
            elif t == 67:  # 'C'
                val = buf[pos]
                pos += 1
            elif t in (102, 100, 108, 105, 98):  #f d l i b arrays, the big boys
                count, enc, clen = _U32x3.unpack_from(buf, pos)
                pos += 12
                val = _LazyRawArray(chr(t), count, enc, RawRef(src, pos, clen))
                pos += clen
            else:
                raise FbxError("unknown property type 0x%02x in node %r at offset %d" % (t, name, pos - 1))
            props.append(val)
            types.append(t)
            if pos > props_end:
                raise FbxError("property overflow in node %r" % name)
        node = FbxNode(name, props, bytes(types))
        self.nodes_parsed += 1
        pos = props_end
        if pos < end:
            if name in self.lazy_names:
                node._lazy = _LazyChildren(self, pos, end)
            else:
                node._children = self.parse_children(pos, end)
        return node, end


class _LazyChildren:
    #callable that parses a skipped subtree when someone finally asks for it

    __slots__ = ("parser", "start", "end")

    def __init__(self, parser, start, end):
        self.parser = parser
        self.start = start
        self.end = end

    def __call__(self):
        if self.parser.src.closed:
            raise FbxError("cannot load FBX subtree: source closed")
        return self.parser.parse_children(self.start, self.end)


class BinaryFbx:
    #what read_binary gives back

    __slots__ = ("root", "version", "source", "footer", "node_count")

    def __init__(self, root, version, source, footer, node_count):
        self.root = root
        self.version = version
        self.source = source
        self.footer = footer
        self.node_count = node_count

    def close(self):
        self.source.close()


def read_binary(path=None, data=None, lazy_names=None, use_mmap=None):
    #parse a binary fbx from a path (or raw bytes in data)
    # lazy_names = node names whose kids get parsed on demand
    # if its file backed u gotta close() it when done, dont leak mmaps pls
    src = BinarySource(path=path, data=data, use_mmap=use_mmap)
    try:
        buf = src.buf
        if src.size < HEADER_SIZE or not is_binary_fbx(buf[:len(HEAD_MAGIC)]):
            raise FbxError("not a binary FBX file")
        (version,) = _U32.unpack_from(buf, len(HEAD_MAGIC))
        parser = _Parser(src, version, frozenset(lazy_names or ()))
        root = FbxNode(b"")
        pos = HEADER_SIZE
        children = []
        while pos < src.size:
            if pos + parser.sentinel > src.size:
                break
            node, pos = parser.parse_node(pos)
            if node is None:
                break
            children.append(node)
        root._children = children
        footer = bytes(buf[pos:min(src.size, pos + 256)])
        return BinaryFbx(root, version, src, footer, parser.nodes_parsed)
    except Exception:
        src.close()
        raise





#writer stuff below

def _as_bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, FbxBlob):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _prop_size(t, value):
    #encoded size of one prop, not counting the type byte
    if t == 83 or t == 82:  # S / R
        if isinstance(value, str):
            return 4 + len(value.encode("utf-8"))
        return 4 + len(value)
    if t == 73 or t == 70:
        return 4
    if t == 68 or t == 76:
        return 8
    if t == 89:
        return 2
    if t == 67:
        return 1
    if t in (102, 100, 108, 105, 98):
        if not isinstance(value, FbxArray):
            raise FbxError("array property must be an FbxArray")
        return 12 + value.payload_size()
    raise FbxError("cannot write property type %r" % chr(t))


def _write_prop(write, t, value):
    if t == 83 or t == 82:
        data = _as_bytes(value)
        write(_U32.pack(len(data)))
        write(data)
    elif t == 73:
        write(_I32.pack(int(value)))
    elif t == 68:
        write(_F64.pack(float(value)))
    elif t == 76:
        write(_I64.pack(int(value)))
    elif t == 70:
        write(_F32.pack(float(value)))
    elif t == 89:
        write(_I16.pack(int(value)))
    elif t == 67:
        write(bytes((int(value) & 0xFF,)))
    else:
        enc, data = value.raw_payload()
        data = _as_bytes(data)
        write(_U32x3.pack(value.count, enc, len(data)))
        write(data)


class _Writer:
    def __init__(self, version):
        self.wide = version >= 7500
        self.hstruct = _U64x3 if self.wide else _U32x3
        self.hsize = 24 if self.wide else 12
        self.sentinel = b"\0" * (self.hsize + 1)
        self.sizes = {}

    def _needs_sentinel(self, node, is_last):
        return bool(node.children) or (not node.props and not is_last) or node.name in _ALWAYS_SENTINEL

    def node_size(self, node, is_last):
        types = node.types
        plen = 0
        for i, value in enumerate(node.props):
            plen += 1 + _prop_size(types[i], value)
        size = self.hsize + 1 + len(node.name) + plen
        children = node.children
        if children:
            last = len(children) - 1
            for i, child in enumerate(children):
                size += self.node_size(child, i == last)
        if self._needs_sentinel(node, is_last):
            size += len(self.sentinel)
        self.sizes[id(node)] = (size, plen)
        return size

    def write_node(self, write, node, offset, is_last):
        size, plen = self.sizes[id(node)]
        end = offset + size
        if len(node.name) > 255:
            raise FbxError("node name too long")
        write(self.hstruct.pack(end, len(node.props), plen))
        write(bytes((len(node.name),)))
        write(node.name)
        types = node.types
        for i, value in enumerate(node.props):
            t = types[i]
            write(bytes((t,)))
            _write_prop(write, t, value)
        pos = offset + self.hsize + 1 + len(node.name) + plen
        children = node.children
        if children:
            last = len(children) - 1
            for i, child in enumerate(children):
                pos = self.write_node(write, child, pos, i == last)
        if self._needs_sentinel(node, is_last):
            write(self.sentinel)
            pos += len(self.sentinel)
        if pos != end:  # pragma: no cover  if this ever fires i messed up the size math
            raise FbxError("size mismatch writing node %r" % node.name)
        return end


def write_binary(stream, root, version=7400):
    #writes roots children as binary fbx into stream
    # payloads get built just in time so ram stays around the size of the biggest array
    # footer is the blender style one, the real sdk footer depends on the file id n nobody reading it cares
    if version < 7100:
        raise FbxError("binary writer supports FBX 7.1+ only (got %d)" % version)
    w = _Writer(version)
    write = stream.write
    header = HEAD_MAGIC + _U32.pack(version)
    write(header)
    offset = len(header)
    children = root.children
    last = len(children) - 1
    for i, child in enumerate(children):
        w.sizes.clear()
        w.node_size(child, i == last)
        offset = w.write_node(write, child, offset, i == last)
    w.sizes.clear()
    write(w.sentinel)
    offset += len(w.sentinel)
    write(_FOOT_ID)
    write(b"\x00" * 4)
    offset += 20
    pad = ((offset + 15) & ~15) - offset
    if pad == 0:
        pad = 16
    write(b"\0" * pad)
    write(_U32.pack(version))
    write(b"\0" * 120)
    write(_FOOT_MAGIC)


def write_binary_file(path, root, version=7400):
    #temp file + rename so a crash mid write doesnt leave half an fbx lying around
    tmp = path + ".part"
    try:
        with open(tmp, "wb", buffering=1024 * 1024) as fh:
            write_binary(fh, root, version)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path
