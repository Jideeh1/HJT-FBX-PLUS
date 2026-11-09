# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#parsed .uemodel parts -> blender. several parts (body, face, hair, hat...) share ONE armature, the
# skeletons get merged by bone name since every part ships its own copy of the same rig
#bones keep the raw ue axes here, chains.align_chains fixes the directions afterwards like for fbx

from __future__ import annotations

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from .mapping import Mapping


class BuildResult:
    def __init__(self):
        self.armature = None
        self.meshes = []
        self.materials = {}
        self.warnings = []


def _bone_world(model):
    out = []
    for b in model.bones:
        x, y, z, w = b.rot
        local = Matrix.LocRotScale(Vector(b.pos), Quaternion((w, x, y, z)).normalized(), Vector(b.scale))
        out.append(out[b.parent] @ local if 0 <= b.parent < len(out) else local)
    return out


def _merge_skeletons(parts, res):
    #-> [(name, parent name, world matrix)] parents first. first part to define a bone wins
    order = []
    seen = {}
    for plabel, m in parts:
        worlds = _bone_world(m)
        for i, b in enumerate(m.bones):
            parent = m.bones[b.parent].name if 0 <= b.parent < len(m.bones) else None
            if b.name in seen:
                prev = order[seen[b.name]][2]
                if (prev.translation - worlds[i].translation).length > 1e-3:
                    res.warnings.append("Bone '%s' sits in a different spot in %s; kept the first one." % (b.name, plabel))
                continue
            seen[b.name] = len(order)
            order.append((b.name, parent, worlds[i]))
    return order


def _build_armature(name, bones, coll, context):
    data = bpy.data.armatures.new(name)
    data.display_type = "OCTAHEDRAL"
    arm = bpy.data.objects.new(name, data)
    arm.show_in_front = True
    coll.objects.link(arm)
    if not bones:
        return arm
    vl = context.view_layer
    prev = vl.objects.active
    vl.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ebs = data.edit_bones
        heads = {n: w.translation for n, _p, w in bones}
        kids = {}
        for n, p, _w in bones:
            if p is not None:
                kids.setdefault(p, []).append(n)
        for n, p, w in bones:
            eb = ebs.new(n)
            gaps = [(heads[c] - heads[n]).length for c in kids.get(n, ())]
            gaps = [g for g in gaps if g > 1e-4]
            eb.head = (0.0, 0.0, 0.0)
            eb.tail = (0.0, max(0.005, min(gaps) if gaps else 0.02), 0.0)
            loc, rot, _s = w.decompose()
            eb.matrix = Matrix.LocRotScale(loc, rot, None)
            if p is not None and p in ebs:
                eb.parent = ebs[p]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        vl.objects.active = prev
    return arm


def _material(name, cache):
    mat = cache.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        cache[name] = mat
    return mat


def _build_mesh(label, model, coll, mats, res):
    lod = model.lods[0]
    nv, nf = len(lod.verts), len(lod.indices)
    me = bpy.data.meshes.new(label)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", lod.verts.astype(np.float32).ravel())
    loops = lod.indices.astype(np.int32).ravel()
    me.loops.add(len(loops))
    me.loops.foreach_set("vertex_index", loops)
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, len(loops), 3, dtype=np.int32))
    if bpy.app.version < (4, 0, 0):
        me.polygons.foreach_set("loop_total", np.full(nf, 3, dtype=np.int32))
    me.update(calc_edges=True)
    me.polygons.foreach_set("use_smooth", np.ones(nf, dtype=bool))

    for i, (uvname, uv) in enumerate(lod.uvs):
        layer = me.uv_layers.new(name=uvname or "UV%d" % i)
        if layer is None:
            res.warnings.append("%s: Blender's UV map limit reached, dropped %s." % (label, uvname))
            break
        layer.data.foreach_set("uv", uv[loops].astype(np.float32).ravel())
    #game vertex colors r data (outline width, ao, masks...), so raw floats, no srgb conversion
    for cname, col in lod.colors:
        attr = me.color_attributes.new(name=cname or "Color", type="FLOAT_COLOR", domain="POINT")
        attr.data.foreach_set("color", (col.astype(np.float32) / 255.0).ravel())

    slot_of = {}
    for name, _path, _first, _n in lod.materials:
        if name not in slot_of:
            slot_of[name] = len(me.materials)
            me.materials.append(_material(name, mats))
    if lod.materials:
        idx = np.zeros(nf, dtype=np.int32)
        for name, _path, first, n in lod.materials:
            s = first // 3
            idx[s:s + n] = slot_of[name]
        me.polygons.foreach_set("material_index", idx)

    me.validate(clean_customdata=False)
    if lod.normals is not None and len(lod.normals) == nv:
        n = lod.normals.astype(np.float64)
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        if hasattr(me, "use_auto_smooth"):
            me.use_auto_smooth = True
        me.normals_split_custom_set_from_vertices(n.tolist())

    ob = bpy.data.objects.new(label, me)
    coll.objects.link(ob)

    if lod.weights is not None and len(lod.weights) and model.bones:
        names = [b.name for b in model.bones]
        w = lod.weights
        bone = w["bone"].astype(np.int64)
        ok = (bone >= 0) & (bone < len(names)) & (w["vert"] >= 0) & (w["vert"] < nv)
        bone, vert, wt = bone[ok], w["vert"][ok].astype(np.int64), w["w"][ok]
        #one add() per (bone, weight value) instead of per vertex. weights r 8/16 bit in ue anyway
        # so theres only a handful of distinct values, 100x faster on a 250k weight body
        key = np.round(wt, 5)
        order = np.lexsort((key, bone))
        bone, vert, key = bone[order], vert[order], key[order]
        cuts = np.flatnonzero((np.diff(bone) != 0) | (np.diff(key) != 0)) + 1
        starts = np.concatenate(([0], cuts))
        ends = np.concatenate((cuts, [len(bone)]))
        groups = {}
        for s, e in zip(starts, ends):
            bname = names[bone[s]]
            vg = groups.get(bname)
            if vg is None:
                vg = groups[bname] = ob.vertex_groups.new(name=bname)
            vg.add(vert[s:e].tolist(), float(key[s]), "ADD")

    if lod.morphs:
        ob.shape_key_add(name="Basis", from_mix=False)
        base = lod.verts.astype(np.float32)
        for mname, vidx, delta in lod.morphs:
            ok = (vidx >= 0) & (vidx < nv)
            co = base.copy()
            np.add.at(co, vidx[ok], delta[ok])
            kb = ob.shape_key_add(name=mname, from_mix=False)
            kb.data.foreach_set("co", co.ravel())
            kb.interpolation = "KEY_LINEAR"
            kb.value = 0.0
    return ob


def build(context, parts, name, coll):
    #parts = [(label, UEModel)] -> (BuildResult, Mapping)
    res = BuildResult()
    mp = Mapping()
    skeletal = [(lb, m) for lb, m in parts if m.bones]
    bones = _merge_skeletons(skeletal, res)
    arm = None
    if bones:
        arm = _build_armature(name + "_Skeleton", bones, coll, context)
        res.armature = arm
        mp.objects["ue:armature"] = arm
        for n, _p, _w in bones:
            mp.bones["ue:bone:" + n] = (arm, n)
    for label, m in parts:
        if not m.lods or not len(m.lods[0].verts):
            res.warnings.append("%s has no geometry, skipped." % label)
            continue
        ob = _build_mesh(label, m, coll, res.materials, res)
        res.meshes.append(ob)
        mp.objects["ue:mesh:" + label] = ob
        mp.meshes["ue:mesh:" + label] = ob.data
        if arm is not None and m.bones:
            ob.parent = arm
            mod = ob.modifiers.new("Armature", "ARMATURE")
            mod.object = arm
            mod.use_vertex_groups = True
    for n, mat in res.materials.items():
        mp.materials["ue:" + n] = mat
    mp.exact = True
    return res, mp
