# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#zip reader on top of stdlib zipfile
# + winzip aes (method 99) which zipfile just refuses to do
# + smarter name decoding: zips made on a chinese/japanese/korean windows w/o the utf8 flag
#   come out as cp437 garbage in zipfile, we try to recover the real names
#zipcrypto (the old weak one) is handled by zipfile itself

from __future__ import annotations

import bz2
import hashlib
import hmac
import lzma
import os
import struct
import zipfile
import zlib

from .base import (ArchiveEntry, ArchiveReader, CorruptArchive, PasswordRequired, Progress,
                   UnsupportedArchive, WrongPassword, check_cancel, normalize_member_path)
from . import crypto

CHUNK = 1 << 20
_FALLBACK_ENCODINGS = ("utf-8", "shift_jis", "gbk", "big5", "cp949")
_SUPPORTED_METHODS = {0, 8, 12, 14}  #store deflate bzip2 lzma


def is_zip(prefix):
    return bytes(prefix[:4]) in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _decode_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _aes_extra(info):
    #find the 0x9901 extra field -> (version, strength, real method) or None
    extra = info.extra
    i = 0
    while i + 4 <= len(extra):
        hid, size = struct.unpack_from("<HH", extra, i)
        if hid == 0x9901 and size >= 7:
            ver, vendor, strength, method = struct.unpack_from("<H2sBH", extra, i + 4)
            return ver, strength, method
        i += 4 + size
    return None


class ZipReader(ArchiveReader):
    format = "zip"
    backend = "zip (built-in)"

    def __init__(self, path, password=None):
        super().__init__(path, password)
        try:
            self._zf = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise CorruptArchive("'%s' is not a valid zip (damaged or incomplete): %s" % (os.path.basename(path), exc)) from exc
        except (OSError, EOFError) as exc:
            raise CorruptArchive("cannot read '%s': %s" % (os.path.basename(path), exc)) from exc
        self.unsupported_methods = set()
        entries = []
        for info in self._zf.infolist():
            name = normalize_member_path(_decode_name(info))
            if not name:
                continue
            enc = bool(info.flag_bits & 0x1)
            method = info.compress_type
            aes = _aes_extra(info) if method == 99 else None
            real_method = aes[2] if aes else method
            if real_method not in _SUPPORTED_METHODS and not info.is_dir():
                self.unsupported_methods.add(real_method)
            e = ArchiveEntry(name, info.file_size, info.compress_size, info.CRC, info.is_dir(), enc,
                             None, info, _method_name(real_method) + (" AES-%d" % (64 + 64 * aes[1]) if aes else (" ZipCrypto" if enc else "")))
            try:
                import time
                e.mtime = time.mktime(info.date_time + (0, 0, -1))
            except (OverflowError, ValueError):
                pass
            entries.append(e)
        self.entries = entries

    def close(self):
        try:
            self._zf.close()
        except Exception:
            pass

    def _extract(self, entries, targets, progress, cancel):
        prog = Progress(sum(e.size for e in entries), progress)
        pwd = self.password.encode("utf-8") if isinstance(self.password, str) else self.password
        for e in entries:
            check_cancel(cancel)
            info = e.index
            path = targets[e.path]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            try:
                if info.compress_type == 99:
                    self._extract_aes(info, tmp, pwd, prog, cancel)
                else:
                    real = info.compress_type
                    if real not in _SUPPORTED_METHODS:
                        raise UnsupportedArchive("zip compression '%s' is not supported by Python's zipfile" % _method_name(real))
                    with self._zf.open(info, "r", pwd=pwd if e.encrypted else None) as src, open(tmp, "wb") as out:
                        while True:
                            chunk = src.read(CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            prog.add(len(chunk))
                            check_cancel(cancel)
                os.replace(tmp, path)
            except RuntimeError as exc:
                _cleanup(tmp)
                msg = str(exc).lower()
                if "password" in msg:
                    if pwd is None:
                        raise PasswordRequired("'%s' is password protected" % os.path.basename(self.path)) from exc
                    raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path)) from exc
                raise CorruptArchive(str(exc)) from exc
            except (zipfile.BadZipFile, zlib.error, EOFError, lzma.LZMAError, OSError) as exc:
                _cleanup(tmp)
                if isinstance(exc, OSError) and not isinstance(exc, (EOFError,)) and exc.errno is not None:
                    raise
                if e.encrypted and pwd is not None:
                    #zipcrypto only has a 1 byte check so a wrong pw can slip past it n then the crc fails
                    raise WrongPassword("wrong password for '%s' (data check failed)" % os.path.basename(self.path)) from exc
                raise CorruptArchive("'%s' in '%s' is damaged: %s" % (e.path, os.path.basename(self.path), exc)) from exc
            except BaseException:
                _cleanup(tmp)
                raise

    def _extract_aes(self, info, tmp, pwd, prog, cancel):
        #winzip ae-1/ae-2. layout: salt + 2 byte pw check + encrypted data + 10 byte hmac
        if pwd is None:
            raise PasswordRequired("'%s' is password protected" % os.path.basename(self.path))
        aes = _aes_extra(info)
        if aes is None:
            raise CorruptArchive("AES zip entry without AES header")
        ver, strength, method = aes
        if strength not in (1, 2, 3):
            raise CorruptArchive("unknown AES strength %d" % strength)
        if method not in _SUPPORTED_METHODS:
            raise UnsupportedArchive("zip compression '%s' is not supported" % _method_name(method))
        key_len = 8 + 8 * strength
        salt_len = 4 + 4 * strength
        with open(self.path, "rb") as fh:
            fh.seek(info.header_offset)
            hdr = fh.read(30)
            if hdr[:4] != b"PK\x03\x04":
                raise CorruptArchive("bad local header for '%s'" % info.filename)
            nlen, xlen = struct.unpack_from("<HH", hdr, 26)
            fh.seek(info.header_offset + 30 + nlen + xlen)
            salt = fh.read(salt_len)
            check = fh.read(2)
            dk = hashlib.pbkdf2_hmac("sha1", pwd, salt, 1000, 2 * key_len + 2)
            if dk[2 * key_len:] != check:
                raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path))
            mac = hmac.new(dk[key_len:2 * key_len], digestmod=hashlib.sha1)
            ctr = crypto.WinZipCtr(dk[:key_len])
            left = info.compress_size - salt_len - 2 - 10
            if left < 0:
                raise CorruptArchive("AES entry too small")
            dec = _Inflater(method, info.file_size)
            crc = 0
            with open(tmp, "wb") as out:
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        raise CorruptArchive("zip is truncated")
                    left -= len(chunk)
                    mac.update(chunk)
                    plain = ctr.process(chunk)
                    for data in dec.feed(plain):
                        out.write(data)
                        crc = zlib.crc32(data, crc)
                        prog.add(len(data))
                    check_cancel(cancel)
                for data in dec.finish():
                    out.write(data)
                    crc = zlib.crc32(data, crc)
                    prog.add(len(data))
            auth = fh.read(10)
        if not hmac.compare_digest(mac.digest()[:10], auth):
            raise CorruptArchive("AES authentication failed for '%s' (damaged data)" % info.filename)
        if ver == 1 and (crc & 0xFFFFFFFF) != info.CRC:
            raise CorruptArchive("CRC mismatch in '%s'" % info.filename)


class _Inflater:
    #decompressor for the payload inside an aes entry
    def __init__(self, method, size):
        self.method = method
        self.size = size
        self.done = 0
        if method == 8:
            self.d = zlib.decompressobj(-15)
        elif method == 12:
            self.d = bz2.BZ2Decompressor()
        elif method == 14:
            self.d = None  #lzma needs its 4 byte header first
            self._hdr = b""
        else:
            self.d = None

    def feed(self, data):
        if self.method == 0:
            yield data
            return
        if self.method == 14 and self.d is None:
            self._hdr += data
            if len(self._hdr) < 4:
                return
            psize = struct.unpack_from("<H", self._hdr, 2)[0]
            if len(self._hdr) < 4 + psize:
                return
            props = self._hdr[4:4 + psize]
            d = props[0]
            filt = {"id": lzma.FILTER_LZMA1, "lc": d % 9, "lp": (d // 9) % 5, "pb": d // 45,
                    "dict_size": max(4096, struct.unpack_from("<I", props, 1)[0])}
            self.d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt])
            data = self._hdr[4 + psize:]
            self._hdr = b""
        if self.method == 8:
            out = self.d.decompress(data, CHUNK)
            while out:
                yield out
                out = self.d.decompress(self.d.unconsumed_tail, CHUNK) if self.d.unconsumed_tail else b""
        else:
            out = self.d.decompress(data)
            if out:
                yield out[:max(0, self.size - self.done)]
                self.done += len(out)

    def finish(self):
        if self.method == 8 and self.d is not None:
            tail = self.d.flush()
            if tail:
                yield tail
        return


def _method_name(m):
    return {0: "Store", 8: "Deflate", 9: "Deflate64", 12: "BZip2", 14: "LZMA", 93: "Zstd",
            95: "XZ", 98: "PPMd", 99: "AES"}.get(m, "method %d" % m)


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass
