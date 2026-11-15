# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#.obj scouting, no bpy (worker thread). blenders own c++ importer does the heavy lifting later,
# we only need names, the up axis n what the .mtl links so textures can be planned like for fbx

from __future__ import annotations

import os
import re

EXT = ".obj"
_LOD = re.compile(r"(?i)[_\-. ]?lod\d+$")

#mtl key -> (slot hint, our label)
_MAPS = {
    "map_kd": "base_color", "map_ka": "ao", "map_ks": "specular", "map_ns": "roughness", "map_d": "opacity",
    "map_bump": "normal", "bump": "normal", "norm": "normal", "map_ke": "emission", "map_pr": "roughness",
    "map_pm": "metallic", "disp": "height",
}


def is_obj(path):
    return (path or "").lower().endswith(EXT)


def model_name(path):
    #NPC_Dog_Size01_Body_001_LOD0.obj -> NPC_Dog_Size01_Body_001
    return _LOD.sub("", os.path.splitext(os.path.basename(path))[0]) or "OBJ"


def _map_path(rest):
    #"-bm 0.5 -o 0 0 tex/a b.png" -> "tex/a b.png". options eat a fixed number of args
    toks = rest.split()
    argc = {"-bm": 1, "-blendu": 1, "-blendv": 1, "-boost": 1, "-mm": 2, "-o": 3, "-s": 3, "-t": 3,
            "-texres": 1, "-clamp": 1, "-imfchan": 1, "-type": 1, "-cc": 1}
    i = 0
    while i < len(toks) and toks[i].startswith("-") and toks[i].lower() in argc:
        i += 1 + argc[toks[i].lower()]
    return " ".join(toks[i:]).strip().strip('"')


def read_mtl(data):
    #bytes -> {material: [{"slot_hint", "relative", "filename", "property"}]}
    out = {}
    cur = None
    for raw in data.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        k = key.lower()
        if k == "newmtl":
            cur = rest.strip()
            out.setdefault(cur, [])
        elif cur is not None and k in _MAPS:
            path = _map_path(rest).replace("\\", "/")
            if path:
                out[cur].append({"slot_hint": _MAPS[k], "relative": path, "filename": path.rsplit("/", 1)[-1],
                                 "property": key})
    return out


def scan(path, sample=2000):
    #-> dict: objects, materials (usemtl order), mtllibs, verts, faces, bbox, up ("Y"/"Z")
    objects, materials, mtllibs = [], [], []
    seen_o, seen_m = set(), set()
    verts = faces = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    with open(path, "rb") as fh:
        for raw in fh:
            c0 = raw[:2]
            if c0 == b"v ":
                verts += 1
                p = raw.split()
                try:
                    xyz = (float(p[1]), float(p[2]), float(p[3]))
                except (IndexError, ValueError):
                    continue
                for i in range(3):
                    if xyz[i] < lo[i]:
                        lo[i] = xyz[i]
                    if xyz[i] > hi[i]:
                        hi[i] = xyz[i]
            elif c0 == b"f ":
                faces += 1
            elif c0 in (b"o ", b"g "):
                name = raw[2:].strip().decode("utf-8", "replace")
                if name and name not in seen_o:
                    seen_o.add(name)
                    objects.append(name)
            elif raw.startswith(b"usemtl "):
                name = raw[7:].strip().decode("utf-8", "replace")
                if name and name not in seen_m:
                    seen_m.add(name)
                    materials.append(name)
            elif raw.startswith(b"mtllib "):
                mtllibs.append(raw[7:].strip().decode("utf-8", "replace"))
    up = guess_up(lo, hi) if verts else "Y"
    return {"objects": objects, "materials": materials, "mtllibs": mtllibs, "verts": verts, "faces": faces,
            "bbox": (lo, hi) if verts else None, "up": up}


def guess_up(lo, hi):
    #game rips stand on the floor: the axis whose lowest point sits on 0 is up. obj says Y up,
    # but exporters (incl the zzz ones) happily write Z up, n then the dog lies on its side lol
    def grounded(i):
        ext = max(hi[i] - lo[i], 1e-9)
        return abs(lo[i]) <= 0.02 * ext and hi[i] > 0
    y, z = grounded(1), grounded(2)
    if z and not y:
        return "Z"
    return "Y"


def summarize(path, info, mtl):
    #same shape as fbx.inspect_document so profiles, planning n reports dont care
    name = model_name(path)
    mats = []
    if info["materials"]:
        for m in info["materials"]:
            mats.append({"name": m, "uid": "obj:" + m, "users": [name], "props": {}, "textures": list(mtl.get(m, []))})
    else:
        mats.append({"name": name, "uid": "obj:" + name, "users": [name], "props": {}, "textures": []})
    tex = sorted({t["filename"].rsplit(".", 1)[0] for lst in mtl.values() for t in lst})
    issues = []
    if not info["materials"]:
        issues.append({"level": "info", "code": "obj_no_materials",
                       "message": "The OBJ has no materials; textures are matched by file name."})
    if info["up"] == "Z":
        issues.append({"level": "info", "code": "obj_z_up", "message": "The OBJ is Z-up; imported without axis conversion."})
    return {
        "format": "obj", "version": 0,
        "header": {"creator": "OBJ"},
        "global_settings": {"UnitScaleFactor": 1.0},
        "materials": mats,
        "material_names": [m["name"] for m in mats],
        "object_names": [name] + info["objects"],
        "bone_names": [],
        "texture_files": tex,
        "issues": issues,
        "counts": {"meshes": max(1, len(info["objects"])), "materials": len(mats), "bones": 0,
                   "vertices": info["verts"], "triangles": info["faces"], "blend_shapes": 0, "anim_curves": 0},
    }
