# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#builds the synthetic test assets w blenders own exporter. everything here is generated from scratch
# so it can live in a public repo (no game rips in the test suite, ever)
#   blender -b --factory-startup --python tests/make_test_assets.py -- <repo> <out dir>

import array
import json
import math
import os
import shutil
import sys

import bpy
import bmesh
import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:]
REPO = os.path.abspath(ARGS[0])
OUT = os.path.abspath(ARGS[1])
sys.path.insert(0, REPO)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def save_png(path, w, h, fn, alpha=False):
    #fn(x, y) arrays -> rgba. tiny + fast, we only need recognizable patterns
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    rgba = fn(xs / w, ys / h)
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=alpha)
    img.pixels.foreach_set(np.ascontiguousarray(rgba, dtype=np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return img


def solid(r, g, b, a=1.0, stripe=None):
    def f(x, y):
        out = np.empty(x.shape + (4,), np.float32)
        out[..., 0], out[..., 1], out[..., 2], out[..., 3] = r, g, b, a
        if stripe is not None:
            mask = (np.floor(x * stripe) % 2) == 0
            out[mask, :3] *= 0.6
        return out
    return f


def normal_flat(x, y):
    out = np.empty(x.shape + (4,), np.float32)
    out[..., 0] = 0.5 + 0.1 * np.sin(x * 30)
    out[..., 1] = 0.5
    out[..., 2] = 1.0
    out[..., 3] = 1.0
    return out


def link_tex(mat, img, socket, non_color=False, normal=False):
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    if normal:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        nt.links.new(tex.outputs["Color"], bsdf.inputs[socket])
    return tex


def new_mat(name, textures=()):
    m = bpy.data.materials.new(name)
    if m.node_tree is None:
        m.use_nodes = True
    for img, socket, kw in textures:
        link_tex(m, img, socket, **kw)
    return m


def capsule(name, radius, depth, loc, rot=(0, 0, 0), segs=24):
    bpy.ops.mesh.primitive_cylinder_add(vertices=segs, radius=radius, depth=depth, location=loc, rotation=rot)
    ob = bpy.context.active_object
    ob.name = name
    return ob


BONES = [
    ("Root", (0, 0, 0), (0, 0, 0.1), None),
    ("Hips", (0, 0, 1.0), (0, 0, 1.12), "Root"),
    ("Spine", (0, 0, 1.12), (0, 0, 1.35), "Hips"),
    ("Chest", (0, 0, 1.35), (0, 0, 1.55), "Spine"),
    ("Neck", (0, 0, 1.55), (0, 0, 1.63), "Chest"),
    ("Head", (0, 0, 1.63), (0, 0, 1.88), "Neck"),
]
for side, sx in (("L", 1), ("R", -1)):
    BONES += [
        ("Shoulder.%s" % side, (0.05 * sx, 0, 1.5), (0.18 * sx, 0, 1.5), "Chest"),
        ("UpperArm.%s" % side, (0.18 * sx, 0, 1.5), (0.46 * sx, 0, 1.5), "Shoulder.%s" % side),
        ("LowerArm.%s" % side, (0.46 * sx, 0, 1.5), (0.72 * sx, 0, 1.5), "UpperArm.%s" % side),
        ("Hand.%s" % side, (0.72 * sx, 0, 1.5), (0.82 * sx, 0, 1.5), "LowerArm.%s" % side),
        ("UpperLeg.%s" % side, (0.1 * sx, 0, 1.0), (0.1 * sx, 0, 0.55), "Hips"),
        ("LowerLeg.%s" % side, (0.1 * sx, 0, 0.55), (0.1 * sx, 0, 0.1), "UpperLeg.%s" % side),
        ("Foot.%s" % side, (0.1 * sx, 0, 0.1), (0.1 * sx, -0.12, 0.02), "LowerLeg.%s" % side),
    ]


def make_rig(name="Rig"):
    arm = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, h, t, parent in BONES:
        eb = arm.edit_bones.new(bname)
        eb.head, eb.tail = h, t
        if parent:
            eb.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return ob


def seg_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(1e-9, ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + np.outer(t, ab)), axis=1)


def skin(mesh_ob, rig):
    co = np.array([v.co for v in mesh_ob.data.vertices]) @ np.array(mesh_ob.matrix_world)[:3, :3].T + np.array(mesh_ob.matrix_world)[:3, 3]
    names = [b[0] for b in BONES if b[0] != "Root"]
    d = np.stack([seg_dist(co, np.array(dict((b[0], b) for b in BONES)[n][1], float), np.array(dict((b[0], b) for b in BONES)[n][2], float)) for n in names], axis=1)
    order = np.argsort(d, axis=1)[:, :2]
    for vi in range(len(co)):
        i0, i1 = order[vi]
        d0, d1 = d[vi, i0], d[vi, i1]
        w0 = d1 / max(1e-9, d0 + d1)
        for bi, w in ((i0, w0), (i1, 1 - w0)):
            g = mesh_ob.vertex_groups.get(names[bi]) or mesh_ob.vertex_groups.new(name=names[bi])
            g.add([vi], float(w), "REPLACE")
    mesh_ob.parent = rig
    md = mesh_ob.modifiers.new("Armature", "ARMATURE")
    md.object = rig


def add_uv2_and_colors(ob):
    me = ob.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    uv2 = me.uv_layers.new(name="UV2")
    n = len(me.loops)
    uv2.data.foreach_set("uv", (np.random.RandomState(1).rand(n * 2)).astype(np.float32))
    col = me.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    col.data.foreach_set("color", np.tile(np.array([0.9, 0.5, 0.2, 1.0], np.float32), n))


def export(path, objects=None, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if objects is not None:
        for o in bpy.context.view_layer.objects:
            o.select_set(o in objects)
    args = dict(filepath=path, use_selection=objects is not None, path_mode="RELATIVE", use_tspace=True,
                use_custom_props=True, add_leaf_bones=False, bake_anim=True, bake_anim_use_all_actions=True,
                bake_anim_use_nla_strips=False)
    args.update(kw)
    bpy.ops.export_scene.fbx(**args)


def hero(root, name="Hero", body_color=(0.2, 0.4, 0.9)):
    reset()
    tex = os.path.join(root, "Textures")
    imgs = {
        "body_d": save_png(os.path.join(tex, name + "_Body_D.png"), 128, 128, solid(*body_color, stripe=8)),
        "body_n": save_png(os.path.join(tex, name + "_Body_N.png"), 128, 128, normal_flat),
        "body_orm": save_png(os.path.join(tex, name + "_Body_ORM.png"), 128, 128, solid(1.0, 0.6, 0.0)),
        "hair_d": save_png(os.path.join(tex, name + "_Hair_D.png"), 128, 128, solid(0.9, 0.8, 0.3, 0.5, stripe=4), alpha=True),
        "face_d": save_png(os.path.join(tex, name + "_Face_D.png"), 128, 128, solid(1.0, 0.85, 0.75)),
    }
    rig = make_rig()
    body = capsule(name + "_Body", 0.16, 1.0, (0, 0, 1.05))
    for i, (loc, rot, r, dpt) in enumerate([((0.45, 0, 1.5), (0, math.pi / 2, 0), 0.05, 0.7), ((-0.45, 0, 1.5), (0, math.pi / 2, 0), 0.05, 0.7),
                                           ((0.1, 0, 0.55), (0, 0, 0), 0.07, 0.95), ((-0.1, 0, 0.55), (0, 0, 0), 0.07, 0.95)]):
        capsule("limb%d" % i, r, dpt, loc, rot)
    bpy.ops.object.select_all(action="DESELECT")
    for o in bpy.data.objects:
        if o.type == "MESH" and (o.name.startswith("limb") or o == body):
            o.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=2)
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.mesh.tris_convert_to_quads()
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")
    add_uv2_and_colors(body)
    body.data.materials.append(new_mat(name + "_Body", [(imgs["body_d"], "Base Color", {}),
                                                        (imgs["body_n"], "Normal", {"non_color": True, "normal": True})]))
    body["game_id"] = 1234
    body["rarity"] = "SSR"
    skin(body, rig)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.14, location=(0, 0, 1.76), segments=24, ring_count=16)
    head = bpy.context.active_object
    head.name = name + "_Head"
    bpy.ops.object.transform_apply(location=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")
    head.data.materials.append(new_mat(name + "_Face", [(imgs["face_d"], "Base Color", {})]))
    head.data.materials.append(new_mat(name + "_Hair", [(imgs["hair_d"], "Base Color", {})]))
    for p in head.data.polygons:
        p.material_index = 1 if p.center.z > 1.82 else 0
    skin(head, rig)
    head.shape_key_add(name="Basis")
    blink = head.shape_key_add(name="Blink", from_mix=False)
    smile = head.shape_key_add(name="Smile", from_mix=False)
    for i, v in enumerate(head.data.vertices):
        if v.co.z > 1.78:
            blink.data[i].co.z -= 0.02
        if v.co.y < -0.1:
            smile.data[i].co.x *= 1.05
    rig.animation_data_create()
    for aname, bone, axis, amp in (("Idle", "Spine", 0, 0.1), ("Wave", "UpperArm.R", 1, 0.8)):
        act = bpy.data.actions.new(aname)
        rig.animation_data.action = act
        pb = rig.pose.bones[bone]
        pb.rotation_mode = "XYZ"
        for f, v in ((1, 0.0), (10, amp), (20, 0.0)):
            pb.rotation_euler[axis] = v
            pb.keyframe_insert("rotation_euler", frame=f)
        pb.rotation_euler = (0, 0, 0)
        act.use_fake_user = True
    rig.animation_data.action = None
    bpy.context.scene.frame_end = 20
    return rig, body, head, imgs


def make_hero_set():
    root = os.path.join(OUT, "character")
    rig, body, head, imgs = hero(root)
    export(os.path.join(root, "Hero.fbx"))
    #embedded variant
    export(os.path.join(OUT, "embedded", "Hero_embedded.fbx"), path_mode="COPY", embed_textures=True)
    #ascii twin via the hjt writer
    from hjt_fbx_plus.fbx import binary
    from hjt_fbx_plus.fbx.ascii_writer import write_ascii
    res = binary.read_binary(os.path.join(root, "Hero.fbx"))
    with open(os.path.join(root, "Hero_ascii.fbx"), "w", encoding="utf-8") as fh:
        write_ascii(fh, res.root, res.version)
    res.close()


def make_crate():
    reset()
    root = os.path.join(OUT, "props")
    tex = os.path.join(root, "Textures")
    save_png(os.path.join(tex, "Crate_BaseColor.png"), 64, 64, solid(0.6, 0.4, 0.2, stripe=6))
    save_png(os.path.join(tex, "Crate_Normal.png"), 64, 64, normal_flat)
    save_png(os.path.join(tex, "Crate_Roughness.png"), 64, 64, solid(0.7, 0.7, 0.7))
    save_png(os.path.join(tex, "Crate_Metallic.png"), 64, 64, solid(0.0, 0.0, 0.0))
    objs = []
    for i, size in enumerate((1.0, 1.0, 1.0)):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(0, 0, 0.5))
        ob = bpy.context.active_object
        ob.name = "Crate_LOD%d" % i
        if i:
            md = ob.modifiers.new("dec", "DECIMATE")
            md.ratio = 0.5 / i
        objs.append(ob)
    mat = bpy.data.materials.new("Crate")  #no textures linked on purpose: name matching must find them
    for ob in objs:
        ob.data.materials.append(mat)
    bpy.ops.mesh.primitive_cube_add(size=1.05, location=(0, 0, 0.5))
    col = bpy.context.active_object
    col.name = "UCX_Crate_00"
    objs.append(col)
    export(os.path.join(root, "Crate.fbx"), bake_anim=False)


def make_genshin_like():
    reset()
    root = os.path.join(OUT, "genshin_like")
    tex = os.path.join(root, "Texture2D")
    pre = "Avatar_Girl_Sword_TestChar"
    save_png(os.path.join(tex, pre + "_Tex_Body_Diffuse.png"), 64, 64, solid(0.8, 0.2, 0.2))
    save_png(os.path.join(tex, pre + "_Tex_Body_Lightmap.png"), 64, 64, solid(0.5, 0.5, 0.5))
    save_png(os.path.join(tex, pre + "_Tex_Body_Shadow_Ramp.png"), 64, 4, solid(0.6, 0.5, 0.7))
    save_png(os.path.join(tex, pre + "_Tex_Hair_Diffuse.png"), 64, 64, solid(0.2, 0.2, 0.8))
    save_png(os.path.join(tex, pre + "_Tex_Hair_Lightmap.png"), 64, 64, solid(0.5, 0.5, 0.5))
    save_png(os.path.join(tex, pre + "_Tex_Face_Diffuse.png"), 64, 64, solid(1.0, 0.9, 0.8))
    save_png(os.path.join(tex, "Avatar_Girl_Tex_FaceLightmap.png"), 64, 64, solid(0.4, 0.4, 0.4))
    for part, z in (("Body", 1.0), ("Hair", 1.8), ("Face", 1.6), ("Dress", 0.7)):
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, z))
        ob = bpy.context.active_object
        ob.name = part
        ob.data.materials.append(bpy.data.materials.new("%s_Mat_%s" % (pre, part)))
    export(os.path.join(root, pre + ".fbx"), bake_anim=False)


def make_zzz_like():
    reset()
    root = os.path.join(OUT, "zzz_like")
    tex = os.path.join(root, "Textures")
    for part in ("Body", "Face"):
        save_png(os.path.join(tex, "TestChar_%s_D.png" % part), 64, 64, solid(0.9, 0.3, 0.6) if part == "Body" else solid(1, 0.9, 0.85))
        save_png(os.path.join(tex, "TestChar_%s_M.png" % part), 64, 64, solid(0.1, 0.5, 0.9))
        save_png(os.path.join(tex, "TestChar_%s_N.png" % part), 64, 64, normal_flat)
    save_png(os.path.join(tex, "Face_Lightmap_Female.png"), 64, 64, solid(0.5, 0.5, 0.5))
    os.makedirs(os.path.join(root, "Materials"), exist_ok=True)
    for part in ("Body", "Face"):
        mname = "MAT_TestChar_%s_UI" % part
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, 1.0 if part == "Body" else 1.6))
        ob = bpy.context.active_object
        ob.name = "TestChar_" + part
        ob.data.materials.append(bpy.data.materials.new(mname))
        envs = {"_MainTex": "TestChar_%s_D" % part, "_OtherDataTex": "TestChar_%s_M" % part}
        envs["_LightTex"] = "Face_Lightmap_Female" if part == "Face" else "TestChar_%s_N" % part
        data = {"m_Name": mname, "m_Shader": {"Name": "miHoYo/ZZZ/Char%s" % part},
                "m_SavedProperties": {"m_TexEnvs": [{"Key": k, "Value": {"m_Texture": {"Name": v}, "m_Scale": {"X": 1, "Y": 1}, "m_Offset": {"X": 0, "Y": 0}}} for k, v in envs.items()],
                                      "m_Floats": [{"Key": "_CullMode", "Value": 2.0}],
                                      "m_Colors": [{"Key": "_Color", "Value": {"r": 1, "g": 1, "b": 1, "a": 1}}]}}
        with open(os.path.join(root, "Materials", mname + ".json"), "w", encoding="utf-8-sig") as fh:
            json.dump(data, fh, indent=2)
    bpy.ops.mesh.primitive_cube_add(size=0.31, location=(0, 0, 1.75))
    hs = bpy.context.active_object
    hs.name = "TestChar_HairShadow"
    hs.data.materials.append(bpy.data.materials.new("MAT_TestChar_HairShadow_UI"))
    export(os.path.join(root, "TestChar.fbx"), bake_anim=False)


def make_dupes():
    #two characters, both w a Textures/Body_D.png (different colors). each must get its OWN
    for who, color in (("Alpha", (1.0, 0.0, 0.0)), ("Beta", (0.0, 1.0, 0.0))):
        reset()
        root = os.path.join(OUT, "dupes", who)
        save_png(os.path.join(root, "Textures", "Body_D.png"), 32, 32, solid(*color))
        bpy.ops.mesh.primitive_cube_add()
        ob = bpy.context.active_object
        ob.name = who
        ob.data.materials.append(bpy.data.materials.new("Body"))
        export(os.path.join(root, who + ".fbx"), bake_anim=False)


def make_inbetween():
    reset()
    root = os.path.join(OUT, "inbetween")
    bpy.ops.mesh.primitive_cube_add()
    ob = bpy.context.active_object
    ob.name = "Box"
    ob.shape_key_add(name="Basis")
    k1 = ob.shape_key_add(name="Half", from_mix=False)
    k2 = ob.shape_key_add(name="Full", from_mix=False)
    for p in k1.data:
        p.co.z += 0.5
    for p in k2.data:
        p.co.z += 1.0
    src = os.path.join(root, "_src.fbx")
    export(src, bake_anim=False)
    from hjt_fbx_plus.fbx.document import FbxDocument
    from hjt_fbx_plus.fbx.elements import FbxArray
    from hjt_fbx_plus.fbx import binary
    doc = FbxDocument.open(src, lazy=False)
    chs = {c.name: c for c in doc.objects_of(b"Deformer", b"BlendShapeChannel")}
    shapes = {s.name: s for s in doc.objects_of(b"Geometry", b"Shape")}
    conns = doc.root.find(b"Connections")
    for c in conns.children:
        if c.props[1] == shapes["Half"].uid and c.props[2] == chs["Half"].uid:
            c.props[2] = chs["Full"].uid
    conns.children = [c for c in conns.children if c.props[1] != chs["Half"].uid]
    objs = doc.root.find(b"Objects")
    objs.children = [n for n in objs.children if n is not chs["Half"].node]
    chs["Full"].node.find(b"FullWeights").props = [FbxArray.from_values("d", array.array("d", [50.0, 100.0]))]
    binary.write_binary_file(os.path.join(root, "Inbetween.fbx"), doc.root, doc.version)
    doc.close()
    os.remove(src)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    for fn in (make_hero_set, make_crate, make_genshin_like, make_zzz_like, make_dupes, make_inbetween):
        fn()
        print("built", fn.__name__)
    print("ASSETS DONE", OUT)


main()
sys.stdout.flush()
os._exit(0)
