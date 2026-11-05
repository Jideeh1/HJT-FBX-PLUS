# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#static api check vs older blenders we cant run here, using fake-bpy-module stubs
#   python tools/api_compat_check.py <stubs old> <stubs new> [<repo>]
# reports: bpy.types classes, bpy.ops operators n node type names we use that dont exist in the old
# stubs, plus attribute names that only exist in the new stubs (each one must be guarded in code)

import ast
import os
import re
import sys


def stub_names(root):
    classes, members, ops = set(), set(), set()
    pkg = os.path.join(root, "bpy-stubs") if os.path.isdir(os.path.join(root, "bpy-stubs")) else os.path.join(root, "bpy")
    tdir = os.path.join(pkg, "types")
    for fn in os.listdir(tdir):
        if not fn.endswith(".pyi"):
            continue
        src = open(os.path.join(tdir, fn), encoding="utf-8").read()
        classes.update(re.findall(r"^class (\w+)", src, re.M))
        members.update(re.findall(r"^    (\w+)\s*[:(=]", src, re.M))
        members.update(re.findall(r"^    def (\w+)", src, re.M))
    odir = os.path.join(pkg, "ops")
    for fn in os.listdir(odir):
        if fn.endswith(".pyi") or os.path.isdir(os.path.join(odir, fn)):
            mod = fn[:-4] if fn.endswith(".pyi") else fn
            path = os.path.join(odir, fn) if fn.endswith(".pyi") else os.path.join(odir, fn, "__init__.pyi")
            if os.path.exists(path):
                #newer stubs write operators as functions, the 3.x ones as classes. took me way too long
                for name in re.findall(r"^(?:def|class) (\w+)", open(path, encoding="utf-8").read(), re.M):
                    ops.add("%s.%s" % (mod, name))
    return classes, members, ops


def code_usage(repo):
    attrs, types, ops, nodes = set(), set(), set(), set()
    for sub in ("blender", "ui", "registration.py", "state.py"):
        p = os.path.join(repo, "hjt_fbx_plus", sub)
        files = [p] if p.endswith(".py") else [os.path.join(r, f) for r, _d, fs in os.walk(p) for f in fs if f.endswith(".py")]
        for f in files:
            src = open(f, encoding="utf-8").read()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    attrs.add(node.attr)
            types.update(re.findall(r"bpy\.types\.(\w+)", src))
            ops.update(re.findall(r"bpy\.ops\.(\w+)\.(\w+)\(", src))
            nodes.update(re.findall(r"[\"'](ShaderNode\w+|NodeFrame|NodeGroup\w+)[\"']", src))
    return attrs, types, {"%s.%s" % o for o in ops}, nodes


def main():
    old, new = sys.argv[1], sys.argv[2]
    repo = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), "..")
    oc, om, oo = stub_names(old)
    nc, nm, no = stub_names(new)
    attrs, types, ops, nodes = code_usage(repo)
    print("old stubs: %d classes, new stubs: %d classes" % (len(oc), len(nc)))
    print("bpy.types used but missing in old:", sorted(t for t in types if t not in oc))
    print("node types used but missing in old:", sorted(n for n in nodes if n not in oc))
    print("operators used but missing in old:", sorted(o for o in ops if o not in oo and not o.startswith("hjt_fbx_plus.")))
    newonly = sorted(a for a in attrs if a in nm and a not in om)
    print("attribute names only in the new API (must be guarded):", newonly)


main()
