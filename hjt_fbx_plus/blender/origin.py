# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#character origin cleanup. unity rips come in w the armature rotated 90 on X (their Y up baked on the
# object, not the bones), sometimes scaled 0.01, n the origin wherever the ripper dropped it
#after this: armature rotation 0, scale 1, origin on the floor right under the hips. every skinned mesh
#shares that origin so theres ONE dot to grab, not a random one floating next to the feet
#
#nothing visibly moves: the old object transform gets baked into the rest bones n the mesh data,
# pose keys live in bone space so they dont care, root location keys only get rescaled if there was scale

from __future__ import annotations

import re

import bpy
from mathutils import Matrix, Vector

_HIPS = re.compile(r"(?i)pelvis|(^|[^a-z])hips?($|[^a-z])|(^|[^a-z])(cog|center)$")  #Bip001Pelvis, pelvis, mixamorig:Hips
_LOC_PATH = re.compile(r'^pose\.bones\["(?:[^"\\]|\\.)*"\]\.location$')
EPS = 1e-4


def _children(arm):
    return [o for o in bpy.data.objects if o.parent == arm]


def _skinned(ob, arm):
    return ob.type == "MESH" and any(m.type == "ARMATURE" and m.object == arm for m in ob.modifiers)


_FEET = re.compile(r"(?i)(toe|ball|foot)")


def _floor_point(arm, meshes):
    #floor right under the hips. bbox centre is fooled by capes, tails n big skirts (pgr laiya: 12 cm off)
    pts = []
    for ob in meshes:
        mw = ob.matrix_world
        pts += [mw @ Vector(c) for c in ob.bound_box]
    if not pts:
        return None
    zmin = min(p.z for p in pts)
    bones = arm.data.bones
    hips = next((b for b in bones if "pelvis" in b.name.lower()), None) or next((b for b in bones if _HIPS.search(b.name)), None)
    if hips is not None:
        h = arm.matrix_world @ hips.head_local
        x, y, ztop = h.x, h.y, h.z
    else:
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        x, y, ztop = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2
    #height: the rip almost always has the floor right already. lowest vertex lies when stuff hangs
    # below the feet (gratia has 60 cm of cloth under her lol), so only fix a height thats nonsense
    z = arm.matrix_world.translation.z
    if not (zmin - 0.05 <= z <= ztop):
        feet = [(arm.matrix_world @ b.head_local).z for b in arm.data.bones if _FEET.search(b.name)]
        z = min(feet) if feet else zmin
    return Vector((x, y, z))


def _object_keyed(ob):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return False
    from . import compat
    return any(not fc.data_path.startswith("pose.") for fc in compat.action_fcurves(ad.action))


def needs_fix(arm, meshes=None):
    #-> (bool, reason). cheap enough for a panel
    meshes = meshes if meshes is not None else [o for o in _children(arm) if _skinned(o, arm)]
    loc, rot, scale = arm.matrix_world.decompose()
    if rot.angle > EPS or any(abs(s - 1.0) > EPS for s in scale):
        return True, "rotation/scale"
    target = _floor_point(arm, meshes)
    if target is not None and (target - loc).length > 0.01:
        return True, "origin"
    for ob in meshes:
        if (ob.matrix_world.translation - loc).length > 0.01:
            return True, "mesh origin"
    return False, ""


def _scale_location_keys(arm, factor):
    from . import chains
    n = 0
    for action in chains._actions_for(arm):
        for _container, fcs in chains._bags(action):
            for fc in fcs:
                if not _LOC_PATH.match(fc.data_path):
                    continue
                for k in fc.keyframe_points:
                    k.co[1] *= factor
                    k.handle_left[1] *= factor
                    k.handle_right[1] *= factor
                    n += 1
                fc.update()
    for pb in arm.pose.bones:
        pb.location = pb.location * factor
    return n


def center_character(arm, diag=None, model=""):
    #-> dict or None when nothing was done
    if arm is None or arm.type != "ARMATURE" or arm.library is not None:
        return None
    if arm.parent is not None:
        return None  #parented to something else, thats a scene setup not a loose character
    if _object_keyed(arm):
        if diag is not None:
            diag.info("Origin left alone on '%s': the armature object itself is animated." % arm.name, model)
        return None
    kids = _children(arm)
    meshes = [o for o in kids if _skinned(o, arm)]
    todo, _why = needs_fix(arm, meshes)
    if not todo:
        return None
    if arm.data.users > 1:
        if diag is not None:
            diag.info("Origin left alone on '%s': its armature data is shared with other objects." % arm.name, model)
        return None
    old = arm.matrix_world.copy()
    _loc, _rot, scale = old.decompose()
    if max(scale) - min(scale) > 1e-4 * max(scale):
        if diag is not None:
            diag.warning("Origin left alone on '%s': non-uniform object scale %s." % (arm.name, tuple(round(s, 4) for s in scale)), model)
        return None
    target = _floor_point(arm, meshes) or old.translation.copy()
    new = Matrix.Translation(target)
    keep = {o: o.matrix_world.copy() for o in kids}
    #old armature space -> new armature space. bones keep their world rest, so poses dont budge
    arm.data.transform(new.inverted() @ old)
    arm.matrix_world = new
    keys = 0
    if abs(scale[0] - 1.0) > EPS:
        keys = _scale_location_keys(arm, scale[0])
    for o in kids:
        if o in meshes:
            #one origin for the whole character: bake the mesh transform into its verts (shape keys too)
            if o.data.users > 1:
                o.data = o.data.copy()
            bake = new.inverted() @ keep[o]
            o.matrix_parent_inverse = Matrix.Identity(4)
            o.matrix_basis = Matrix.Identity(4)
            o.data.transform(bake, shape_keys=True)  #default skips shape keys n faces w keys jump away
            if bake.determinant() < 0:
                o.data.flip_normals()
            o.data.update()
        else:
            o.matrix_world = keep[o]
    bpy.context.view_layer.update()
    return {"moved": (target - old.translation).length, "scale": scale[0], "keys": keys, "meshes": len(meshes)}
