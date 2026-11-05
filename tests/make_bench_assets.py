# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#big stuff for performance tests (generated, nothing copyrighted)
#   blender -b --python tests/make_bench_assets.py -- <out dir>
#  heavy/Heavy.fbx   ~400k vert skinned mesh, 200 bones, 2 clips + a 1M vert static mesh, 8 materials
#  many/            40 small models + 3000 small textures + 12 x 2k textures (for the big archive)

import math
import os
import shutil
import sys

import bpy
import numpy as np

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1])


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def png(path, w, h, seed, noise=True):
    rs = np.random.RandomState(seed)
    if noise:
        px = rs.rand(h, w, 4).astype(np.float32)
    else:
        ys, xs = np.mgrid[0:h, 0:w]
        px = np.stack([xs / w, ys / h, np.full_like(xs, seed % 7 / 7.0, dtype=np.float64), np.ones_like(xs, dtype=np.float64)], -1).astype(np.float32)
    px[..., 3] = 1.0
    img = bpy.data.images.new(os.path.basename(path), w, h)
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def heavy():
    reset()
    d = os.path.join(OUT, "heavy")
    os.makedirs(os.path.join(d, "Textures"), exist_ok=True)
    #200 bone rig: spine chain + 4 limbs w fingers + a bunch of hair/cloth chains
    arm = bpy.data.armatures.new("BigRig")
    rig = bpy.data.objects.new("BigRig", arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    root = arm.edit_bones.new("root")
    root.head, root.tail = (0, 0, 0), (0, 0, 0.1)
    heads = []
    prev = root
    for i in range(10):
        b = arm.edit_bones.new("spine_%02d" % i)
        b.head, b.tail = (0, 0, 0.1 + i * 0.18), (0, 0, 0.28 + i * 0.18)
        b.parent = prev
        prev = b
        heads.append(b)
    n = len(arm.edit_bones)
    chain = 0
    while n < 200:
        base = heads[chain % len(heads)]
        ang = chain * 0.7
        p = base
        for j in range(9):
            b = arm.edit_bones.new("chain_%02d_%02d" % (chain, j))
            r0 = 0.2 + 0.12 * j
            b.head = (math.cos(ang) * r0, math.sin(ang) * r0, base.head.z)
            b.tail = (math.cos(ang) * (r0 + 0.12), math.sin(ang) * (r0 + 0.12), base.head.z - 0.02)
            b.parent = p
            p = b
            n += 1
            if n >= 200:
                break
        chain += 1
    bpy.ops.object.mode_set(mode="OBJECT")
    bones = [(b.name, np.array(b.head_local), np.array(b.tail_local)) for b in arm.bones]
    #skinned mesh ~400k verts
    bpy.ops.mesh.primitive_uv_sphere_add(segments=640, ring_count=640, radius=1.0, location=(0, 0, 1.0))
    body = bpy.context.active_object
    body.name = "SkinnedBody"
    body.scale = (1.4, 1.4, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    co = np.empty(len(body.data.vertices) * 3)
    body.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mids = np.array([(h + t) / 2 for _n, h, t in bones])
    nearest = np.empty(len(co), dtype=np.int64)
    for s in range(0, len(co), 50000):
        dd = ((co[s:s + 50000, None, :] - mids[None, :, :]) ** 2).sum(-1)
        nearest[s:s + 50000] = dd.argmin(1)
    for bi, (name, _h, _t) in enumerate(bones):
        idx = np.nonzero(nearest == bi)[0]
        if len(idx):
            vg = body.vertex_groups.new(name=name)
            vg.add(idx.tolist(), 1.0, "REPLACE")
    body.parent = rig
    body.modifiers.new("Armature", "ARMATURE").object = rig
    #static high poly mesh 1M verts
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=1000, y_subdivisions=1000, size=4, location=(3, 0, 0))
    grid = bpy.context.active_object
    grid.name = "StaticTerrain"
    for ob in (body, grid):
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.0)
        bpy.ops.object.mode_set(mode="OBJECT")
    for i in range(8):
        tex = os.path.join(d, "Textures", "Heavy_M%d_D.png" % i)
        png(tex, 1024, 1024, i, noise=False)
        m = bpy.data.materials.new("Heavy_M%d" % i)
        if m.node_tree is None:
            m.use_nodes = True
        t = m.node_tree.nodes.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(tex)
        m.node_tree.links.new(t.outputs["Color"], m.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
        (body if i < 4 else grid).data.materials.append(m)
    for ob in (body, grid):
        mi = np.arange(len(ob.data.polygons)) % 4
        ob.data.polygons.foreach_set("material_index", mi.astype(np.int32))
    rig.animation_data_create()
    for aname in ("Run", "Jump"):
        act = bpy.data.actions.new(aname)
        rig.animation_data.action = act
        for bname in [b[0] for b in bones[:60]]:
            pb = rig.pose.bones[bname]
            for f in range(1, 31, 3):
                pb.rotation_quaternion = (1, 0.05 * math.sin(f), 0, 0)
                pb.keyframe_insert("rotation_quaternion", frame=f)
        act.use_fake_user = True
    rig.animation_data.action = None
    bpy.context.scene.frame_end = 30
    bpy.ops.export_scene.fbx(filepath=os.path.join(d, "Heavy.fbx"), path_mode="RELATIVE", add_leaf_bones=False,
                             bake_anim=True, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
                             use_tspace=False)
    print("heavy", os.path.getsize(os.path.join(d, "Heavy.fbx")))


def many():
    reset()
    d = os.path.join(OUT, "many")
    for i in range(40):
        reset()
        sub = os.path.join(d, "Models", "Prop_%02d" % i)
        os.makedirs(sub, exist_ok=True)
        bpy.ops.mesh.primitive_monkey_add()
        ob = bpy.context.active_object
        ob.name = "Prop_%02d" % i
        m = bpy.data.materials.new("Prop_%02d" % i)
        ob.data.materials.append(m)
        bpy.ops.export_scene.fbx(filepath=os.path.join(sub, "Prop_%02d.fbx" % i), bake_anim=False)
    tex = os.path.join(d, "Textures")
    os.makedirs(tex, exist_ok=True)
    reset()
    for i in range(40):
        for suffix in ("D", "N", "ORM"):
            png(os.path.join(tex, "Prop_%02d_%s.png" % (i, suffix)), 64, 64, i, noise=False)
    for i in range(2880):
        png(os.path.join(tex, "Misc", "filler_%04d.png" % i), 16, 16, i, noise=False) if i == 0 else None
    #filler: copy one small file many times (fast), still 2880 distinct entries
    first = os.path.join(tex, "Misc", "filler_0000.png")
    os.makedirs(os.path.dirname(first), exist_ok=True)
    for i in range(1, 2880):
        shutil.copyfile(first, os.path.join(tex, "Misc", "filler_%04d.png" % i))
    for i in range(12):
        png(os.path.join(tex, "Large", "Large_%02d.png" % i), 2048, 2048, 100 + i, noise=True)
    print("many done")


os.makedirs(OUT, exist_ok=True)
heavy()
many()
sys.stdout.flush()
os._exit(0)
