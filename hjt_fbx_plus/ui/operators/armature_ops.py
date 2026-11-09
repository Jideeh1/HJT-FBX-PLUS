# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#fix chains on rigs that r already in the scene. imported last week w the old version? just select n click

from __future__ import annotations

import bpy
from bpy.props import BoolProperty

from ...blender import chains


def _target_armatures(context):
    out = []
    for ob in context.selected_objects or ([context.active_object] if context.active_object else []):
        arm = ob if ob.type == "ARMATURE" else ob.find_armature() if ob.type == "MESH" else None
        if arm is not None and arm not in out:
            out.append(arm)
    return out


class HJTFBX_OT_align_chains(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.align_chains"
    bl_label = "Align Bone Chains"
    bl_description = ("Point every bone of the selected armatures at the next bone of its chain. "
                      "Rest mesh, poses and animation stay exactly where they were")
    bl_options = {"REGISTER", "UNDO"}

    compensate: BoolProperty(name="Keep Animation", default=True,
                             description="Re-express pose and animation keys in the new bone axes so nothing moves")

    @classmethod
    def poll(cls, context):
        if context.mode not in ("OBJECT", "POSE"):
            cls.poll_message_set("Switch to Object or Pose mode")
            return False
        if not _target_armatures(context):
            cls.poll_message_set("Select an armature (or a mesh skinned to one)")
            return False
        return True

    def execute(self, context):
        arms = _target_armatures(context)
        total_a = total_s = total_k = 0
        for arm in arms:
            meshes = [o for o in context.view_layer.objects if o.type == "MESH" and o.find_armature() == arm]
            r = chains.align_chains(arm, meshes, compensate=self.compensate)
            total_a += r["aligned"]
            total_s += r["straight"]
            total_k += r["keys"]
        msg = "Aligned %d bone%s on %d armature%s (%d forks/leaves kept straight)" % (
            total_a, "" if total_a == 1 else "s", len(arms), "" if len(arms) == 1 else "s", total_s)
        if total_k:
            msg += ", %d keys re-expressed" % total_k
        self.report({"INFO"}, msg)
        return {"FINISHED"}


CLASSES = (HJTFBX_OT_align_chains,)
