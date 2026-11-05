# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#archive reader that drives an external tool (7-zip / unrar / unar+lsar / bsdtar)
# main use: rar (no python way to decompress it) + 7z/zip stuff our native readers cant do (ppmd, deflate64...)
#
#safety: tools extract into a private staging folder first, then we move each file to its
# sanitized target ourselves. so a cursed "../../../evil.dll" entry can never land outside the cache

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zlib

from .base import (ArchiveEntry, ArchiveReader, BackendUnavailable, Cancelled, CorruptArchive,
                   PasswordRequired, UnsupportedArchive, WrongPassword, normalize_member_path)
from .tools import subprocess_kwargs

_NO_PASSWORD = "HJT-no-password-given"  #dummy so 7z never sits there waiting for stdin


def _password_error(text):
    t = text.lower()
    return ("wrong password" in t or "incorrect password" in t or "password is incorrect" in t
            or "can not open encrypted archive" in t or "cannot open encrypted archive" in t
            or "enter password" in t or "encrypted file" in t and "password" in t
            or "missing password" in t or "password required" in t)


class CliReader(ArchiveReader):
    one_pass_preferred = True

    def __init__(self, path, password, tool, fmt):
        super().__init__(path, password)
        self.tool = tool
        self.format = fmt
        self.backend = tool.label
        if password is not None and fmt not in tool.encryption:
            raise BackendUnavailable("%s cannot open password protected %s archives" % (tool.label, fmt.upper()))
        self._list()

    #running stuff
    def _run(self, args, timeout=300):
        try:
            p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                               **subprocess_kwargs())
        except subprocess.TimeoutExpired as exc:
            raise CorruptArchive("%s timed out reading '%s'" % (self.tool.label, os.path.basename(self.path))) from exc
        except OSError as exc:
            raise BackendUnavailable("could not run %s: %s" % (self.tool.label, exc)) from exc
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def _fail(self, code, out):
        low = out.lower()
        if self.tool.kind == "bsdtar" and ("encryption is not supported" in low or "unsupported block header" in low):
            #bsdtar cant do rar passwords at all, tell ppl what to install instead
            from .tools import install_hint
            raise BackendUnavailable("'%s' is password protected and bsdtar cannot decrypt %s archives. %s" % (
                os.path.basename(self.path), self.format.upper(), install_hint(self.format)))
        if _password_error(out) or "requires a password" in low:
            if self.password is None:
                raise PasswordRequired("'%s' is password protected" % os.path.basename(self.path))
            raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path))
        tail = " ".join(out.strip().splitlines()[-3:])[:300]
        if "unsupported" in out.lower() or "can not open the file as archive" in out.lower() or "cannot open the file as archive" in out.lower():
            raise UnsupportedArchive("%s cannot open '%s': %s" % (self.tool.label, os.path.basename(self.path), tail))
        raise CorruptArchive("%s failed on '%s' (exit %s): %s" % (self.tool.label, os.path.basename(self.path), code, tail))

    def _pw_args(self):
        k = self.tool.kind
        if k == "7z":
            return ["-p" + (self.password if self.password is not None else _NO_PASSWORD)]
        if k == "unrar":
            return ["-p" + self.password] if self.password is not None else ["-p-"]
        if k == "unar":
            return ["-p", self.password] if self.password is not None else []
        return []

    #listing
    def _list(self):
        k = self.tool.kind
        if k == "7z":
            self._list_7z()
        elif k == "unrar":
            self._list_unrar()
        elif k == "unar":
            self._list_lsar()
        elif k == "bsdtar":
            self._list_bsdtar()
        else:
            raise BackendUnavailable("unknown tool %s" % k)

    def _list_7z(self):
        code, out = self._run([self.tool.path, "l", "-slt", "-sccUTF-8"] + self._pw_args() + [self.path])
        if code not in (0, 1) or "----------" not in out:
            self._fail(code, out)
        body = out.split("----------", 1)[1]
        entries = []
        for block in re.split(r"\r?\n\s*\r?\n", body):
            info = {}
            for line in block.splitlines():
                if " = " in line:
                    k, v = line.split(" = ", 1)
                    info[k.strip()] = v
            raw = info.get("Path")
            if raw is None:
                continue
            attrs = info.get("Attributes", "")
            is_dir = info.get("Folder") == "+" or attrs.startswith("D")
            crc = info.get("CRC", "")
            e = ArchiveEntry(normalize_member_path(raw), _int(info.get("Size")), _int(info.get("Packed Size")),
                             int(crc, 16) if re.fullmatch(r"[0-9A-Fa-f]{8}", crc or "") else None, is_dir,
                             info.get("Encrypted") == "+", None, raw, info.get("Method", ""))
            if e.path:
                entries.append(e)
        self.entries = entries
        if "Solid = +" in out:
            self.solid = True

    def _list_unrar(self):
        code, out = self._run([self.tool.path, "lt", "-cfg-", "-scf"] + self._pw_args() + [self.path])
        if code != 0 or "Name:" not in out:
            if code == 0 and "Name:" not in out and "Details:" in out:
                self.entries = []
                return
            self._fail(code, out)
        entries = []
        for block in re.split(r"\r?\n\s*\r?\n", out):
            info = {}
            for line in block.splitlines():
                m = re.match(r"\s*([A-Za-z0-9 ]+?):\s(.*)$", line)
                if m:
                    info.setdefault(m.group(1).strip(), m.group(2))
            raw = info.get("Name")
            if raw is None:
                continue
            typ = info.get("Type", "File")
            crc = (info.get("CRC32") or "").strip()
            e = ArchiveEntry(normalize_member_path(raw), _int(info.get("Size")), _int(info.get("Packed size")),
                             int(crc, 16) if re.fullmatch(r"[0-9A-Fa-f]{8}", crc) else None, typ.lower().startswith("dir"),
                             "encrypted" in (info.get("Flags") or "").lower(), None, raw, info.get("Compression", ""))
            if e.path and not typ.lower().startswith("symbolic"):
                entries.append(e)
        self.entries = entries
        if "solid" in out.lower().split("details:", 1)[-1][:200]:
            self.solid = True

    def _list_lsar(self):
        args = [self.tool.lsar, "-j"] + self._pw_args() + [self.path]
        code, out = self._run(args)
        try:
            data = json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            self._fail(code, out)
        err = data.get("lsarError")
        if err:
            #lsar prints a plain text hint before the json n uses error 15 for bad passwords
            head = out[:out.index("{")]
            if err == 15 or "password" in head.lower():
                self._fail(code, head + " wrong password")
            self._fail(code, head + " lsar error %s" % err)
        entries = []
        for item in data.get("lsarContents", []):
            raw = item.get("XADFileName", "")
            e = ArchiveEntry(normalize_member_path(raw), int(item.get("XADFileSize", 0) or 0),
                             int(item.get("XADCompressedSize", 0) or 0), item.get("XADCRC32"),
                             bool(item.get("XADIsDirectory")), bool(item.get("XADIsEncrypted")), None,
                             item.get("XADIndex"), str(item.get("XADCompressionName", "")))
            if e.path:
                entries.append(e)
        self.entries = entries
        if any(item.get("XADIsSolid") for item in data.get("lsarContents", [])):
            self.solid = True

    def _list_bsdtar(self):
        code, out = self._run([self.tool.path, "-tvf", self.path])
        if code != 0:
            self._fail(code, out)
        entries = []
        pat = re.compile(r"^(\S{10})\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w{3}\s+\d+\s+[\d:]+\s(.*)$")
        for line in out.splitlines():
            m = pat.match(line)
            if not m:
                continue
            mode, size, name = m.group(1), int(m.group(2)), m.group(3)
            if mode.startswith("l") and " -> " in name:
                continue
            raw = name.rstrip("/") if mode.startswith("d") else name
            e = ArchiveEntry(normalize_member_path(raw), size, 0, None, mode.startswith("d"), False, None, raw, "")
            if e.path:
                entries.append(e)
        self.entries = entries

    #extraction
    def _extract(self, entries, targets, progress, cancel):
        root = os.path.dirname(os.path.commonpath(list(targets.values()))) if len(targets) > 1 else os.path.dirname(next(iter(targets.values())))
        os.makedirs(root, exist_ok=True)
        staging = os.path.join(root, ".hjt_stage_" + uuid.uuid4().hex[:10])
        os.makedirs(staging)
        listfile = None
        try:
            k = self.tool.kind
            total = sum(e.size for e in entries)
            if k == "unar":
                args = [self.tool.path, "-o", staging, "-f", "-q", "-D", "-i"] + self._pw_args() + [self.path] + [str(e.index) for e in entries]
            else:
                fd, listfile = tempfile.mkstemp(prefix="hjt_list_", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    for e in entries:
                        name = e.index if isinstance(e.index, str) else e.path
                        if k == "bsdtar":
                            name = re.sub(r"([*?\[\]\\])", r"\\\1", name)
                        fh.write(name + "\n")
                if k == "7z":
                    args = [self.tool.path, "x", "-y", "-aoa", "-spd", "-sccUTF-8", "-scsUTF-8", "-o" + staging] + self._pw_args() + [self.path, "@" + listfile]
                elif k == "unrar":
                    args = [self.tool.path, "x", "-y", "-o+", "-cfg-", "-scfl", "-idq"] + self._pw_args() + [self.path, "@" + listfile, staging + os.sep]
                else:
                    args = [self.tool.path, "-x", "-f", self.path, "-C", staging, "-T", listfile]
            code, out = self._run_watched(args, staging, total, progress, cancel)
            ok_codes = (0, 1) if k in ("7z", "unrar") else (0,)
            low = out.lower()
            if code not in ok_codes or "unsupported method" in low or "error:" in low and k == "7z":
                #check the tool said ok BEFORE trusting files, failed tools love leaving empty files behind
                if "unsupported method" in low:
                    raise UnsupportedArchive("%s cannot decompress this %s archive (missing codec). %s" % (
                        self.tool.label, self.format.upper(), "Install the full 7-Zip or UnRAR."))
                self._fail(code, out)
            found = _index_files(staging)
            missing = []
            for e in entries:
                src = _locate(found, staging, e)
                if src is None:
                    missing.append(e)
                    continue
                dst = targets[e.path]
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
                if e.crc is not None and isinstance(e.crc, int):
                    if _file_crc(dst) != e.crc:
                        os.remove(dst)
                        if e.encrypted:
                            raise WrongPassword("wrong password for '%s'" % os.path.basename(self.path))
                        raise CorruptArchive("CRC mismatch in '%s' (the archive is damaged)" % e.path)
            if missing:
                self._fail(code, out + "\nmissing: " + ", ".join(m.path for m in missing[:5]))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if listfile:
                try:
                    os.remove(listfile)
                except OSError:
                    pass

    def _run_watched(self, args, staging, total, progress, cancel):
        #run w cancel + progress (we just watch the staging folder grow, tools all print progress differently ugh)
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **subprocess_kwargs())
        except OSError as exc:
            raise BackendUnavailable("could not run %s: %s" % (self.tool.label, exc)) from exc
        chunks = []
        last = 0.0
        while True:
            try:
                out, _ = proc.communicate(timeout=0.25)
                if out:
                    chunks.append(out)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise Cancelled("cancelled")
            now = time.monotonic()
            if progress is not None and now - last > 0.5:
                last = now
                progress(min(total, _dir_size(staging)), max(1, total))
        if progress is not None:
            progress(total, max(1, total))
        return proc.returncode, b"".join(chunks).decode("utf-8", "replace")


def _int(v):
    try:
        return int(str(v).strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return 0


def _dir_size(path):
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def _index_files(staging):
    out = {}
    for root, _dirs, files in os.walk(staging):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, staging).replace(os.sep, "/")
            out.setdefault(rel.lower(), full)
    return out


def _locate(found, staging, entry):
    p = found.get(entry.path.lower())
    if p is not None and os.path.isfile(p):
        found.pop(entry.path.lower(), None)
        return p
    #windows tools replace : * ? etc w _ , so match by basename + size as a fallback
    base = entry.name.lower()
    for rel, full in list(found.items()):
        if rel.rsplit("/", 1)[-1] == base or rel.rsplit("/", 1)[-1].replace("_", "") == re.sub(r'[<>:"|?*]', "", base):
            try:
                if os.path.getsize(full) == entry.size:
                    found.pop(rel, None)
                    return full
            except OSError:
                continue
    return None


def _file_crc(path):
    crc = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            crc = zlib.crc32(b, crc)
    return crc & 0xFFFFFFFF


class Py7zrReader(ArchiveReader):
    one_pass_preferred = True
    #optional backend if the user installed py7zr (handles ppmd, bcj2, zstd w its deps)
    format = "7z"
    backend = "py7zr"

    def __init__(self, path, password=None):
        super().__init__(path, password)
        try:
            import py7zr  # noqa: F401
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001
            raise BackendUnavailable("py7zr is not installed or cannot be loaded") from exc
        import py7zr
        try:
            self._z = py7zr.SevenZipFile(path, mode="r", password=password)
        except py7zr.exceptions.PasswordRequired as exc:
            raise PasswordRequired("'%s' is password protected" % os.path.basename(path)) from exc
        except Exception as exc:
            msg = str(exc).lower()
            if "password" in msg:
                raise WrongPassword("wrong password for '%s'" % os.path.basename(path)) from exc
            raise CorruptArchive("py7zr cannot read '%s': %s" % (os.path.basename(path), exc)) from exc
        entries = []
        for info in self._z.list():
            e = ArchiveEntry(normalize_member_path(info.filename), info.uncompressed or 0, info.compressed or 0,
                             info.crc32, info.is_directory, bool(password) or self._z.needs_password(), None,
                             info.filename, "")
            if e.path:
                entries.append(e)
        self.entries = entries

    def _extract(self, entries, targets, progress, cancel):
        root = os.path.dirname(next(iter(targets.values())))
        staging = os.path.join(root, ".hjt_stage_" + uuid.uuid4().hex[:10])
        os.makedirs(staging, exist_ok=True)
        try:
            self._z.reset()
            self._z.extract(path=staging, targets=[e.index for e in entries])
            found = _index_files(staging)
            for e in entries:
                src = _locate(found, staging, e)
                if src is None:
                    raise CorruptArchive("py7zr did not extract '%s'" % e.path)
                os.makedirs(os.path.dirname(targets[e.path]), exist_ok=True)
                os.replace(src, targets[e.path])
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def close(self):
        try:
            self._z.close()
        except Exception:
            pass
