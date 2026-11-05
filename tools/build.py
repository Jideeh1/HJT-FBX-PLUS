# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#builds the installable zip: dist/hjt_fbx_plus-<version>.zip w the hjt_fbx_plus/ folder inside.
# one zip for everything: legacy addon (3.6-4.1, Install...) n extension (4.2+, Install from Disk),
# blender finds the manifest one folder down just fine
#   python tools/build.py [--out dist]
#zip is reproducible (sorted entries, fixed timestamps) so the same source = the same sha256

import argparse
import ast
import hashlib
import os
import py_compile
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "hjt_fbx_plus")
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_EXT = (".pyc", ".pyo", ".part", ".blend1", ".orig", ".rej", ".log")
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
STAMP = (2026, 1, 1, 0, 0, 0)
MAX_FILE = 2 * 1024 * 1024  #nothing in the addon should be this big, catches stray test assets


def read_manifest():
    txt = open(os.path.join(PKG, "blender_manifest.toml"), encoding="utf-8").read()
    try:
        import tomllib
        return tomllib.loads(txt)
    except ImportError:  #python 3.10, good enough for the flat keys we check
        return dict(re.findall(r'^(\w+)\s*=\s*"([^"]*)"', txt, re.M))


def read_bl_info():
    tree = ast.parse(open(os.path.join(PKG, "__init__.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "bl_info":
            return ast.literal_eval(node.value)
    raise SystemExit("bl_info not found in hjt_fbx_plus/__init__.py")


def package_files():
    for root, dirs, names in os.walk(PKG):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            if n in SKIP_FILES or n.endswith(SKIP_EXT):
                continue
            full = os.path.join(root, n)
            yield full, os.path.relpath(full, ROOT).replace(os.sep, "/")


def check(man, info, files):
    problems = []
    ver = man.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        problems.append("manifest version %r is not X.Y.Z" % ver)
    elif tuple(int(x) for x in ver.split(".")) != tuple(info.get("version", ())):
        problems.append("version mismatch: manifest %s, bl_info %s" % (ver, info.get("version")))
    if man.get("id") != "hjt_fbx_plus":
        problems.append("manifest id must be hjt_fbx_plus (got %r)" % man.get("id"))
    if man.get("name") != info.get("name"):
        problems.append("name mismatch: manifest %r, bl_info %r" % (man.get("name"), info.get("name")))
    names = {arc for _full, arc in files}
    for need in ("hjt_fbx_plus/__init__.py", "hjt_fbx_plus/blender_manifest.toml", "hjt_fbx_plus/LICENSE.txt"):
        if need not in names:
            problems.append("missing %s" % need)
    tmp = tempfile.mkdtemp(prefix="hjt_build_")
    for full, arc in files:
        if os.path.getsize(full) > MAX_FILE:
            problems.append("%s is %.1f MB, too big for the add-on" % (arc, os.path.getsize(full) / 1e6))
        if arc.endswith(".py"):
            try:
                py_compile.compile(full, cfile=os.path.join(tmp, "x.pyc"), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append("%s does not compile: %s" % (arc, exc.msg))
    return problems


def build(out_dir):
    man, info = read_manifest(), read_bl_info()
    files = list(package_files())
    problems = check(man, info, files)
    if problems:
        for p in problems:
            print("error:", p, file=sys.stderr)
        raise SystemExit(1)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "hjt_fbx_plus-%s.zip" % man["version"])
    with zipfile.ZipFile(path + ".part", "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in files:
            zi = zipfile.ZipInfo(arc, STAMP)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 3  #unix, so the permission bits above mean something
            with open(full, "rb") as fh:
                zf.writestr(zi, fh.read(), compresslevel=9)
    os.replace(path + ".part", path)
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(path + ".sha256", "w", encoding="utf-8") as fh:
        fh.write("%s  %s\n" % (digest, os.path.basename(path)))
    print("%s  (%d files, %.0f KB)" % (os.path.relpath(path, ROOT), len(files), os.path.getsize(path) / 1024))
    print("sha256 %s" % digest)
    return path


def main():
    ap = argparse.ArgumentParser(description="Build the installable HJT FBX+ ZIP")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output folder (default: dist/)")
    build(os.path.abspath(ap.parse_args().out))


if __name__ == "__main__":
    main()
