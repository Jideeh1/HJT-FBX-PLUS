# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#shared archive stuff: entry class, errors, the reader base n safe path handling
# no bpy here, all of this runs in worker threads

from __future__ import annotations

import os
import re
import threading

IMAGE_EXTS = frozenset((".png", ".tga", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".dds", ".exr",
                        ".hdr", ".psd", ".webp", ".ktx", ".gif"))
FBX_EXTS = frozenset((".fbx", ".uemodel"))  #"fbx" kind = anything importable as a model
DATA_EXTS = frozenset((".json", ".mat", ".txt", ".xml", ".yaml", ".yml"))
ARCHIVE_EXTS = frozenset((".zip", ".7z", ".rar"))


class ArchiveError(Exception):
    #base for everything archive related, message is always human readable
    pass


class PasswordRequired(ArchiveError):
    pass


class WrongPassword(ArchiveError):
    pass


class UnsupportedArchive(ArchiveError):
    pass


class CorruptArchive(ArchiveError):
    pass


class BackendUnavailable(ArchiveError):
    #the format is fine but nothing on this machine can open it (no 7-zip/unrar etc)
    pass


class Cancelled(ArchiveError):
    pass


class ArchiveEntry:
    __slots__ = ("path", "size", "packed_size", "crc", "is_dir", "encrypted", "mtime", "index",
                 "method", "folder", "extra")

    def __init__(self, path, size=0, packed_size=0, crc=None, is_dir=False, encrypted=False,
                 mtime=None, index=None, method="", folder=None, extra=None):
        self.path = path          #normalized posix path inside the archive
        self.size = size
        self.packed_size = packed_size
        self.crc = crc
        self.is_dir = is_dir
        self.encrypted = encrypted
        self.mtime = mtime
        self.index = index        #whatever the backend needs to find it again
        self.method = method
        self.folder = folder      # 7z solid block index (None elsewhere)
        self.extra = extra

    @property
    def name(self):
        return self.path.rsplit("/", 1)[-1]

    @property
    def ext(self):
        n = self.name
        i = n.rfind(".")
        return n[i:].lower() if i > 0 else ""

    @property
    def kind(self):
        if self.is_dir:
            return "dir"
        e = self.ext
        if e in FBX_EXTS:
            return "fbx"
        if e in IMAGE_EXTS:
            return "image"
        if e in DATA_EXTS:
            return "data"
        if e in ARCHIVE_EXTS:
            return "archive"
        return "other"

    def identity(self):
        #cheap content identity for dedupe: same size + crc = same file basically
        if self.crc is None:
            return None
        return (self.size, self.crc)

    def __repr__(self):
        return "<ArchiveEntry %r %d%s>" % (self.path, self.size, " enc" if self.encrypted else "")


def normalize_member_path(raw):
    #archive member name -> clean posix path. backslashes, ./ n leading slashes gone
    p = raw.replace("\\", "/")
    parts = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = frozenset(["CON", "PRN", "AUX", "NUL"] + ["COM%d" % i for i in range(1, 10)] +
                      ["LPT%d" % i for i in range(1, 10)])


def safe_relpath(member_path):
    #member path -> relative path thats safe to write on ANY os. zip slip begone
    out = []
    for part in normalize_member_path(member_path).split("/"):
        if not part:
            continue
        if len(part) == 2 and part[1] == ":":
            continue  #drive letter lol nice try
        part = _BAD_CHARS.sub("_", part).rstrip(" .")
        if not part:
            part = "_"
        if part.split(".")[0].upper() in _RESERVED:
            part = "_" + part
        out.append(part[:200])
    return "/".join(out) if out else "_unnamed"


class ArchiveReader:
    #base class. subclasses fill self.entries in open n implement _extract
    format = ""
    backend = ""
    #true when many small extract() calls cost way more than one big one (external tools: a process
    # per call n solid streams decoded from the start every time). the pipeline batches those
    one_pass_preferred = False

    def __init__(self, path, password=None):
        self.path = path
        self.password = password
        self.entries = []
        self.header_encrypted = False
        self.solid = False
        self.notes = []
        self._lock = threading.Lock()

    #info
    @property
    def has_encrypted(self):
        return self.header_encrypted or any(e.encrypted for e in self.entries)

    def files(self):
        return [e for e in self.entries if not e.is_dir]

    def find(self, member_path):
        if not hasattr(self, "_by_path"):
            self._by_path = {e.path: e for e in self.entries}
            self._by_lower = {}
            for e in self.entries:
                self._by_lower.setdefault(e.path.lower(), e)
        e = self._by_path.get(member_path)
        if e is None:
            e = self._by_lower.get(member_path.lower())
        return e

    #extraction
    def extract(self, entries, dest_dir, progress=None, cancel=None):
        #extract entries under dest_dir keeping their (sanitized) relative paths
        # returns {entry.path: absolute local path}. progress(done_bytes, total_bytes)
        entries = [e for e in entries if not e.is_dir]
        if not entries:
            return {}
        os.makedirs(dest_dir, exist_ok=True)
        targets = plan_targets(entries, dest_dir)
        if self.password is None and any(e.encrypted for e in entries):
            raise PasswordRequired("'%s' is password protected" % os.path.basename(self.path))
        with self._lock:
            self._extract(entries, targets, progress, cancel)
        return {e.path: targets[e.path] for e in entries}

    def read(self, entry, limit=64 * 1024 * 1024):
        #small files straight into memory (json, txt)
        if entry.size > limit:
            raise ArchiveError("'%s' is too large to read into memory" % entry.path)
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="hjt_read_")
        try:
            out = self.extract([entry], tmp)
            with open(out[entry.path], "rb") as fh:
                return fh.read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _extract(self, entries, targets, progress, cancel):
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def plan_targets(entries, dest_dir):
    #figure out where each entry goes. case insensitive clashes get a ~N suffix
    # bc windows n macos think Tex/A.png n tex/a.png r the same file
    dest_dir = os.path.abspath(dest_dir)
    used = {}
    targets = {}
    long_paths = os.name == "nt"
    for e in entries:
        rel = safe_relpath(e.path)
        key = rel.lower()
        if key in used and used[key] != e.path:
            stem, dot, ext = rel.rpartition(".")
            n = 1
            while True:
                cand = ("%s~%d.%s" % (stem, n, ext)) if dot else ("%s~%d" % (rel, n))
                if cand.lower() not in used:
                    rel = cand
                    key = rel.lower()
                    break
                n += 1
        used[key] = e.path
        full = os.path.join(dest_dir, *rel.split("/"))
        if long_paths and len(full) > 240:
            #windows MAX_PATH says hi. flatten into a short hashed name instead
            import hashlib
            h = hashlib.sha1(e.path.encode("utf-8", "surrogateescape")).hexdigest()[:12]
            full = os.path.join(dest_dir, "_long", h + "_" + rel.rsplit("/", 1)[-1][-60:])
        targets[e.path] = full
    return targets


class Progress:
    #thread safe byte counter that throttles the callback a bit
    def __init__(self, total, callback):
        self.total = max(1, total)
        self.done = 0
        self.cb = callback
        self._last = 0

    def add(self, n):
        self.done += n
        if self.cb is not None and (self.done - self._last >= 262144 or self.done >= self.total):
            self._last = self.done
            self.cb(self.done, self.total)


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")
