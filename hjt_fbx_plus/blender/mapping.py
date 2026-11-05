# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#who came from where. before import we snapshot bpy.data, after import we diff it, then read the
# hidden hjt_fbx_uid props the prepare step stamped into the fbx -> exact fbx uid -> blender datablock map
#the uid props get deleted right after so nothing extra stays on ur stuff

from __future__ import annotations

import re

import bpy

from ..fbx.prepare import UID_PROP

COLLECTIONS = ("objects", "meshes", "materials", "images", "actions", "armatures", "cameras", "lights",
               "collections", "node_groups", "shape_keys", "textures")
_DUP = re.compile(r"\.\d{3,}$")


def _key(idb):
    return getattr(idb, "session_uid", None) or idb.as_pointer()


def snapshot():
    return {name: {_key(i) for i in getattr(bpy.data, name)} for name in COLLECTIONS if hasattr(bpy.data, name)}


def diff(before):
    out = {}
    for name, keys in before.items():
        out[name] = [i for i in getattr(bpy.data, name) if _key(i) not in keys]
    return out


class Mapping:
    def __init__(self):
        self.objects = {}
        self.meshes = {}
        self.materials = {}
        self.images = {}
        self.bones = {}
        self.exact = False

    def object_for_model(self, uid):
        return self.objects.get(str(uid))

    def as_counts(self):
        return {"objects": len(self.objects), "meshes": len(self.meshes), "materials": len(self.materials),
                "images": len(self.images), "bones": len(self.bones), "exact": self.exact}


def _pop(idb, prop):
    try:
        v = idb.get(prop)
    except (TypeError, AttributeError):
        return None
    if v is not None:
        try:
            del idb[prop]
        except (KeyError, TypeError):
            pass
    return v


def build(new, prop=UID_PROP):
    m = Mapping()
    for ob in new.get("objects", []):
        uid = _pop(ob, prop)
        if uid is not None:
            m.objects[str(uid)] = ob
        data = ob.data
        if data is not None:
            duid = _pop(data, prop)
            if duid is not None and ob.type == "MESH":
                m.meshes[str(duid)] = data
        if ob.type == "ARMATURE" and ob.pose is not None:
            for pb in ob.pose.bones:
                buid = _pop(pb, prop)
                if buid is not None:
                    m.bones[str(buid)] = (ob, pb.name)
            for b in ob.data.bones:
                _pop(b, prop)
    for ma in new.get("materials", []):
        uid = _pop(ma, prop)
        if uid is not None:
            m.materials[str(uid)] = ma
    for img in new.get("images", []):
        uid = _pop(img, prop)
        if uid is not None:
            m.images[str(uid)] = img
    for name in ("cameras", "lights", "armatures"):
        for idb in new.get(name, []):
            _pop(idb, prop)
    m.exact = bool(m.objects or m.materials)
    return m


def build_by_name(new, summary, doc):
    #fallback when the engine dropped our uid props (fbx 6 via ufbx etc). names + material slot order
    m = Mapping()
    objs = {}
    for ob in new.get("objects", []):
        objs.setdefault(_DUP.sub("", ob.name), []).append(ob)
    for model in doc.objects_of(b"Model"):
        cands = objs.get(model.name)
        if cands:
            m.objects[_uid(model.uid)] = cands.pop(0)
    for model_uid, ob in list(m.objects.items()):
        if ob.type != "MESH":
            continue
        model = doc.get(_from_uid(model_uid, doc))
        if model is None:
            continue
        geoms = doc.children(model.uid, b"Geometry")
        if geoms:
            m.meshes.setdefault(_uid(geoms[0].uid), ob.data)
        mats = doc.children(model.uid, b"Material")
        for i, mat in enumerate(mats):
            if i < len(ob.material_slots) and ob.material_slots[i].material is not None:
                m.materials.setdefault(_uid(mat.uid), ob.material_slots[i].material)
    for ob in new.get("objects", []):
        if ob.type == "ARMATURE":
            for bone_model in doc.objects_of(b"Model"):
                if bone_model.name in ob.pose.bones:
                    m.bones.setdefault(_uid(bone_model.uid), (ob, bone_model.name))
    return m


def _uid(uid):
    from ..fbx.prepare import uid_key
    return uid_key(uid)


def _from_uid(key, doc):
    for uid in doc.objects:
        if _uid(uid) == key:
            return uid
    return None
