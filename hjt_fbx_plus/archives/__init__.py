# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#archive front door. open_archive() figures out the format from magic bytes n picks a backend:
#  zip -> built in (stdlib + our aes), external tool for deflate64 etc
#  7z  -> built in (stdlib lzma + our aes), external 7-zip / py7zr for ppmd bcj2 zstd
#  rar -> external tool only (7-zip, unrar, the unarchiver or bsdtar)
# no bpy anywhere in here

from __future__ import annotations

import os

from .base import (ArchiveEntry, ArchiveError, ArchiveReader, BackendUnavailable, Cancelled,  # noqa: F401
                   CorruptArchive, PasswordRequired, UnsupportedArchive, WrongPassword)
from . import tools as _tools

SUPPORTED_EXTENSIONS = (".zip", ".7z", ".rar")


def detect_format(path):
    #magic bytes first, extension as a fallback (sfx stubs, renamed files etc)
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        raise ArchiveError("cannot open '%s': %s" % (path, exc)) from exc
    if not head:
        raise CorruptArchive("'%s' is empty" % os.path.basename(path))
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    low = path.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(".7z") or ".7z.0" in low:
        return "7z"
    if low.endswith(".rar") or low.endswith(".r00"):
        return "rar"
    return None


def is_archive_path(path):
    low = path.lower()
    return low.endswith(SUPPORTED_EXTENSIONS) or (".7z.0" in low and low[-3:].isdigit())


def open_archive(path, password=None, tool_paths=None, allow_external=True):
    #-> ArchiveReader. raises PasswordRequired / WrongPassword / UnsupportedArchive / CorruptArchive /
    # BackendUnavailable w messages good enough to show the user directly
    fmt = detect_format(path)
    if fmt is None:
        raise UnsupportedArchive("'%s' is not a ZIP, 7Z or RAR archive" % os.path.basename(path))
    found = _tools.detect(tool_paths) if allow_external else {}

    def external(reason):
        tool = _tools.pick(found, fmt, need_password=password is not None)
        if tool is None:
            return None
        from .cli import CliReader
        r = CliReader(path, password, tool, fmt)
        r.notes.append(reason)
        return r

    if fmt == "zip":
        from .zipfmt import ZipReader
        try:
            r = ZipReader(path, password)
        except CorruptArchive as exc:
            alt = external("Built-in ZIP reader failed (%s); using %s." % (exc, "external tool"))
            if alt is not None:
                return alt
            raise
        if r.unsupported_methods:
            from .zipfmt import _method_name
            names = ", ".join(sorted(_method_name(m) for m in r.unsupported_methods))
            alt = external("Some entries use compression %s, so an external tool is used." % names)
            if alt is not None:
                r.close()
                return alt
            r.notes.append("Some entries use unsupported compression (%s). %s" % (names, _tools.install_hint("zip")))
        return r

    if fmt == "7z":
        from .sevenzip import SevenZipReader
        try:
            r = SevenZipReader(path, password)
        except UnsupportedArchive as exc:
            alt = external(str(exc))
            if alt is not None:
                return alt
            alt = _py7zr(path, password)
            if alt is not None:
                return alt
            raise UnsupportedArchive("%s. %s" % (exc, _tools.install_hint("7z"))) from exc
        bad = r.unsupported_methods()
        if bad:
            reason = "This 7z uses %s, which the built-in reader cannot decode." % ", ".join(sorted(bad))
            alt = external(reason)
            if alt is None:
                alt = _py7zr(path, password)
            if alt is not None:
                r.close()
                return alt
            r.notes.append(reason + " " + _tools.install_hint("7z"))
        return r

    #rar
    multi = _is_multivolume_rar(path)
    tool = _tools.pick(found, "rar", need_password=password is not None, multivolume=multi)
    if tool is None:
        if password is not None and _tools.pick(found, "rar"):
            raise BackendUnavailable("The available tool (%s) cannot open password protected RAR files. %s" % (
                _tools.pick(found, "rar").label, _tools.install_hint("rar")))
        raise BackendUnavailable("RAR archives need an external extractor. " + _tools.install_hint("rar"))
    from .cli import CliReader
    return CliReader(path, password, tool, "rar")


def _is_multivolume_rar(path):
    low = os.path.basename(path).lower()
    import re
    if re.search(r"\.part\d+\.rar$", low):
        return True
    stem = os.path.splitext(path)[0]
    return os.path.exists(stem + ".r00") or os.path.exists(stem + ".R00")


def _py7zr(path, password):
    try:
        from .cli import Py7zrReader
        return Py7zrReader(path, password)
    except (BackendUnavailable, ImportError):
        return None


def backend_report(tool_paths=None):
    #what can this machine do, for the prefs n the archive panel
    found = _tools.detect(tool_paths)
    from . import crypto
    return {
        "zip": "built-in" + ("" if not found else " (+ %s for exotic methods)" % ", ".join(t.label for t in found.values() if "zip" in t.formats)),
        "7z": "built-in (LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/AES)" + ("" if not found else " + %s" % ", ".join(t.label for t in found.values() if "7z" in t.formats)),
        "rar": ", ".join(t.label for t in found.values() if "rar" in t.formats) or "not available (needs 7-Zip, UnRAR, unar or bsdtar)",
        "aes": crypto.backend_name(),
        "tools": {k: v.as_dict() for k, v in found.items()},
    }
