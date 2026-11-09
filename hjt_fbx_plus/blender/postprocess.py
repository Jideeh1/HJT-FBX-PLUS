# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#stuff that happens after blenders importer is done
#  tangents          fbx tangents/binormals -> corner attributes (blender doesnt store tangents itself)
#  categorize        lod / collision / custom categories -> hide/wire/collections, never delete
#  skin check        every skinned mesh really bound to its armature? if not, fix it
#  fit bones         bone lengths that reach the next joint (cosmetic, deformation unchanged)
#  inbetween drivers rebuild fbx in-between shape blending w drivers

from __future__ import annotations

import re

import bpy

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

from ..fbx.elements import FbxArray
from ..fbx.prepare import uid_key


#tangents

def _layer_values(geom_node, layer_name, data_name, index_name, w_name, n_loops, pvi_verts, poly_of_loop):
    el = geom_node.find(layer_name)
    if el is None:
        return None, None
    dnode = el.find(data_name)
    if dnode is None or not dnode.props or not isinstance(dnode.props[0], FbxArray):
        return None, None
    vals = dnode.props[0].numpy().astype(np.float32)
    mapping = el.value(b"MappingInformationType", 0, b"ByPolygonVertex")
    ref = el.value(b"ReferenceInformationType", 0, b"Direct")
    comps = 3
    w = None
    if mapping == b"ByPolygonVertex" and ref != b"IndexToDirect" and len(vals) == n_loops * 4:
        comps = 4
    vals = vals.reshape(-1, comps)
    if comps == 4:
        w = vals[:, 3].copy()
        vals = vals[:, :3]
    wnode = el.find(w_name)
    if w is None and wnode is not None and wnode.props and isinstance(wnode.props[0], FbxArray):
        w = wnode.props[0].numpy().astype(np.float32)
    if ref == b"IndexToDirect":
        inode = el.find(index_name)
        if inode is None or not inode.props:
            return None, None
        idx = inode.props[0].numpy()
        vals = vals[idx]
        if w is not None and len(w) >= idx.max() + 1:
            w = w[idx]
    if mapping == b"ByPolygonVertex":
        per_loop = vals
    elif mapping in (b"ByControlPoint", b"ByVertice", b"ByVertex"):
        per_loop = vals[pvi_verts]
        w = w[pvi_verts] if w is not None and len(w) > pvi_verts.max() else None
    elif mapping == b"ByPolygon":
        per_loop = vals[poly_of_loop]
        w = w[poly_of_loop] if w is not None else None
    elif mapping == b"AllSame":
        per_loop = np.repeat(vals[:1], n_loops, axis=0)
        w = np.repeat(w[:1], n_loops) if w is not None else None
    else:
        return None, None
    if len(per_loop) != n_loops:
        return None, None
    if w is not None and len(w) != n_loops:
        w = None
    return per_loop, w


def store_tangents(doc, mapping, diag, model=""):
    #-> number of meshes that got fbx_tangent / fbx_binormal attributes
    if np is None:
        return 0
    done = 0
    for geom in doc.objects_of(b"Geometry", b"Mesh"):
        node = geom.node
        if node.find(b"LayerElementTangent") is None and node.find(b"LayerElementBinormal") is None:
            continue
        mesh = mapping.meshes.get(uid_key(geom.uid))
        if mesh is None:
            continue
        pvi_node = node.find(b"PolygonVertexIndex")
        if pvi_node is None or not pvi_node.props:
            continue
        pvi = pvi_node.props[0].numpy()
        verts = np.where(pvi < 0, ~pvi, pvi)
        n_loops = len(pvi)
        if len(mesh.loops) != n_loops:
            diag.info("Tangents of '%s' not stored: Blender changed the face layout (%d loops vs %d in the FBX)." % (
                mesh.name, len(mesh.loops), n_loops), model)
            continue
        loop_verts = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_verts)
        if not np.array_equal(loop_verts, verts.astype(np.int32)):
            diag.info("Tangents of '%s' not stored: loop order differs from the FBX." % mesh.name, model)
            continue
        ends = (pvi < 0).astype(np.int32)
        poly_of_loop = np.concatenate(([0], np.cumsum(ends)[:-1]))
        wrote = False
        for layer, data, index, wname, attr in ((b"LayerElementTangent", b"Tangents", b"TangentsIndex", b"TangentsW", "fbx_tangent"),
                                                (b"LayerElementBinormal", b"Binormals", b"BinormalsIndex", b"BinormalsW", "fbx_binormal")):
            vals, w = _layer_values(node, layer, data, index, wname, n_loops, verts, poly_of_loop)
            if vals is None:
                continue
            a = mesh.attributes.get(attr) or mesh.attributes.new(attr, "FLOAT_VECTOR", "CORNER")
            a.data.foreach_set("vector", np.ascontiguousarray(vals, dtype=np.float32).ravel())
            if w is not None and attr == "fbx_tangent":
                aw = mesh.attributes.get(attr + "_w") or mesh.attributes.new(attr + "_w", "FLOAT", "CORNER")
                aw.data.foreach_set("value", np.ascontiguousarray(w, dtype=np.float32))
            wrote = True
        if wrote:
            done += 1
    return done


#categories

def categorize(doc, mapping, profile, opts, diag, model, sub_collection):
    #-> {category label: [objects]}
    cats = profile.categories
    lod_on = (cats.get("lod") or {}).get("enabled", True) and opts.get("handle_lods", True)
    col_on = (cats.get("collision") or {}).get("enabled", True) and opts.get("handle_collision", True)
    found = {}
    lod_levels = {}
    names = {uid_key(u): o.name for u, o in doc.objects.items() if o.cls == b"Model"} if doc is not None else {}
    for uid, ob in mapping.objects.items():
        fbx_name = names.get(uid) or re.sub(r"\.\d{3,}$", "", ob.name)
        if col_on and ob.type == "MESH" and any(rx.search(fbx_name) for rx in profile.collision_patterns):
            found.setdefault("Collision", []).append(ob)
            continue
        if lod_on and ob.type == "MESH":
            for rx in profile.lod_patterns:
                m = rx.search(fbx_name)
                if m:
                    try:
                        lvl = int(m.group(1))
                    except (IndexError, TypeError, ValueError):
                        lvl = 0
                    lod_levels[ob] = lvl
                    break
        for cname, rx, action, target in profile.custom_categories:
            if target == "material":
                hit = any(s.material is not None and rx.search(s.material.name) for s in ob.material_slots)
            else:
                hit = bool(rx.search(fbx_name))
            if hit:
                found.setdefault(cname, []).append(ob)
                ob["hjt_category"] = cname
                _apply_action(ob, action)
    #lod groups from fbx LodGroup nodes, children in connection order = lod0, lod1 ...
    for grp in (doc.objects_of(b"Model", b"LodGroup") if doc is not None else ()):
        kids = [c for c in doc.children(grp.uid, b"Model")]
        for i, child in enumerate(kids):
            ob = mapping.objects.get(uid_key(child.uid))
            if ob is not None and ob not in lod_levels:
                lod_levels[ob] = i
    levels = sorted(set(lod_levels.values()))
    if len(levels) > 1:
        action = (cats.get("lod") or {}).get("action", "HIDE_LOWER")
        best = levels[0]
        for ob, lvl in lod_levels.items():
            ob["hjt_category"] = "LOD%d" % lvl
            found.setdefault("LOD%d" % lvl, []).append(ob)
            if lvl != best and action == "HIDE_LOWER":
                _hide(ob)
        if action == "COLLECTIONS":
            for lvl in levels:
                coll = sub_collection("LOD%d" % lvl)
                for ob, l2 in lod_levels.items():
                    if l2 == lvl:
                        _move_to(ob, coll)
                if lvl != best:
                    _exclude(coll)
        diag.info("LODs: %s (%s)." % (", ".join("LOD%d" % l for l in levels),
                                      "lower levels hidden" if action == "HIDE_LOWER" else action.lower()), model)
    if found.get("Collision"):
        action = (cats.get("collision") or {}).get("action", "WIRE_HIDE")
        for ob in found["Collision"]:
            ob["hjt_category"] = "Collision"
            if action == "WIRE_HIDE":
                ob.display_type = "WIRE"
                ob.hide_render = True
            elif action == "HIDE":
                _hide(ob)
        if opts.get("group_by_category", True):
            coll = sub_collection("Collision")
            for ob in found["Collision"]:
                _move_to(ob, coll)
        diag.info("%d collision meshes (%s)." % (len(found["Collision"]), action.lower().replace("_", " + ")), model)
    return found


def _hide(ob):
    ob.hide_render = True
    try:
        ob.hide_set(True)
    except RuntimeError:
        ob.hide_viewport = True


def _apply_action(ob, action):
    if action == "HIDE":
        _hide(ob)
    elif action == "WIRE":
        ob.display_type = "WIRE"
        ob.hide_render = True


def _move_to(ob, coll):
    if coll is None or coll in ob.users_collection:
        return
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)


def _exclude(coll):
    vl = bpy.context.view_layer
    from .compat import find_layer_collection
    lc = find_layer_collection(vl.layer_collection, coll)
    if lc is not None:
        lc.exclude = True


#skin check

def verify_skinning(doc, mapping, diag, model):
    #-> (ok, repaired). the "armature doesnt move my mesh" safety net
    ok = repaired = 0
    for skin in doc.objects_of(b"Deformer", b"Skin"):
        geoms = doc.parents(skin.uid, b"Geometry")
        arms = {}
        bone_names = set()
        for cl in doc.children(skin.uid, b"Deformer"):
            for bm in doc.children(cl.uid, b"Model"):
                hit = mapping.bones.get(uid_key(bm.uid))
                if hit is not None:
                    arms[hit[0]] = arms.get(hit[0], 0) + 1
                    bone_names.add(hit[1])
        if not arms:
            continue
        arm = max(arms, key=arms.get)
        for g in geoms:
            mesh = mapping.meshes.get(uid_key(g.uid))
            if mesh is None:
                continue
            for ob in [o for o in mapping.objects.values() if o.type == "MESH" and o.data == mesh]:
                mods = [m for m in ob.modifiers if m.type == "ARMATURE"]
                bound = [m for m in mods if m.object is not None]
                vg_hits = sum(1 for vg in ob.vertex_groups if vg.name in bone_names)
                fixed = False
                if not bound:
                    md = mods[0] if mods else ob.modifiers.new("Armature", "ARMATURE")
                    md.object = arm
                    md.use_vertex_groups = True
                    fixed = True
                if ob.parent is None:
                    mw = ob.matrix_world.copy()
                    ob.parent = arm
                    ob.matrix_world = mw
                if vg_hits == 0:
                    n = _rebuild_weights(doc, skin, ob, mapping)
                    if n:
                        fixed = True
                        diag.warning("Rebuilt %d vertex groups of '%s' from the FBX skin data." % (n, ob.name), model)
                if fixed:
                    repaired += 1
                    diag.warning("'%s' was not bound to its armature; bound it to '%s'." % (ob.name, arm.name), model)
                else:
                    ok += 1
    return ok, repaired


def _rebuild_weights(doc, skin, ob, mapping):
    n = 0
    for cl in doc.children(skin.uid, b"Deformer"):
        bones = doc.children(cl.uid, b"Model")
        if not bones:
            continue
        hit = mapping.bones.get(uid_key(bones[0].uid))
        if hit is None:
            continue
        idx_n = cl.node.find(b"Indexes")
        w_n = cl.node.find(b"Weights")
        if idx_n is None or w_n is None or not idx_n.props or not w_n.props:
            continue
        idx = list(idx_n.props[0].values())
        ws = list(w_n.props[0].values())
        vg = ob.vertex_groups.get(hit[1]) or ob.vertex_groups.new(name=hit[1])
        for i, w in zip(idx, ws):
            if 0 <= i < len(ob.data.vertices):
                vg.add([int(i)], float(w), "REPLACE")
        n += 1
    return n


#bone fitting

def fit_bone_lengths(arm, meshes, aligned=0.9998, owned=0.3, stub=0.25):
    #bones get the length that reaches the child theyre pointing at (spine meets spine, arm meets elbow)
    # leaves get sized by how far their own vertices go along them, w a minimum stub.
    # only lengths change: heads, directions, rolls, parenting all untouched -> deformation + anims same
    #same idea as the fit_bones.py helper trident ships w its exports, written from scratch here
    if np is None or arm.type != "ARMATURE":
        return 0
    bones = {b.name: (np.array(b.head_local), np.array((b.tail_local - b.head_local).normalized())) for b in arm.data.bones}
    leaf_names = {b.name for b in arm.data.bones if not b.children and b.parent is not None}
    reach = {}
    to_arm = np.array(arm.matrix_world.inverted())
    for ob in meshes:
        if ob.type != "MESH" or not any(m.type == "ARMATURE" and m.object == arm for m in ob.modifiers):
            continue
        groups = {g.index: g.name for g in ob.vertex_groups if g.name in leaf_names}
        if not groups:
            continue
        me = ob.data
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = to_arm @ np.array(ob.matrix_world)
        co = co @ m[:3, :3].T + m[:3, 3]
        for v in me.vertices:
            for g in v.groups:
                name = groups.get(g.group)
                if name is None or g.weight < owned:
                    continue
                head, axis = bones[name]
                along = float((co[v.index] - head) @ axis)
                if along > reach.get(name, 0.0):
                    reach[name] = along
    vl = bpy.context.view_layer
    prev_active = vl.objects.active
    prev_mode = arm.mode
    vl.objects.active = arm
    hidden = arm.hide_get() if hasattr(arm, "hide_get") else False
    if hidden:
        arm.hide_set(False)
    bpy.ops.object.mode_set(mode="EDIT")
    fitted = 0
    try:
        ebs = arm.data.edit_bones
        for eb in ebs:
            y = eb.tail - eb.head
            if y.length < 1e-9:
                continue
            y.normalize()
            best = None
            for child in eb.children:
                off = child.head - eb.head
                if off.length < 1e-6:
                    continue
                cos = off.normalized().dot(y)
                if cos < aligned:
                    continue
                key = (len(child.children_recursive), cos)
                if best is None or key > best[0]:
                    best = (key, off.length)
            if best is not None:
                eb.length = best[1]
                fitted += 1
        #leaves after the chains so the stub uses the parents FITTED length
        for eb in ebs:
            if eb.children or eb.parent is None:
                continue
            eb.length = max(reach.get(eb.name, 0.0), eb.parent.length * stub, 1e-4)
            fitted += 1
    finally:
        bpy.ops.object.mode_set(mode="OBJECT" if prev_mode == "OBJECT" else prev_mode)
        if hidden:
            arm.hide_set(True)
        vl.objects.active = prev_active
    return fitted


#in-between shapes

def rebuild_inbetweens(inbetweens, mapping, diag, model):
    #fbx in-betweens: channel value v (0..100) blends piecewise linear between shapes at w1<w2<..<100
    # blender has no such thing, so: the main key becomes a pure control slider (relative to itself =
    # zero effect, keeps its animation), a copy of its shape + the in-between keys get chained relative
    #to each other n driven from it w simple expressions (no python auto run needed)
    done = 0
    for info in inbetweens:
        ibs = sorted(info.get("inbetweens", []), key=lambda d: d["weight"])
        if not ibs:
            continue
        for guid in info.get("geometry_uids", []):
            mesh = mapping.meshes.get(guid)
            if mesh is None or mesh.shape_keys is None:
                continue
            kb = mesh.shape_keys.key_blocks
            main = kb.get(info["main_key"])
            keys = [kb.get(d["key"]) for d in ibs]
            if main is None or any(k is None for k in keys):
                continue
            ob = next((o for o in mapping.objects.values() if o.type == "MESH" and o.data == mesh), None)
            if ob is None:
                continue
            target = ob.shape_key_add(name="%s (100%%)" % main.name, from_mix=False)
            co = [0.0] * (len(main.data) * 3)
            main.data.foreach_get("co", co)
            target.data.foreach_set("co", co)
            chain = keys + [target]
            weights = [d["weight"] for d in ibs] + [info.get("main_weight", 100.0) or 100.0]
            basis = mesh.shape_keys.reference_key
            prev = basis
            prev_w = 0.0
            for k, w in zip(chain, weights):
                k.relative_key = prev
                k.slider_min = 0.0
                k.slider_max = 10.0 if k is target else 1.0  #last segment may overshoot 100% like in fbx
                k.value = 0.0
                fc = k.driver_add("value")
                drv = fc.driver
                drv.type = "SCRIPTED"
                var = drv.variables.new()
                var.name = "v"
                var.type = "SINGLE_PROP"
                var.targets[0].id_type = "KEY"
                var.targets[0].id = mesh.shape_keys
                var.targets[0].data_path = 'key_blocks["%s"].value' % main.name.replace('"', '\\"')
                span = max(1e-6, w - prev_w)
                if k is target:
                    drv.expression = "max((v*100-%g)/%g, 0)" % (prev_w, span)
                else:
                    drv.expression = "min(max((v*100-%g)/%g, 0), 1)" % (prev_w, span)
                prev, prev_w = k, w
            main.relative_key = main  #control only, zero deformation by itself
            done += 1
    if done:
        diag.info("Rebuilt in-between blending for %d shape channels (driven from the original key)." % done, model)
    return done
