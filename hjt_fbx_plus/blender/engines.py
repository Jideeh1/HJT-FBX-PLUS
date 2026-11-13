# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#wrappers around blenders own fbx importers
#  PYTHON = import_scene.fbx (io_scene_fbx, every version, most options)
#  CPP    = wm.fbx_import (ufbx based, 4.5+, way faster, reads ascii n fbx 6 natively)
# options get filtered against what the operator really has, so a version w/o some option
#just doesnt get it instead of throwing TypeError at the user

from __future__ import annotations

import gc

import bpy


class Engine:
    def __init__(self, eid, label, op_path, mapping):
        self.id = eid
        self.label = label
        self.op_path = op_path
        self.mapping = mapping  #our setting name -> operator property name
        self._props = None

    def _op(self):
        mod, name = self.op_path.split(".")
        return getattr(getattr(bpy.ops, mod, None), name, None)

    def available(self):
        op = self._op()
        if op is None:
            return False
        try:
            op.get_rna_type()
            return True
        except Exception:  # noqa: BLE001
            return False

    def props(self):
        if self._props is None:
            try:
                self._props = {p.identifier for p in self._op().get_rna_type().properties}
            except Exception:  # noqa: BLE001
                self._props = set()
        return self._props

    def build_kwargs(self, settings):
        #-> (kwargs, ignored setting names)
        have = self.props()
        kw = {}
        ignored = []
        for ours, theirs in self.mapping.items():
            if ours not in settings:
                continue
            if theirs in have:
                kw[theirs] = settings[ours]
            elif ours in USER_FACING:
                ignored.append(ours)
        return kw, ignored

    def run(self, filepath, kwargs):
        op = self._op()
        if op is None:
            raise RuntimeError("%s is not available in this Blender" % self.label)
        #the python importer allocates like crazy n every gc pass rescans everything alive, incl our
        #parsed fbx tree. freezing the old stuff first = ~0.5 s less on a 1.4M vert file. free speed
        gc.freeze()
        try:
            return op(filepath=filepath, **kwargs)
        finally:
            gc.unfreeze()


#settings the user can actually see, so we only complain about these being ignored
USER_FACING = {"automatic_bone_orientation", "primary_bone_axis", "secondary_bone_axis", "force_connect_children",
               "use_prepost_rot", "use_manual_orientation", "bake_space_transform"}

PYTHON = Engine("PYTHON", "Blender FBX (Python)", "import_scene.fbx", {
    "global_scale": "global_scale", "use_custom_normals": "use_custom_normals", "colors_type": "colors_type",
    "use_anim": "use_anim", "anim_offset": "anim_offset", "ignore_leaf_bones": "ignore_leaf_bones",
    "force_connect_children": "force_connect_children", "automatic_bone_orientation": "automatic_bone_orientation",
    "primary_bone_axis": "primary_bone_axis", "secondary_bone_axis": "secondary_bone_axis",
    "use_prepost_rot": "use_prepost_rot", "use_custom_props": "use_custom_props",
    "use_custom_props_enum_as_string": "use_custom_props_enum_as_string", "use_subsurf": "use_subsurf",
    "use_manual_orientation": "use_manual_orientation", "axis_forward": "axis_forward", "axis_up": "axis_up",
    "bake_space_transform": "bake_space_transform", "use_image_search": "use_image_search",
    "mtl_name_collision_mode": "mtl_name_collision_mode", "use_alpha_decals": "use_alpha_decals",
})

CPP = Engine("CPP", "Blender FBX (C++)", "wm.fbx_import", {
    "global_scale": "global_scale", "use_custom_normals": "use_custom_normals", "colors_type": "import_colors",
    "use_anim": "use_anim", "anim_offset": "anim_offset", "ignore_leaf_bones": "ignore_leaf_bones",
    "use_custom_props": "use_custom_props", "use_custom_props_enum_as_string": "use_custom_props_enum_as_string",
    "use_subsurf": "import_subdivision", "validate_meshes": "validate_meshes",
    "mtl_name_collision_mode": "mtl_name_collision_mode",
    "automatic_bone_orientation": "__none__", "primary_bone_axis": "__none__", "secondary_bone_axis": "__none__",
    "force_connect_children": "__none__", "use_prepost_rot": "__none__", "use_manual_orientation": "__none__",
    "bake_space_transform": "__none__",
})

ENGINES = {"PYTHON": PYTHON, "CPP": CPP}


def availability():
    #main thread only (pokes bpy.ops). pass the result to choose() from worker threads
    return {"PYTHON": PYTHON.available(), "CPP": CPP.available()}


class _Avail:
    def __init__(self, d):
        self.d = d

    def __call__(self, eng):
        return self.d.get(eng.id, False) if self.d is not None else eng.available()


ANIM_HEAVY = 2000  #animation curves. 40 clips on a 256 bone rig: python importer 30 s, c++ 2.6 s, same poses
_PY_ONLY_DEFAULTS = {"automatic_bone_orientation": False, "primary_bone_axis": "Y", "secondary_bone_axis": "X",
                     "force_connect_children": False, "use_prepost_rot": True, "use_manual_orientation": False,
                     "bake_space_transform": False}


def python_only_options(settings):
    return sorted(k for k, d in _PY_ONLY_DEFAULTS.items() if k in (settings or {}) and settings[k] != d)


def choose(requested, fbx_format, fbx_version, avail=None, anim_curves=0, settings=None):
    #-> (engine, notes). AUTO = python importer (most options, battle tested) unless the file needs ufbx,
    # or its animation heavy n nothing python-only is switched on. the python one crawls on big takes
    ok = _Avail(avail)
    notes = []
    v6 = 0 < fbx_version < 7000
    if requested == "CPP":
        if ok(CPP):
            return CPP, notes
        notes.append("The C++ FBX importer needs Blender 4.5 or newer; using the Python importer.")
        if v6:
            return None, notes + ["FBX %d files can only be read by the C++ importer (Blender 4.5+)." % fbx_version]
        return PYTHON, notes
    if v6:
        if ok(CPP):
            if requested == "PYTHON":
                notes.append("FBX %d (pre-2011) is not readable by the Python importer; using the C++ importer." % fbx_version)
            return CPP, notes
        return None, ["FBX %d files can only be read by Blender's C++ importer (Blender 4.5+). "
                      "Re-save the file as FBX 2011 or newer (e.g. with Autodesk FBX Converter)." % fbx_version]
    if not ok(PYTHON):
        if ok(CPP):
            notes.append("The Python FBX importer (io_scene_fbx) is disabled; using the C++ importer.")
            return CPP, notes
        return None, ["No FBX importer is enabled. Enable the 'FBX format' add-on in Preferences > Add-ons."]
    if requested == "AUTO" and anim_curves >= ANIM_HEAVY and ok(CPP) and not python_only_options(settings):
        notes.append("%d animation curves: using Blender's C++ importer (much faster on big animation sets)." % anim_curves)
        return CPP, notes
    return PYTHON, notes
