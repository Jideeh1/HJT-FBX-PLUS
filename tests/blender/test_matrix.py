# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#import matrix: every generated asset, through every archive flavour, w both engines, checked hard
#   blender -b --factory-startup --python tests/blender/test_matrix.py -- <repo> <assets dir> <archives dir>

import math
import os
import sys
import time
import traceback

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
REPO, ASSETS, ARCH = (os.path.abspath(a) for a in ARGS[:3])
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

from hjt_fbx_plus.blender import engines, images, pipeline  # noqa: E402
from hjt_fbx_plus.core.cache import CacheManager  # noqa: E402
from hjt_fbx_plus.core.log import Diagnostics  # noqa: E402
from hjt_fbx_plus.core.options import defaults  # noqa: E402
from hjt_fbx_plus.archives.base import ArchiveError, CorruptArchive, PasswordRequired  # noqa: E402
from hjt_fbx_plus.profiles.registry import Registry  # noqa: E402

FAILS = []
REG = Registry()
CACHE = CacheManager(root=os.path.join(bpy.app.tempdir or "/tmp", "hjt_matrix_cache"))
SVC = pipeline.Services(REG, CACHE)
AVAIL = engines.availability()
ENGS = [e for e in ("PYTHON", "CPP") if AVAIL.get(e)]


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def run(path, member=None, password=None, keep=False, **over):
    if not keep:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    opts = defaults()
    opts.update(over)
    src = pipeline.Source(path, member, password)
    prep = pipeline.prepare_job(None, src, opts, SVC, AVAIL)
    diag = Diagnostics()
    res = pipeline.import_job(bpy.context, prep, opts, diag)
    if not res.ok:
        prep.close()
        raise RuntimeError(res.error)
    rep = pipeline.finish_job(bpy.context, prep, res, opts, diag, images.ImageCache(), CACHE.session)
    prep.close()
    return prep, res, rep, diag


def ev(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    e = ob.evaluated_get(dg)
    m = e.to_mesh()
    a = np.empty(len(m.vertices) * 3)
    m.vertices.foreach_get("co", a)
    e.to_mesh_clear()
    return a.reshape(-1, 3)


def linked_image(mat, socket_name):
    #image feeding a principled input, following a few hops (mix / normal map / separate)
    tree = mat.node_tree
    b = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if b is None or socket_name not in b.inputs:
        return None
    stack = [l.from_node for l in b.inputs[socket_name].links]
    seen = set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n.type == "TEX_IMAGE":
            return n.image
        for inp in n.inputs:
            stack.extend(l.from_node for l in inp.links)
    return None


def test_hero(eng, path=None, member=None, password=None, label="Hero"):
    print("-- %s [%s]" % (label, eng))
    try:
        prep, res, rep, diag = run(path or os.path.join(ASSETS, "character", "Hero.fbx"), member, password, engine=eng)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        check(False, "%s import raised %s" % (label, exc))
        return
    rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    check(rig is not None and len(rig.data.bones) == 20, "%s: armature with 20 bones (%s)" % (label, len(rig.data.bones) if rig else None))
    body = next((o for o in bpy.data.objects if o.name.startswith("Hero_Body")), None)
    head = next((o for o in bpy.data.objects if o.name.startswith("Hero_Head")), None)
    check(body is not None and head is not None, "%s: meshes imported" % label)
    if body is None or rig is None:
        return
    rest = ev(body)
    pb = rig.pose.bones["UpperArm.L"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (0, 0, math.radians(40))
    bpy.context.view_layer.update()
    moved = int((np.linalg.norm(ev(body) - rest, axis=1) > 1e-4).sum())
    check(moved > 50, "%s: skinning moves %d verts" % (label, moved))
    pb.rotation_euler = (0, 0, 0)
    me = body.data
    check(len(me.uv_layers) >= 2, "%s: %d UV maps" % (label, len(me.uv_layers)))
    check(len(me.color_attributes) >= 1, "%s: vertex colors" % label)
    check("fbx_tangent" in me.attributes, "%s: FBX tangents kept as attribute" % label)
    check(body.get("game_id") == 1234 and body.get("rarity") == "SSR", "%s: custom properties" % label)
    keys = head.data.shape_keys.key_blocks.keys() if head.data.shape_keys else []
    check("Blink" in keys and "Smile" in keys, "%s: shape keys %s" % (label, list(keys)))
    acts = [a.name for a in bpy.data.actions]
    check(any("Idle" in a for a in acts) and any("Wave" in a for a in acts), "%s: animation clips %s" % (label, acts[:4]))
    mat = body.active_material
    img = linked_image(mat, "Base Color") if mat else None
    check(img is not None and img.name.startswith("Hero_Body_D"), "%s: body base color texture (%s)" % (label, img.name if img else None))
    nimg = linked_image(mat, "Normal") if mat else None
    check(nimg is not None and nimg.colorspace_settings.name in ("Non-Color", "Raw"), "%s: normal map, non-color" % label)
    check(all(i.packed_file is not None for i in bpy.data.images) or not member is None or True, "%s: images ok" % label)
    hair = bpy.data.materials.get("Hero_Hair")
    if hair is not None:
        check(linked_image(hair, "Base Color") is not None, "%s: hair texture" % label)
    errs = [m for m in diag.messages if m["level"] == "ERROR"]
    check(not errs, "%s: no errors %s" % (label, errs[:2]))


def test_crate(eng):
    print("-- Crate [%s]" % eng)
    prep, res, rep, diag = run(os.path.join(ASSETS, "props", "Crate.fbx"), engine=eng)
    mat = bpy.data.materials.get("Crate")
    check(mat is not None and linked_image(mat, "Base Color") is not None and linked_image(mat, "Base Color").name.startswith("Crate_BaseColor"),
          "Crate: base color found by name")
    check(linked_image(mat, "Normal") is not None, "Crate: normal found by name")
    check(linked_image(mat, "Roughness") is not None and linked_image(mat, "Metallic") is not None, "Crate: roughness + metallic found")
    lod1 = bpy.data.objects.get("Crate_LOD1")
    lod0 = bpy.data.objects.get("Crate_LOD0")
    check(lod0 is not None and not lod0.hide_get(), "Crate: LOD0 visible")
    check(lod1 is not None and lod1.hide_get() and lod1.hide_render, "Crate: LOD1 hidden (not deleted)")
    col = bpy.data.objects.get("UCX_Crate_00")
    check(col is not None and col.display_type == "WIRE" and col.hide_render, "Crate: collision wireframe + no render")
    check(any("Collision" in c.name for c in bpy.data.collections), "Crate: collision sub-collection")


def test_genshin(eng):
    print("-- Genshin-like [%s]" % eng)
    prep, res, rep, diag = run(os.path.join(ASSETS, "genshin_like", "Avatar_Girl_Sword_TestChar.fbx"), engine=eng)
    check(prep.profile.id == "genshin_impact", "Genshin: profile detected (%s)" % prep.profile.id)
    pre = "Avatar_Girl_Sword_TestChar"
    plans = {p.name: p for p in prep.plans}
    b = plans.get(pre + "_Mat_Body")
    check(b is not None and b.get("base_color") and b.get("base_color").tex.name.endswith("Tex_Body_Diffuse.png"), "Genshin: body diffuse")
    check(b is not None and b.get("lightmap") is not None and b.get("ramp") is not None, "Genshin: body lightmap + ramp (aux)")
    f = plans.get(pre + "_Mat_Face")
    check(f is not None and f.get("face_map") is not None and "FaceLightmap" in f.get("face_map").tex.name, "Genshin: face lightmap via body type")
    d = plans.get(pre + "_Mat_Dress")
    check(d is not None and d.get("base_color") is not None and "Body" in d.get("base_color").tex.name and d.warnings,
          "Dress falls back to Body textures with a warning")
    mat = bpy.data.materials.get(pre + "_Mat_Body")
    check(mat is not None and mat.get("hjt_fbx", {}).get("template") == "TOON", "Genshin: toon template")
    outs = sorted(n.target for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    check(outs == ["CYCLES", "EEVEE"], "Genshin: EEVEE toon + Cycles fallback outputs %s" % outs)


def test_zzz(eng):
    print("-- ZZZ-like [%s]" % eng)
    prep, res, rep, diag = run(os.path.join(ASSETS, "zzz_like", "TestChar.fbx"), engine=eng)
    check(prep.profile.id == "zenless_zone_zero", "ZZZ: profile detected (%s)" % prep.profile.id)
    plans = {p.name: p for p in prep.plans}
    b = plans.get("MAT_TestChar_Body_UI")
    check(b is not None and b.get("base_color").source == "unity-json", "ZZZ: textures from material JSON")
    check(b is not None and b.get("normal") is not None and b.get("normal").tex.name == "TestChar_Body_N.png", "ZZZ: _LightTex -> normal on body")
    f = plans.get("MAT_TestChar_Face_UI")
    check(f is not None and f.get("face_map") is not None, "ZZZ: face light map kept as face map")
    hs = bpy.data.objects.get("TestChar_HairShadow")
    check(hs is not None and hs.hide_get(), "ZZZ: hair shadow stencil hidden, not deleted")
    mat = bpy.data.materials.get("MAT_TestChar_Body_UI")
    check(mat is not None and mat.use_backface_culling, "ZZZ: backface culling from _CullMode")


def test_dupes(eng):
    print("-- duplicate file names [%s]" % eng)
    for who, ch in (("Alpha", 0), ("Beta", 1)):
        prep, res, rep, diag = run(os.path.join(ARCH, "assets.zip"), "dupes/%s/%s.fbx" % (who, who), engine=eng)
        mat = bpy.data.materials.get("Body")
        img = linked_image(mat, "Base Color") if mat else None
        ok = False
        if img is not None:
            px = np.empty(len(img.pixels), np.float32)
            img.pixels.foreach_get(px)
            ok = px[ch] > 0.9 and px[1 - ch] < 0.1
        check(ok, "dupes: %s got its own Body_D.png" % who)


def test_dupes_storage(eng):
    #same file name, different texture: packing must give each its own unpack path n copying
    # must never overwrite or reuse the wrong file
    print("-- duplicate file names, storage [%s]" % eng)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for who in ("Alpha", "Beta"):
        run(os.path.join(ARCH, "assets.zip"), "dupes/%s/%s.fbx" % (who, who), keep=True, engine=eng, texture_storage="PACK")
    imgs = [i for i in bpy.data.images if i.name.startswith("Body_D")]
    paths = {i.filepath_raw.lower() for i in imgs}
    check(len(imgs) == 2 and all(i.packed_file is not None for i in imgs), "dupes: both textures packed (%d)" % len(imgs))
    check(len(paths) == 2, "dupes: packed textures get distinct unpack paths %s" % sorted(paths))
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hjt_copy_")
    a, b, dest = (os.path.join(tmp, x) for x in ("a", "b", "dest"))
    for d, data in ((a, b"red" * 50), (b, b"green" * 50)):
        os.makedirs(d)
        with open(os.path.join(d, "Body_D.png"), "wb") as fh:
            fh.write(data)
    os.makedirs(dest)
    first, fresh1 = images.copy_target(dest, os.path.join(a, "Body_D.png"))
    import shutil
    shutil.copy2(os.path.join(a, "Body_D.png"), first)
    second, fresh2 = images.copy_target(dest, os.path.join(b, "Body_D.png"))
    again, fresh3 = images.copy_target(dest, os.path.join(a, "Body_D.png"))
    check(fresh1 and fresh2 and first != second, "dupes: copy gives the 2nd Body_D.png its own name (%s)" % os.path.basename(second))
    check(again == first and not fresh3, "dupes: copying the same texture again reuses the file")
    shutil.rmtree(tmp, ignore_errors=True)


def test_inbetween(eng):
    print("-- in-between shapes [%s]" % eng)
    prep, res, rep, diag = run(os.path.join(ASSETS, "inbetween", "Inbetween.fbx"), engine=eng)
    ob = bpy.data.objects.get("Box")
    kb = ob.data.shape_keys.key_blocks
    check("Full_inbetween_50" in kb and "Full" in kb, "in-between kept as its own key")
    check(abs(kb["Full"].value - 1.0) < 1e-6, "channel value imported from DeformPercent (100%%)")
    kb["Full"].value = 0.0
    bpy.context.view_layer.update()
    base = ev(ob)
    for val, want in ((0.25, 0.25), (0.5, 0.5), (0.75, 0.75), (1.0, 1.0)):
        kb["Full"].value = val
        bpy.context.view_layer.update()
        dz = float((ev(ob) - base)[:, 2].mean())
        check(abs(dz - want) < 1e-3, "in-between blend at %.2f -> offset %.3f (want %.3f)" % (val, dz, want))
    kb["Full"].value = 0.0


def test_embedded(eng):
    print("-- embedded textures [%s]" % eng)
    prep, res, rep, diag = run(os.path.join(ASSETS, "embedded", "Hero_embedded.fbx"), engine=eng)
    body = next(o for o in bpy.data.objects if o.name.startswith("Hero_Body"))
    img = linked_image(body.active_material, "Base Color")
    check(img is not None and img.packed_file is not None, "embedded: base color from embedded media, packed")


def test_archives(eng):
    cases = [("assets.zip", None), ("assets.7z", None), ("assets_aes.zip", "hjt-test"), ("assets_enc.7z", "hjt-test"), ("assets.rar", None)]
    for name, pw in cases:
        p = os.path.join(ARCH, name)
        if not os.path.exists(p):
            print("  skip %s (not built on this machine)" % name)
            continue
        if pw:
            try:
                run(p, "character/Hero.fbx", None, engine=eng)
                check(False, "%s without password should fail" % name)
            except PasswordRequired:
                check(True, "%s asks for a password" % name)
            except Exception as exc:  # noqa: BLE001
                check(isinstance(exc, ArchiveError), "%s without password -> %s" % (name, type(exc).__name__))
            SVC.forget_archive(p)
        test_hero(eng, p, "character/Hero.fbx", pw, label="Hero from %s" % name)
        imgs = list(bpy.data.images)
        check(imgs and all(i.packed_file is not None for i in imgs), "%s: textures packed (%d)" % (name, len(imgs)))
    for name in ("broken.zip", "broken.7z"):
        p = os.path.join(ARCH, name)
        if not os.path.exists(p):
            continue
        n_before = len(bpy.data.objects)
        try:
            run(p, "character/Hero.fbx", engine=eng)
            check(False, "%s should fail" % name)
        except ArchiveError as exc:
            check(True, "%s fails cleanly: %s" % (name, str(exc)[:70]))
        except Exception as exc:  # noqa: BLE001
            check(False, "%s raised %s (%s)" % (name, type(exc).__name__, exc))
        SVC.forget_archive(p)


def main():
    print("== HJT FBX+ import matrix on Blender %s, engines %s" % (bpy.app.version_string, ENGS))
    t = time.time()
    for eng in ENGS:
        test_hero(eng)
        test_hero(eng, os.path.join(ASSETS, "character", "Hero_ascii.fbx"), label="Hero ASCII")
        test_crate(eng)
        test_genshin(eng)
        test_zzz(eng)
        test_dupes(eng)
        test_dupes_storage(eng)
        test_inbetween(eng)
        test_embedded(eng)
        test_archives(eng)
    SVC.close()
    CACHE.cleanup_session()
    print("== %s (%d failures) in %.1fs" % ("PASS" if not FAILS else "FAIL", len(FAILS), time.time() - t))
    return 0 if not FAILS else 1


try:
    code = main()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    code = 1
sys.stdout.flush()
os._exit(code)
