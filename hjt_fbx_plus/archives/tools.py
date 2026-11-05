# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#finds external archive tools on this machine: 7-zip (7z 7zz 7za), unrar, the unarchiver (unar/lsar), bsdtar
# rar can only be unpacked w one of these (rar compression is closed n we r not writing an unrar in python lol)
#
# fun facts: macos /usr/bin/tar IS bsdtar n reads rar5, windows 11 tar.exe too.
# so a lot of ppl already have something that works w/o installing anything

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading

_CACHE = {}
_LOCK = threading.Lock()

CREATE_NO_WINDOW = 0x08000000


def subprocess_kwargs():
    #no console windows popping up on windows pls
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    env = dict(os.environ)
    env.setdefault("LC_ALL", "C.UTF-8") if sys.platform.startswith("linux") else None
    env["LANG"] = env.get("LANG") or "C.UTF-8"
    kw["env"] = env
    return kw


class ToolInfo:
    def __init__(self, kind, path, version="", formats=(), encryption=(), rar5=False, label=""):
        self.kind = kind            #"7z" "unrar" "unar" "bsdtar"
        self.path = path
        self.version = version
        self.formats = set(formats)  #which formats it can READ
        self.encryption = set(encryption)  #formats where it can do passwords
        self.rar5 = rar5
        self.label = label or kind

    def as_dict(self):
        return {"kind": self.kind, "path": self.path, "version": self.version,
                "formats": sorted(self.formats), "encryption": sorted(self.encryption), "label": self.label}

    def __repr__(self):
        return "<ToolInfo %s %s %s>" % (self.kind, self.version, self.path)


def _run(args, timeout=8):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                           **subprocess_kwargs())
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _candidates():
    c = {"7z": [], "unrar": [], "unar": [], "bsdtar": []}
    for name in ("7zz", "7z", "7za", "7zr"):
        p = shutil.which(name)
        if p:
            c["7z"].append(p)
    for name in ("unrar", "rar"):
        p = shutil.which(name)
        if p:
            c["unrar"].append(p)
    p = shutil.which("unar")
    if p:
        c["unar"].append(p)
    p = shutil.which("bsdtar")
    if p:
        c["bsdtar"].append(p)
    if os.name == "nt":
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
              os.environ.get("ProgramW6432", r"C:\Program Files")]
        local = os.environ.get("LOCALAPPDATA", "")
        for base in pf:
            c["7z"] += [os.path.join(base, "7-Zip", "7z.exe"), os.path.join(base, "7-Zip-Zstandard", "7z.exe"),
                        os.path.join(base, "PeaZip", "res", "bin", "7z", "7z.exe")]
            c["unrar"] += [os.path.join(base, "WinRAR", "UnRAR.exe"), os.path.join(base, "WinRAR", "Rar.exe")]
        if local:
            c["7z"].append(os.path.join(local, "Programs", "7-Zip", "7z.exe"))
        nana = shutil.which("NanaZipC") or shutil.which("NanaZipC.exe")
        if nana:
            c["7z"].append(nana)
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        c["bsdtar"].append(os.path.join(sysroot, "System32", "tar.exe"))
    elif sys.platform == "darwin":
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
            c["7z"] += [os.path.join(base, n) for n in ("7zz", "7z", "7za")]
            c["unrar"].append(os.path.join(base, "unrar"))
            c["unar"].append(os.path.join(base, "unar"))
            c["bsdtar"].append(os.path.join(base, "bsdtar"))
        c["7z"].append("/Applications/Keka.app/Contents/Resources/keka7z")
        c["bsdtar"] += ["/usr/bin/bsdtar", "/usr/bin/tar"]
    else:
        for base in ("/usr/bin", "/usr/local/bin", "/snap/bin", os.path.expanduser("~/.local/bin")):
            c["7z"] += [os.path.join(base, n) for n in ("7zz", "7z", "7za")]
            c["unrar"].append(os.path.join(base, "unrar"))
            c["unar"].append(os.path.join(base, "unar"))
            c["bsdtar"].append(os.path.join(base, "bsdtar"))
    out = {}
    for k, lst in c.items():
        seen = []
        for p in lst:
            if p and os.path.isfile(p):
                rp = os.path.realpath(p)
                if rp not in [os.path.realpath(s) for s in seen]:
                    seen.append(p)
        out[k] = seen
    return out


def probe(kind, path):
    #run the tool once to make sure it works n see what it can do
    if kind == "7z":
        code, out = _run([path, "i"], timeout=10)
        if code is None or "7-Zip" not in out:
            return None
        m = re.search(r"7-Zip\S*\s+(?:\(\w+\)\s+|\[\d+\]\s+)?([\d.]+)", out)
        version = m.group(1) if m else ""
        formats = {"7z"}
        low = out.lower()
        if re.search(r"\bzip\b", low):
            formats.add("zip")
        #listing rar needs the Rar format handler, EXTRACTING needs the rar codecs. debian/ubuntu "7zip"
        # ships the handler w/o the non-free codec so it lists fine then spits "Unsupported Method" lol
        codecs = low.split("codecs:", 1)[1] if "codecs:" in low else ""
        has_handler = bool(re.search(r"\brar5?\b", low.split("codecs:", 1)[0]))
        has_codec = bool(re.search(r"\brar[1-5]\b", codecs)) if codecs else has_handler
        has_rar = has_handler and has_codec
        if has_rar:
            formats.add("rar")
        name = os.path.basename(path).lower()
        label = "7-Zip %s" % version if version else "7-Zip"
        if "nana" in name:
            label = "NanaZip"
        return ToolInfo("7z", path, version, formats, formats, rar5=has_rar and "rar5" in low, label=label)
    if kind == "unrar":
        code, out = _run([path], timeout=8)
        if "RAR" not in out:
            return None
        m = re.search(r"RAR\s+([\d.]+)", out)
        version = m.group(1) if m else ""
        major = int(version.split(".")[0]) if version[:1].isdigit() else 0
        return ToolInfo("unrar", path, version, {"rar"}, {"rar"}, rar5=major >= 5, label="UnRAR %s" % version)
    if kind == "unar":
        code, out = _run([path, "-v"], timeout=8)
        if code is None or not (re.search(r"^\s*v?\d+\.\d+", out) or "unar" in out.lower()):
            return None
        lsar = os.path.join(os.path.dirname(path), "lsar.exe" if os.name == "nt" else "lsar")
        if not os.path.isfile(lsar):
            lsar = shutil.which("lsar") or ""
        if not lsar:
            return None
        m = re.search(r"v?([\d.]+)", out)
        info = ToolInfo("unar", path, m.group(1) if m else "", {"zip", "7z", "rar"}, {"zip", "7z", "rar"},
                        rar5=True, label="The Unarchiver %s" % (m.group(1) if m else ""))
        info.lsar = lsar
        return info
    if kind == "bsdtar":
        code, out = _run([path, "--version"], timeout=8)
        if code is None or "bsdtar" not in out:
            return None
        m = re.search(r"libarchive\s+([\d.]+)", out)
        version = m.group(1) if m else ""
        parts = [int(x) for x in version.split(".")[:2] if x.isdigit()] or [0, 0]
        rar5 = tuple(parts) >= (3, 4)
        return ToolInfo("bsdtar", path, version, {"zip", "7z", "rar"}, {"zip"}, rar5=rar5,
                        label="bsdtar (libarchive %s)" % version)
    return None


def detect(custom=None, refresh=False):
    #-> {kind: ToolInfo} for the first working tool of each kind. custom = {kind: path} from prefs
    key = tuple(sorted((custom or {}).items()))
    with _LOCK:
        if not refresh and key in _CACHE:
            return _CACHE[key]
    found = {}
    cands = _candidates()
    for kind in ("7z", "unrar", "unar", "bsdtar"):
        paths = []
        if custom and custom.get(kind):
            paths.append(custom[kind])
        paths += cands.get(kind, [])
        for p in paths:
            if not p or not os.path.isfile(p):
                continue
            info = probe(kind, p)
            if info is not None:
                found[kind] = info
                break
    with _LOCK:
        _CACHE[key] = found
    return found


def pick(found, fmt, need_password=False, multivolume=False):
    #best tool for a format. 7-zip first, then the format specialists
    # bsdtar cant follow multi volume rar sets from the cli so its out for those
    order = {"rar": ("7z", "unrar", "unar") if multivolume else ("7z", "unrar", "unar", "bsdtar"),
             "7z": ("7z", "unar", "bsdtar"),
             "zip": ("7z", "unar", "bsdtar")}.get(fmt, ("7z",))
    for kind in order:
        t = found.get(kind)
        if t is None or fmt not in t.formats:
            continue
        if need_password and fmt not in t.encryption:
            continue
        return t
    return None


def install_hint(fmt):
    #what to tell the user when nothing can open fmt
    if sys.platform == "darwin":
        return ("Install 7-Zip with Homebrew ('brew install sevenzip') or The Unarchiver CLI ('brew install unar'). "
                "macOS's built-in tar can read unencrypted RAR files.")
    if os.name == "nt":
        return ("Install 7-Zip (https://www.7-zip.org) or WinRAR. On Windows 11 the built-in tar.exe can read "
                "unencrypted RAR files.")
    return ("Install 7-Zip ('7zip' or 'p7zip-full' + 'p7zip-rar' package), 'unrar', 'unar' or 'libarchive-tools' "
            "(bsdtar) with your package manager.")
