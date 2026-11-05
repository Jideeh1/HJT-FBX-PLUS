# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#material tools for stuff thats already imported:
#  rebuild   switch pbr / toon / unlit / template w/o reimporting (uses the tagged image nodes)
#  find missing  relink images whose files moved
#  reassign  put a different file into a slot

from __future__ import annotations

import json
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ...blender import compat, materials
from ...blender.materials import META_PROP
from ...core.options import OPTIONS
from ...textures.discovery import MaterialPlan
from ...textures.slots import SLOTS
from ..props import SLOT_ITEMS

TEMPLATE_ITEMS = [i for i in OPTIONS["material_mode"]["items"] if i[0] not in ("PROFILE", "KEEP")]


class _SlotRef:
    #looks enough like a discovery Assignment for the material builder
    def __init__(self, slot, unity="", uv="", scale=(1.0, 1.0), offset=(0.0, 0.0)):
        self.slot = slot
        self.unity_slot = unity
        self.uv_set = uv
        self.scale = tuple(scale)
        self.offset = tuple(offset)
        self.tex = None
        self.source = "rebuild"
        self.faithful = True
        self.alternatives = []


def _materials_of(context, only_active=False):
    mats = []
    if only_active:
        ob = context.active_object
        if ob is not None and ob.active_material is not None:
            mats.append(ob.active_material)
        return mats
    for ob in context.selected_objects:
        for sl in ob.material_slots:
            if sl.material is not None and sl.material not in mats:
                mats.append(sl.material)
    return mats


def collect_slots(mat):
    out = {}
    if mat.node_tree is None:
        return out
    for n in mat.node_tree.nodes:
        if n.type != "TEX_IMAGE" or n.image is None:
            continue
        slot = n.get("hjt_slot")
        if not slot:
            continue
        ref = _SlotRef(slot, n.get("hjt_unity", ""), n.get("hjt_uv", ""), n.get("hjt_scale", (1.0, 1.0)),
                       n.get("hjt_offset", (0.0, 0.0)))
        out[ref] = n.image
    return out


def _meta(mat):
    #COPY the idproperty group into a plain dict. holding the group itself n then replacing
    # mat[META_PROP] leaves a dangling pointer -> blender segfaults (found that out the fun way)
    m = mat.get(META_PROP)
    if m is None:
        return {}
    try:
        return m.to_dict()
    except AttributeError:
        return dict(m)


def rebuild(mat, template, extra=None):
    meta = _meta(mat)
    plan = MaterialPlan(mat.name, "")
    try:
        plan.params = json.loads(meta.get("params", "{}"))
        plan.fbx_props = json.loads(meta.get("fbx_props", "{}"))
        settings = json.loads(meta.get("settings", "{}"))
    except (ValueError, TypeError):
        plan.params, plan.fbx_props, settings = {}, {}, {}
    plan.alpha_from_base = bool(meta.get("alpha_from_base", False))
    plan.shader = meta.get("game_shader", "")
    plan.category = meta.get("category", "")
    images = collect_slots(mat)
    if extra:
        #replace / add slots
        for ref, img in extra.items():
            images = {r: i for r, i in images.items() if not (r.slot == ref.slot and r.unity_slot == ref.unity_slot)}
            images[ref] = img
    if not settings:
        from ...profiles.schema import DEFAULT_MATERIAL
        settings = dict(DEFAULT_MATERIAL)
    settings["template"] = template
    plan.settings = settings
    color_attr = ""
    for ob in bpy.data.objects:
        if ob.type == "MESH" and any(sl.material == mat for sl in ob.material_slots):
            attrs = getattr(ob.data, "color_attributes", None)
            if attrs is not None and len(attrs):
                color_attr = attrs[0].name
            break
    info = {}
    for img in images.values():
        info[img.name] = {"alpha": None, "format": ""}
    notes = []
    ctx = materials._Ctx(images, settings, plan, color_attr, info, meta.get("profile", ""), notes)
    materials.build(mat, plan, ctx)
    target = ctx.template_result or mat
    meta2 = materials.write_meta(target, plan, meta.get("profile", ""), template, notes)
    meta2["textures_faithful"] = meta.get("textures_faithful", 0)
    meta2["textures_inferred"] = meta.get("textures_inferred", 0)
    target[META_PROP] = meta2
    if ctx.template_result is not None:
        bpy.data.materials.remove(mat)
    return target


class HJTFBX_OT_rebuild_materials(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.rebuild_materials"
    bl_label = "Rebuild Materials"
    bl_description = "Rebuild the materials of the selected objects with another template (only materials made by HJT FBX+)"
    bl_options = {"REGISTER", "UNDO"}

    template: EnumProperty(name="Template", items=TEMPLATE_ITEMS, default="PBR")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        mats = [m for m in _materials_of(context) if m.get(META_PROP)]
        if not mats:
            self.report({"WARNING"}, "Select objects whose materials were imported by HJT FBX+")
            return {"CANCELLED"}
        if self.template == "TEMPLATE":
            name = context.scene.hjt_fbx_plus.template_material
            if not name or name not in bpy.data.materials:
                self.report({"ERROR"}, "Set a Template Material in Material Reconstruction first")
                return {"CANCELLED"}
        n = 0
        for m in mats:
            try:
                rebuild(m, self.template)
                n += 1
            except Exception as exc:  # noqa: BLE001
                self.report({"WARNING"}, "%s: %s" % (m.name, exc))
        self.report({"INFO"}, "Rebuilt %d materials" % n)
        return {"FINISHED"}


class HJTFBX_OT_find_missing(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.find_missing"
    bl_label = "Find Missing Textures"
    bl_description = "Search a folder (recursively) for image files that are missing and relink them by file name"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")
    all_images: BoolProperty(name="All Images in File", default=False,
                             description="Check every image in the file instead of only the selected objects")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.directory or not os.path.isdir(self.directory):
            return {"CANCELLED"}
        if self.all_images:
            imgs = list(bpy.data.images)
        else:
            imgs = []
            for m in _materials_of(context):
                if m.node_tree is not None:
                    imgs += [n.image for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None]
        missing = [i for i in dict.fromkeys(imgs) if i.source == "FILE" and i.packed_file is None
                   and not os.path.exists(bpy.path.abspath(i.filepath))]
        if not missing:
            self.report({"INFO"}, "No missing images")
            return {"FINISHED"}
        index = {}
        for root, _dirs, files in os.walk(self.directory):
            for f in files:
                index.setdefault(f.lower(), os.path.join(root, f))
        fixed = 0
        for img in missing:
            name = bpy.path.basename(img.filepath).lower() or img.name.lower()
            hit = index.get(name)
            if hit is None:
                stem = os.path.splitext(name)[0]
                hit = next((p for k, p in index.items() if os.path.splitext(k)[0] == stem), None)
            if hit is not None:
                img.filepath = hit
                img.reload()
                fixed += 1
        self.report({"INFO"}, "Relinked %d of %d missing images" % (fixed, len(missing)))
        return {"FINISHED"}


class HJTFBX_OT_reassign_texture(bpy.types.Operator, ImportHelper):
    bl_idname = "hjt_fbx_plus.reassign_texture"
    bl_label = "Assign Texture"
    bl_description = "Put an image file into a texture slot of the active material (made by HJT FBX+) and rebuild it"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.png;*.tga;*.jpg;*.jpeg;*.dds;*.tif;*.tiff;*.bmp;*.exr;*.webp;*.psd", options={"HIDDEN"})
    slot: EnumProperty(name="Slot", items=SLOT_ITEMS, default="base_color")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.active_material is not None

    def draw(self, context):
        self.layout.prop(self, "slot")

    def execute(self, context):
        mat = context.active_object.active_material
        try:
            img = bpy.data.images.load(self.filepath, check_existing=True)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        img.colorspace_settings.name = compat.colorspace(SLOTS[self.slot]["colorspace"])
        if self.slot != "base_color":
            img.alpha_mode = "CHANNEL_PACKED"
        meta = _meta(mat)
        if not meta:
            #not ours: never rebuild somebody elses material, it would throw their node setup away
            self.report({"ERROR"}, "'%s' was not created by HJT FBX+; assign the image in the Shader Editor instead" % mat.name)
            return {"CANCELLED"}
        template = meta.get("template", "PBR") if meta.get("template") in ("PBR", "TOON", "FLAT", "TEMPLATE") else "PBR"
        rebuild(mat, template, {_SlotRef(self.slot): img})
        self.report({"INFO"}, "Assigned %s to %s" % (img.name, SLOTS[self.slot]["label"]))
        return {"FINISHED"}


CLASSES = (HJTFBX_OT_rebuild_materials, HJTFBX_OT_find_missing, HJTFBX_OT_reassign_texture)
