# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#native 7z reader using only the python stdlib (lzma zlib bz2) + our aes
#
# handles: lzma, lzma2, bcj x86/arm/armt/ppc/sparc/ia64, delta (all via liblzma filter chains),
#  deflate, bzip2, copy, 7zaes (aes-256 + sha256), encrypted headers, solid blocks, empty files
#  n multi volume .7z.001 .7z.002 ...
# does NOT handle: ppmd, bcj2, deflate64, zstd/brotli forks. those raise UnsupportedArchive n the
#  caller falls back to py7zr or an external 7-zip if one is around
#
#memory: everything streams in 1mb chunks. extracting a few files from a solid block still has
# to decompress everything before them in that block (thats just how solid works), but we stop
# as soon as the last wanted file is done, n park the decoder so the next ask can keep going from there

from __future__ import annotations

import bz2
import lzma
import os
import struct
import zlib

from .base import (ArchiveEntry, ArchiveReader, CorruptArchive, PasswordRequired, Progress,
                   UnsupportedArchive, WrongPassword, check_cancel, normalize_member_path)
from . import crypto

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
CHUNK = 1 << 20

#property ids, straight outta 7zFormat.txt
K_END, K_HEADER, K_ARCHIVE_PROPS, K_ADD_STREAMS, K_MAIN_STREAMS, K_FILES = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS, K_SIZE, K_CRC, K_FOLDER = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B
K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM, K_EMPTY_STREAM, K_EMPTY_FILE = 0x0C, 0x0D, 0x0E, 0x0F
K_ANTI, K_NAME, K_CTIME, K_ATIME, K_MTIME, K_ATTRS = 0x10, 0x11, 0x12, 0x13, 0x14, 0x15
K_COMMENT, K_ENCODED_HEADER, K_START_POS, K_DUMMY = 0x16, 0x17, 0x18, 0x19

C_COPY = b"\x00"
C_DELTA = b"\x03"
C_LZMA = b"\x03\x01\x01"
C_LZMA2 = b"\x21"
C_AES = b"\x06\xf1\x07\x01"
C_DEFLATE = b"\x04\x01\x08"
C_BZIP2 = b"\x04\x02\x02"
_BCJ = {
    b"\x03\x03\x01\x03": lzma.FILTER_X86, b"\x04": lzma.FILTER_X86,
    b"\x03\x03\x02\x05": lzma.FILTER_POWERPC, b"\x05": lzma.FILTER_POWERPC,
    b"\x03\x03\x04\x01": lzma.FILTER_IA64, b"\x06": lzma.FILTER_IA64,
    b"\x03\x03\x05\x01": lzma.FILTER_ARM, b"\x07": lzma.FILTER_ARM,
    b"\x03\x03\x07\x01": lzma.FILTER_ARMTHUMB, b"\x08": lzma.FILTER_ARMTHUMB,
    b"\x03\x03\x08\x05": lzma.FILTER_SPARC, b"\x09": lzma.FILTER_SPARC,
}
_NAMES = {
    C_COPY: "Copy", C_DELTA: "Delta", C_LZMA: "LZMA", C_LZMA2: "LZMA2", C_AES: "7zAES",
    C_DEFLATE: "Deflate", C_BZIP2: "BZip2", b"\x03\x04\x01": "PPMd", b"\x03\x03\x01\x1b": "BCJ2",
    b"\x04\x01\x09": "Deflate64", b"\x04\xf7\x11\x01": "ZSTD", b"\x04\xf7\x11\x02": "Brotli",
    b"\x04\xf7\x11\x04": "LZ4", b"\x0a": "ARM64",
}
for _k in _BCJ:
    _NAMES.setdefault(_k, "BCJ")

FILETIME_EPOCH = 116444736000000000


def is_7z(prefix):
    return bytes(prefix[:6]) == SIGNATURE


class _Buf:
    #tiny cursor over header bytes w the 7z varint
    __slots__ = ("data", "pos")

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def byte(self):
        if self.pos >= len(self.data):
            raise CorruptArchive("7z header ends unexpectedly")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def bytes(self, n):
        if self.pos + n > len(self.data):
            raise CorruptArchive("7z header ends unexpectedly")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u32(self):
        return struct.unpack("<I", self.bytes(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.bytes(8))[0]

    def num(self):
        #7z variable length uint64. first byte's high bits say how many extra bytes follow. cute
        first = self.byte()
        mask = 0x80
        value = 0
        for i in range(8):
            if first & mask == 0:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def bits(self, n):
        out = []
        b = 0
        mask = 0
        for _ in range(n):
            if mask == 0:
                b = self.byte()
                mask = 0x80
            out.append(bool(b & mask))
            mask >>= 1
        return out

    def defined_bits(self, n):
        all_defined = self.byte()
        if all_defined:
            return [True] * n
        return self.bits(n)


class Coder:
    __slots__ = ("method", "num_in", "num_out", "props")

    def __init__(self, method, num_in, num_out, props):
        self.method = method
        self.num_in = num_in
        self.num_out = num_out
        self.props = props


class Folder:
    __slots__ = ("coders", "bind_pairs", "packed_streams", "unpack_sizes", "crc", "first_pack",
                 "num_substreams", "sub_sizes", "sub_crcs")

    def __init__(self):
        self.coders = []
        self.bind_pairs = []
        self.packed_streams = []
        self.unpack_sizes = []
        self.crc = None
        self.first_pack = 0
        self.num_substreams = 1
        self.sub_sizes = []
        self.sub_crcs = []

    @property
    def total_in(self):
        return sum(c.num_in for c in self.coders)

    @property
    def total_out(self):
        return sum(c.num_out for c in self.coders)

    def main_out_index(self):
        bound = {out_i for _in_i, out_i in self.bind_pairs}
        for i in range(self.total_out):
            if i not in bound:
                return i
        raise CorruptArchive("7z folder has no unbound output")

    def unpack_size(self):
        return self.unpack_sizes[self.main_out_index()]

    def methods(self):
        return "+".join(_NAMES.get(c.method, c.method.hex()) for c in self.coders)

    @property
    def encrypted(self):
        return any(c.method == C_AES for c in self.coders)


class _StreamsInfo:
    def __init__(self):
        self.pack_pos = 0
        self.pack_sizes = []
        self.folders = []


def _read_pack_info(b, si):
    si.pack_pos = b.num()
    n = b.num()
    while True:
        pid = b.num()
        if pid == K_END:
            break
        if pid == K_SIZE:
            si.pack_sizes = [b.num() for _ in range(n)]
        elif pid == K_CRC:
            defined = b.defined_bits(n)
            for d in defined:
                if d:
                    b.u32()
        else:
            raise CorruptArchive("unexpected property %d in 7z pack info" % pid)


def _read_folder(b):
    f = Folder()
    for _ in range(b.num()):
        flag = b.byte()
        if flag & 0x80:
            raise UnsupportedArchive("7z alternative coder methods are not supported")
        method = bytes(b.bytes(flag & 0x0F))
        if flag & 0x10:
            num_in, num_out = b.num(), b.num()
        else:
            num_in, num_out = 1, 1
        props = bytes(b.bytes(b.num())) if flag & 0x20 else b""
        f.coders.append(Coder(method, num_in, num_out, props))
    for _ in range(f.total_out - 1):
        f.bind_pairs.append((b.num(), b.num()))
    n_packed = f.total_in - len(f.bind_pairs)
    if n_packed == 1:
        bound_in = {i for i, _o in f.bind_pairs}
        for i in range(f.total_in):
            if i not in bound_in:
                f.packed_streams = [i]
                break
    else:
        f.packed_streams = [b.num() for _ in range(n_packed)]
    return f


def _read_unpack_info(b, si):
    if b.num() != K_FOLDER:
        raise CorruptArchive("7z: expected folder info")
    n = b.num()
    if b.byte() != 0:
        raise UnsupportedArchive("7z external folder data is not supported")
    si.folders = [_read_folder(b) for _ in range(n)]
    if b.num() != K_CODERS_UNPACK_SIZE:
        raise CorruptArchive("7z: expected coder unpack sizes")
    for f in si.folders:
        f.unpack_sizes = [b.num() for _ in range(f.total_out)]
    while True:
        pid = b.num()
        if pid == K_END:
            break
        if pid == K_CRC:
            defined = b.defined_bits(n)
            for f, d in zip(si.folders, defined):
                if d:
                    f.crc = b.u32()
        else:
            raise CorruptArchive("unexpected property %d in 7z unpack info" % pid)


def _read_substreams(b, si):
    folders = si.folders
    for f in folders:
        f.num_substreams = 1
    pid = b.num()
    if pid == K_NUM_UNPACK_STREAM:
        for f in folders:
            f.num_substreams = b.num()
        pid = b.num()
    if pid == K_SIZE:
        for f in folders:
            if f.num_substreams == 0:
                continue
            sizes = [b.num() for _ in range(f.num_substreams - 1)]
            sizes.append(f.unpack_size() - sum(sizes))
            f.sub_sizes = sizes
        pid = b.num()
    for f in folders:
        if not f.sub_sizes and f.num_substreams == 1:
            f.sub_sizes = [f.unpack_size()]
    #crcs only listed for streams we dont already know (single stream folders reuse the folder crc)
    unknown = 0
    for f in folders:
        if not (f.num_substreams == 1 and f.crc is not None):
            unknown += f.num_substreams
    crcs = []
    while pid != K_END:
        if pid == K_CRC:
            defined = b.defined_bits(unknown)
            crcs = [b.u32() if d else None for d in defined]
        else:
            raise CorruptArchive("unexpected property %d in 7z substreams" % pid)
        pid = b.num()
    it = iter(crcs)
    for f in folders:
        if f.num_substreams == 1 and f.crc is not None:
            f.sub_crcs = [f.crc]
        else:
            f.sub_crcs = [next(it, None) for _ in range(f.num_substreams)]


def _read_streams_info(b):
    si = _StreamsInfo()
    while True:
        pid = b.num()
        if pid == K_END:
            break
        if pid == K_PACK_INFO:
            _read_pack_info(b, si)
        elif pid == K_UNPACK_INFO:
            _read_unpack_info(b, si)
        elif pid == K_SUBSTREAMS:
            _read_substreams(b, si)
        else:
            raise CorruptArchive("unexpected property %d in 7z streams info" % pid)
    for f in si.folders:
        if not f.sub_sizes and f.num_substreams == 1:
            f.sub_sizes = [f.unpack_size()]
            f.sub_crcs = [f.crc]
    pack_index = 0
    for f in si.folders:
        f.first_pack = pack_index
        pack_index += len(f.packed_streams)
    return si


#decoding pipeline, the part where i questioned my life choices

def _lzma_filter(coder):
    m, p = coder.method, coder.props
    if m == C_LZMA:
        if len(p) < 5:
            raise CorruptArchive("bad LZMA properties")
        d = p[0]
        lc = d % 9
        d //= 9
        return {"id": lzma.FILTER_LZMA1, "lc": lc, "lp": d % 5, "pb": d // 5,
                "dict_size": max(4096, struct.unpack("<I", p[1:5])[0])}
    if m == C_LZMA2:
        b = p[0] if p else 0
        if b > 40:
            raise CorruptArchive("bad LZMA2 properties")
        dsize = 0xFFFFFFFF if b == 40 else (2 | (b & 1)) << (b // 2 + 11)
        return {"id": lzma.FILTER_LZMA2, "dict_size": max(4096, dsize)}
    if m == C_DELTA:
        return {"id": lzma.FILTER_DELTA, "dist": (p[0] + 1) if p else 1}
    if m in _BCJ:
        f = {"id": _BCJ[m]}
        if len(p) == 4:
            f["start_offset"] = struct.unpack("<I", p)[0]
        return f
    return None


def _limit(it, size):
    #cut a chunk stream at exactly size bytes
    left = size
    for chunk in it:
        if left <= 0:
            break
        if len(chunk) > left:
            chunk = chunk[:left]
        left -= len(chunk)
        if chunk:
            yield chunk
    if left > 0:
        raise CorruptArchive("7z stream is shorter than expected")


def _lzma_stage(src, filters, out_size):
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    produced = 0
    try:
        for chunk in src:
            out = dec.decompress(chunk, CHUNK)
            while True:
                if out:
                    if produced + len(out) > out_size:
                        out = out[:out_size - produced]
                    produced += len(out)
                    yield out
                if produced >= out_size or dec.eof or dec.needs_input:
                    break
                out = dec.decompress(b"", CHUNK)
            if produced >= out_size or dec.eof:
                return
        while produced < out_size and not dec.eof:
            out = dec.decompress(b"", CHUNK)
            if not out:
                break
            out = out[:out_size - produced]
            produced += len(out)
            yield out
    except lzma.LZMAError as exc:
        raise CorruptArchive("LZMA data error: %s" % exc) from exc


def _zlib_stage(src, out_size):
    dec = zlib.decompressobj(-15)
    produced = 0
    try:
        for chunk in src:
            out = dec.decompress(chunk, CHUNK)
            while out:
                out = out[:out_size - produced]
                produced += len(out)
                yield out
                if produced >= out_size:
                    return
                out = dec.decompress(dec.unconsumed_tail, CHUNK) if dec.unconsumed_tail else b""
        tail = dec.flush()
        if tail and produced < out_size:
            yield tail[:out_size - produced]
    except zlib.error as exc:
        raise CorruptArchive("Deflate data error: %s" % exc) from exc


def _bz2_stage(src, out_size):
    #bzip2 can blow 1mb into 100mb+ so cap every call like the lzma one
    dec = bz2.BZ2Decompressor()
    produced = 0
    try:
        for chunk in src:
            out = dec.decompress(chunk, CHUNK)
            while True:
                if out:
                    out = out[:out_size - produced]
                    produced += len(out)
                    yield out
                if produced >= out_size or dec.eof or dec.needs_input:
                    break
                out = dec.decompress(b"", CHUNK)
            if produced >= out_size or dec.eof:
                return
    except (OSError, ValueError, EOFError) as exc:
        raise CorruptArchive("BZip2 data error: %s" % exc) from exc


def _x86_convert(data, ip, state):
    #bcj x86 DEcoder, straight port of Bra86.c from the lzma sdk
    # returns (bytes processed, new state). last <=4 bytes may stay unprocessed till more data shows up
    # honestly dont touch this, the mask logic is black magic n it works
    size = len(data)
    if size < 5:
        return 0, state
    pos = 0
    mask = state & 7
    limit = size - 4
    ip += 5
    find = data.find
    while True:
        p1 = find(b"\xe8", pos, limit)
        p2 = find(b"\xe9", pos, limit)
        if p1 < 0:
            p = p2
        elif p2 < 0:
            p = p1
        else:
            p = p1 if p1 < p2 else p2
        if p < 0:
            d = limit - pos
            return limit, (0 if d > 2 else mask >> d)
        d = p - pos
        pos = p
        if d > 2:
            mask = 0
        else:
            mask >>= d
            if mask != 0 and (mask > 4 or mask == 3 or ((data[p + (mask >> 1) + 1] + 1) & 0xFE) == 0):
                mask = (mask >> 1) | 4
                pos += 1
                continue
        if ((data[p + 4] + 1) & 0xFE) == 0:
            v = data[p + 1] | (data[p + 2] << 8) | (data[p + 3] << 16) | (data[p + 4] << 24)
            cur = (ip + pos) & 0xFFFFFFFF
            pos += 5
            v = (v - cur) & 0xFFFFFFFF
            if mask != 0:
                sh = (mask & 6) << 2
                if ((((v >> sh) & 0xFF) + 1) & 0xFE) == 0:
                    v ^= ((0x100 << sh) - 1)
                    v = (v - cur) & 0xFFFFFFFF
                mask = 0
            data[p + 1] = v & 0xFF
            data[p + 2] = (v >> 8) & 0xFF
            data[p + 3] = (v >> 16) & 0xFF
            data[p + 4] = (0 - ((v >> 24) & 1)) & 0xFF
        else:
            mask = (mask >> 1) | 4
            pos += 1


def _bcj_x86_stage(src, out_size, start_ip=0):
    #streaming wrapper. needed bc liblzma's bcj after RAW lzma1 never flushes its tail (no end marker)
    ip = start_ip
    state = 0
    buf = bytearray()
    produced = 0
    for chunk in src:
        buf += chunk
        n, state = _x86_convert(buf, ip, state)
        if n:
            ip = (ip + n) & 0xFFFFFFFF
            out = bytes(buf[:n])
            del buf[:n]
            out = out[:out_size - produced]
            produced += len(out)
            yield out
    if buf and produced < out_size:
        yield bytes(buf[:out_size - produced])


def _aes_stage(src, coder, password, out_size):
    if password is None:
        raise PasswordRequired("archive is password protected")
    p = coder.props
    if not p:
        raise CorruptArchive("bad 7zAES properties")
    b0 = p[0]
    cycles = b0 & 0x3F
    salt = iv = b""
    if b0 & 0xC0:
        b1 = p[1]
        salt_size = ((b0 >> 7) & 1) + (b1 >> 4)
        iv_size = ((b0 >> 6) & 1) + (b1 & 0x0F)
        salt = p[2:2 + salt_size]
        iv = p[2 + salt_size:2 + salt_size + iv_size]
    iv = (iv + b"\0" * 16)[:16]
    key = crypto.sevenzip_key(password, salt, cycles)
    dec = crypto.CbcDecryptor(key, iv)
    produced = 0
    for chunk in src:
        out = dec.decrypt(chunk)
        if out:
            out = out[:out_size - produced]
            produced += len(out)
            yield out
        if produced >= out_size:
            return


class SevenZipReader(ArchiveReader):
    format = "7z"
    backend = "7z (built-in)"

    def __init__(self, path, password=None):
        super().__init__(path, password)
        self._files = _VolumeFile.open(path)
        self._cursors = []  #paused decoders: (folder, next substream, _ChunkReader)
        try:
            self._parse()
        except Exception:
            self._files.close()
            raise

    def close(self):
        self._cursors = []
        self._files.close()

    #header
    def _parse(self):
        fh = self._files
        sig = fh.read_at(0, 32)
        if len(sig) < 32 or not is_7z(sig):
            raise UnsupportedArchive("not a 7z archive")
        start_crc = struct.unpack_from("<I", sig, 8)[0]
        if zlib.crc32(sig[12:32]) & 0xFFFFFFFF != start_crc:
            raise CorruptArchive("7z start header is damaged")
        next_off, next_size, next_crc = struct.unpack_from("<QQI", sig, 12)
        if next_size == 0:
            self.entries = []
            self.folders = []
            return
        if 32 + next_off + next_size > fh.size:
            raise CorruptArchive("7z archive is truncated (incomplete download or missing volume?)")
        raw = fh.read_at(32 + next_off, next_size)
        if zlib.crc32(raw) & 0xFFFFFFFF != next_crc:
            raise CorruptArchive("7z header checksum mismatch")
        b = _Buf(raw)
        pid = b.num()
        depth = 0
        while pid == K_ENCODED_HEADER:
            depth += 1
            if depth > 4:
                raise CorruptArchive("too many nested 7z headers")
            si = _read_streams_info(b)
            if any(f.encrypted for f in si.folders):
                self.header_encrypted = True
            try:
                data = b"".join(self._decode_folder(si, 0))
            except CorruptArchive as exc:
                if self.header_encrypted:
                    raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path)) from exc
                raise
            if si.folders and si.folders[0].crc is not None and zlib.crc32(data) & 0xFFFFFFFF != si.folders[0].crc:
                if self.header_encrypted:
                    raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path))
                raise CorruptArchive("7z header checksum mismatch")
            b = _Buf(data)
            pid = b.num()
        if pid != K_HEADER:
            raise CorruptArchive("unknown 7z header type %d" % pid)
        self._read_header(b)

    def _read_header(self, b):
        pid = b.num()
        if pid == K_ARCHIVE_PROPS:
            while True:
                t = b.num()
                if t == K_END:
                    break
                b.bytes(b.num())
            pid = b.num()
        if pid == K_ADD_STREAMS:
            _read_streams_info(b)
            pid = b.num()
        si = _StreamsInfo()
        if pid == K_MAIN_STREAMS:
            si = _read_streams_info(b)
            pid = b.num()
        self._si = si
        self.folders = si.folders
        names, empty_stream, empty_file, mtimes, attrs = [], [], [], [], []
        num_files = 0
        if pid == K_FILES:
            num_files = b.num()
            while True:
                t = b.num()
                if t == K_END:
                    break
                size = b.num()
                end = b.pos + size
                if t == K_EMPTY_STREAM:
                    empty_stream = b.bits(num_files)
                elif t == K_EMPTY_FILE:
                    empty_file = b.bits(sum(empty_stream))
                elif t == K_NAME:
                    if b.byte() != 0:
                        raise UnsupportedArchive("7z external names are not supported")
                    blob = b.bytes(end - b.pos)
                    names = blob.decode("utf-16-le", "replace").split("\x00")[:num_files]
                elif t == K_MTIME:
                    defined = b.defined_bits(num_files)
                    if b.byte() != 0:
                        raise UnsupportedArchive("7z external times are not supported")
                    mtimes = [b.u64() if d else None for d in defined]
                elif t == K_ATTRS:
                    defined = b.defined_bits(num_files)
                    if b.byte() != 0:
                        raise UnsupportedArchive("7z external attributes are not supported")
                    attrs = [b.u32() if d else None for d in defined]
                b.pos = end  #skip whatever we didnt read (ctime atime dummy padding etc)
            pid = b.num()
        if pid != K_END:
            raise CorruptArchive("7z header does not end properly")
        self._build_entries(num_files, names, empty_stream, empty_file, mtimes, attrs)

    def _build_entries(self, num_files, names, empty_stream, empty_file, mtimes, attrs):
        entries = []
        folder_i = 0
        sub_i = 0
        empty_i = 0
        folders = self.folders
        self._layout = {}
        for i in range(num_files):
            name = names[i] if i < len(names) else "file_%d" % i
            path = normalize_member_path(name)
            is_empty_stream = empty_stream[i] if i < len(empty_stream) else False
            attr = attrs[i] if i < len(attrs) else None
            mt = mtimes[i] if i < len(mtimes) else None
            mtime = (mt - FILETIME_EPOCH) / 1e7 if mt else None
            if is_empty_stream:
                is_file = empty_file[empty_i] if empty_i < len(empty_file) else False
                empty_i += 1
                is_dir = not is_file
                if attr is not None and attr & 0x10:
                    is_dir = True
                e = ArchiveEntry(path, 0, 0, None, is_dir, False, mtime, ("empty", i), "")
            else:
                while folder_i < len(folders) and sub_i >= folders[folder_i].num_substreams:
                    folder_i += 1
                    sub_i = 0
                if folder_i >= len(folders):
                    raise CorruptArchive("7z file list does not match its data streams")
                f = folders[folder_i]
                size = f.sub_sizes[sub_i] if sub_i < len(f.sub_sizes) else 0
                crc = f.sub_crcs[sub_i] if sub_i < len(f.sub_crcs) else None
                e = ArchiveEntry(path, size, 0, crc, False, f.encrypted, mtime, (folder_i, sub_i),
                                 f.methods(), folder_i)
                self._layout.setdefault(folder_i, []).append(e)
                sub_i += 1
            if path:
                entries.append(e)
        self.entries = entries
        self.solid = any(f.num_substreams > 1 for f in folders)
        for fi, lst in self._layout.items():
            if folders[fi].num_substreams > 1:
                for e in lst:
                    e.packed_size = 0
        #rough packed sizes for non solid folders
        for fi, f in enumerate(folders):
            lst = self._layout.get(fi, [])
            if len(lst) == 1:
                lst[0].packed_size = sum(self._si.pack_sizes[f.first_pack + k] for k in range(len(f.packed_streams)))

    def folder_supported(self, f):
        #can we decode this solid block ourselves? if not, caller hands the archive to 7-zip / py7zr
        if len(f.packed_streams) != 1 or any(c.num_in != 1 or c.num_out != 1 for c in f.coders):
            return False
        prev = None
        for c in f.coders:
            m = c.method
            if m in (C_COPY, C_LZMA, C_LZMA2, C_AES, C_DEFLATE, C_BZIP2, C_DELTA):
                pass
            elif m in _BCJ:
                pass
            else:
                return False
            prev = m
        #non x86 bcj after raw lzma1 needs the flush trick we only wrote for x86
        methods = [c.method for c in f.coders]
        if C_LZMA in methods and any(m in _BCJ and _BCJ[m] != lzma.FILTER_X86 for m in methods):
            return False
        return prev is not None or not f.coders

    def unsupported_methods(self):
        out = set()
        for f in self.folders:
            if not self.folder_supported(f):
                out.add(f.methods())
        return out

    #folder decoding
    def _pack_stream(self, si, index):
        off = 32 + si.pack_pos + sum(si.pack_sizes[:index])
        size = si.pack_sizes[index]
        return self._files.iter_range(off, size, CHUNK)

    def _decode_folder(self, si, folder_index):
        #returns a generator of decoded chunks for the folders main output
        f = si.folders[folder_index]
        if len(f.packed_streams) != 1 or any(c.num_in != 1 or c.num_out != 1 for c in f.coders):
            raise UnsupportedArchive("7z coder graph '%s' needs an external 7-Zip (BCJ2 or similar)" % f.methods())
        #walk back from the main output to the packed input -> decode order
        out_owner = []
        for ci, c in enumerate(f.coders):
            out_owner.extend([ci] * c.num_out)
        in_owner = []
        for ci, c in enumerate(f.coders):
            in_owner.extend([ci] * c.num_in)
        bound_in_to_out = {in_i: out_i for in_i, out_i in f.bind_pairs}
        order = []
        cur_out = f.main_out_index()
        seen = set()
        while True:
            ci = out_owner[cur_out]
            if ci in seen:
                raise CorruptArchive("7z coder loop")
            seen.add(ci)
            order.append((ci, cur_out))
            in_index = sum(c.num_in for c in f.coders[:ci])
            if in_index in bound_in_to_out:
                cur_out = bound_in_to_out[in_index]
            else:
                break
        order.reverse()  #now packed -> ... -> main output
        stream = self._pack_stream(si, f.first_pack)
        i = 0
        while i < len(order):
            ci, out_i = order[i]
            coder = f.coders[ci]
            size = f.unpack_sizes[out_i]
            m = coder.method
            if m in (C_LZMA, C_LZMA2):
                #grab the filters that come right after so liblzma does them in one go
                # EXCEPT bcj after lzma1: raw lzma1 has no end marker so liblzma's bcj keeps the
                # last bytes forever n the file comes out short. those go to our own bcj stage
                group = [_lzma_filter(coder)]
                j = i + 1
                while j < len(order) and len(group) < 4:
                    nxt = f.coders[order[j][0]]
                    if m == C_LZMA and nxt.method in _BCJ:
                        break
                    flt = _lzma_filter(nxt) if nxt.method not in (C_LZMA, C_LZMA2) else None
                    if flt is None:
                        break
                    group.append(flt)
                    j += 1
                last_out = order[j - 1][1]
                stream = _lzma_stage(stream, list(reversed(group)), f.unpack_sizes[last_out])
                i = j
                continue
            if m == C_COPY:
                stream = _limit(stream, size)
            elif m == C_AES:
                stream = _aes_stage(stream, coder, self.password, size)
            elif m == C_DEFLATE:
                stream = _zlib_stage(stream, size)
            elif m == C_BZIP2:
                stream = _bz2_stage(stream, size)
            elif _BCJ.get(m) == lzma.FILTER_X86:
                start_ip = struct.unpack("<I", coder.props)[0] if len(coder.props) == 4 else 0
                stream = _bcj_x86_stage(stream, size, start_ip)
            elif _lzma_filter(coder) is not None:
                raise UnsupportedArchive("7z filter %s without LZMA is not supported natively" % _NAMES.get(m, m.hex()))
            else:
                raise UnsupportedArchive("7z compression method '%s' is not supported natively" % _NAMES.get(m, m.hex()))
            i += 1
        return _limit(stream, f.unpack_size())

    #extraction
    def _extract(self, entries, targets, progress, cancel):
        prog = Progress(sum(e.size for e in entries), progress)
        by_folder = {}
        for e in entries:
            if e.index[0] == "empty":
                path = targets[e.path]
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
                continue
            by_folder.setdefault(e.index[0], []).append(e)
        for fi in sorted(by_folder):
            wanted = {e.index[1]: e for e in by_folder[fi]}
            last = max(wanted)
            f = self.folders[fi]
            try:
                start, reader = self._take_cursor(fi, min(wanted))
                for si in range(start, last + 1):
                    check_cancel(cancel)
                    size = f.sub_sizes[si]
                    e = wanted.get(si)
                    if e is None:
                        reader.skip(size, cancel)
                        continue
                    path = targets[e.path]
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    crc = 0
                    tmp = path + ".part"
                    with open(tmp, "wb") as out:
                        for chunk in reader.read_iter(size):
                            out.write(chunk)
                            crc = zlib.crc32(chunk, crc)
                            prog.add(len(chunk))
                            check_cancel(cancel)
                    if e.crc is not None and (crc & 0xFFFFFFFF) != e.crc:
                        os.remove(tmp)
                        if f.encrypted:
                            raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path))
                        raise CorruptArchive("CRC mismatch in '%s' (the archive is damaged)" % e.path)
                    os.replace(tmp, path)
            except CorruptArchive as exc:
                if f.encrypted:
                    raise WrongPassword("wrong password for '%s' (%s)" % (os.path.basename(self.path), exc)) from exc
                raise
            self._keep_cursor(fi, last + 1, reader)

    #solid blocks r one long stream, so file N means decoding files 0..N-1 first. batch importing
    #40 models out of one block restarted from 0 every time = O(n^2), 52 sec on an aes 7z lmao
    # so we park the decoder after each extract n pick it back up if the next ask is further along
    #two of them bc a job asks for its fbx (early in the block) then its textures (way later),
    #w one cursor those two keep stealing it from each other and we r back to square one
    def _take_cursor(self, fi, first):
        best = None
        for c in self._cursors:
            if c[0] == fi and c[1] <= first and (best is None or c[1] > best[1]):
                best = c
        if best is not None:
            self._cursors.remove(best)  #taken out while in use, a half read cursor must never go back
            return best[1], best[2]
        return 0, _ChunkReader(self._decode_folder(self._si, fi))

    def _keep_cursor(self, fi, nxt, reader):
        if nxt >= len(self.folders[fi].sub_sizes):
            return  #block fully read, nothing left to resume
        self._cursors.append((fi, nxt, reader))
        del self._cursors[:-2]  #each one sits on a whole lzma dictionary of ram, 2 is plenty


class _ChunkReader:
    #reads exact byte counts out of a chunk generator
    def __init__(self, gen):
        self._gen = gen
        self._buf = b""

    def read_iter(self, n):
        while n > 0:
            if not self._buf:
                self._buf = next(self._gen, b"")
                if not self._buf:
                    raise CorruptArchive("7z data ended early")
            take = self._buf[:n]
            self._buf = self._buf[len(take):]
            n -= len(take)
            yield take

    def skip(self, n, cancel=None):
        for _ in self.read_iter(n):
            check_cancel(cancel)


class _VolumeFile:
    #one or more volumes glued together (.7z or .7z.001 .002 ...)
    def __init__(self, paths):
        self._handles = []
        self._starts = []
        self._ends = []
        self.size = 0
        for p in paths:
            fh = open(p, "rb")
            fh.seek(0, 2)
            n = fh.tell()
            self._handles.append(fh)
            self._starts.append(self.size)
            self.size += n
            self._ends.append(self.size)

    @classmethod
    def open(cls, path):
        base, ext = os.path.splitext(path)
        if ext.isdigit() or (len(ext) == 4 and ext[1:].isdigit()):
            stem = base
            parts = []
            n = 1
            while True:
                p = "%s.%03d" % (stem, n)
                if not os.path.exists(p):
                    break
                parts.append(p)
                n += 1
            if parts:
                return cls(parts)
        return cls([path])

    def read_at(self, off, n):
        out = []
        for fh, start, end in zip(self._handles, self._starts, self._ends):
            if n <= 0:
                break
            if off >= end:
                continue
            fh.seek(off - start)
            take = min(n, end - off)
            data = fh.read(take)
            out.append(data)
            off += len(data)
            n -= len(data)
        return b"".join(out)

    def iter_range(self, off, size, chunk):
        left = size
        while left > 0:
            data = self.read_at(off, min(chunk, left))
            if not data:
                raise CorruptArchive("7z archive is truncated")
            off += len(data)
            left -= len(data)
            yield data

    def close(self):
        for fh in self._handles:
            try:
                fh.close()
            except OSError:
                pass
        self._handles = []
