# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#every "blender changed this again" workaround lives here, 3.6 -> 5.2+
# rule: check what exists (bl_rna / hasattr), never trust version numbers alone

from __future__ import annotations

import os
import warnings

import bpy

VERSION = bpy.app.version


def at_least(*v):
    return VERSION >= tuple(v)


def _rna_has_function(cls, name):
    try:
        return name in cls.bl_rna.functions
    except Exception:  # noqa: BLE001
        return False


def _rna_has_prop(cls, name):
    try:
        return name in cls.bl_rna.properties
    except Exception:  # noqa: BLE001
        return False


HAS_FILE_HANDLER = hasattr(bpy.types, "FileHandler")
HAS_LAYOUT_PANEL = _rna_has_function(bpy.types.UILayout, "panel")
HAS_LAYOUT_PROGRESS = _rna_has_function(bpy.types.UILayout, "progress")
HAS_EXTENSIONS = hasattr(bpy.utils, "extension_path_user")
HAS_NODE_INTERFACE = _rna_has_prop(bpy.types.NodeTree, "interface")
HAS_RENDER_METHOD = _rna_has_prop(bpy.types.Material, "surface_render_method")
HAS_SLOTTED_ACTIONS = _rna_has_prop(bpy.types.Action, "slots")


#principled bsdf got its sockets renamed in 4.0, keep a list of candidates per thing
PRINCIPLED = {
    "base_color": ("Base Color",),
    "metallic": ("Metallic",),
    "roughness": ("Roughness",),
    "alpha": ("Alpha",),
    "normal": ("Normal",),
    "emission_color": ("Emission Color", "Emission"),
    "emission_strength": ("Emission Strength",),
    "specular": ("Specular IOR Level", "Specular"),
    "ior": ("IOR",),
}


def socket(node, key, output=False):
    #first socket matching one of the candidate names (or the key itself)
    socks = node.outputs if output else node.inputs
    for name in PRINCIPLED.get(key, (key,)):
        s = socks.get(name)
        if s is not None:
            return s
    return None


def socket_by_id(node, identifier, output=False):
    for s in (node.outputs if output else node.inputs):
        if s.identifier == identifier:
            return s
    return None


def ensure_node_tree(mat):
    #5.x deprecates use_nodes (materials always have trees), older ones need it switched on
    if mat.node_tree is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            mat.use_nodes = True
    elif VERSION < (5, 0, 0) and not mat.use_nodes:
        mat.use_nodes = True
    return mat.node_tree


_CS_CACHE = {}


def colorspace(want):
    #map "sRGB" / "Non-Color" / "Linear" onto whatever the current ocio config calls them
    if want in _CS_CACHE:
        return _CS_CACHE[want]
    names = set()
    try:
        img = next(iter(bpy.data.images), None)
        tmp = None
        if img is None:
            tmp = img = bpy.data.images.new("__hjt_cs_probe", 1, 1)
        names = {i.identifier for i in img.colorspace_settings.bl_rna.properties["name"].enum_items}
        if tmp is not None:
            bpy.data.images.remove(tmp)
    except Exception:  # noqa: BLE001
        pass
    table = {
        "sRGB": ("sRGB", "sRGB EOTF", "Utility - sRGB - Texture"),
        "Non-Color": ("Non-Color", "Raw", "Generic Data", "Utility - Raw"),
        "Linear": ("Linear Rec.709", "Linear", "Linear BT.709", "scene_linear", "Utility - Linear - sRGB"),
    }
    result = want
    for cand in table.get(want, (want,)):
        if not names or cand in names:
            result = cand
            break
    _CS_CACHE[want] = result
    return result


def set_blend_mode(mat, mode, threshold=0.5):
    #mode: OPAQUE CLIP HASHED BLEND. eevee legacy n eevee next want totally diff props, fun
    if HAS_RENDER_METHOD:
        mat.surface_render_method = "BLENDED" if mode == "BLEND" else "DITHERED"
    if _rna_has_prop(bpy.types.Material, "blend_method"):
        try:
            mat.blend_method = {"OPAQUE": "OPAQUE", "CLIP": "CLIP", "HASHED": "HASHED", "BLEND": "BLEND"}[mode]
        except (TypeError, KeyError):
            pass
    if _rna_has_prop(bpy.types.Material, "shadow_method"):
        try:
            mat.shadow_method = {"OPAQUE": "OPAQUE", "CLIP": "CLIP", "HASHED": "HASHED", "BLEND": "HASHED"}[mode]
        except (TypeError, KeyError):
            pass
    if _rna_has_prop(bpy.types.Material, "alpha_threshold"):
        mat.alpha_threshold = float(threshold)
    if mode == "BLEND":
        for attr in ("show_transparent_back", "use_transparency_overlap"):
            if _rna_has_prop(bpy.types.Material, attr):
                setattr(mat, attr, False)


def needs_clip_nodes():
    #eevee next (4.2+) dropped the material alpha clip setting, clipping has to happen in the node tree
    return HAS_RENDER_METHOD


def new_group_socket(group, in_out, name, socket_type, default=None):
    if HAS_NODE_INTERFACE:
        s = group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    else:  # 3.6
        coll = group.inputs if in_out == "INPUT" else group.outputs
        s = coll.new(socket_type, name)
    if default is not None and hasattr(s, "default_value"):
        try:
            s.default_value = default
        except (TypeError, ValueError):
            pass
    return s


def user_dir(package, sub):
    #per user folder for profiles, history, logs
    path = None
    if HAS_EXTENSIONS and package.startswith("bl_ext."):
        try:
            path = bpy.utils.extension_path_user(package, path=sub, create=True)
        except Exception:  # noqa: BLE001
            path = None
    if not path:
        base = bpy.utils.user_resource("CONFIG", path="hjt_fbx_plus", create=True)
        path = os.path.join(base, sub)
        os.makedirs(path, exist_ok=True)
    return path


def action_fcurves(action):
    #4.4 slotted actions, 5.0 removed action.fcurves. iterate whatever this version has
    if _rna_has_prop(bpy.types.Action, "fcurves"):
        try:
            return list(action.fcurves)
        except Exception:  # noqa: BLE001
            pass
    out = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for bag in getattr(strip, "channelbags", ()):
                out.extend(bag.fcurves)
    return out


def find_layer_collection(layer_coll, coll):
    if layer_coll.collection == coll:
        return layer_coll
    for child in layer_coll.children:
        found = find_layer_collection(child, coll)
        if found is not None:
            return found
    return None


def set_active_collection(context, coll):
    vl = context.view_layer
    lc = find_layer_collection(vl.layer_collection, coll)
    if lc is not None:
        vl.active_layer_collection = lc
        return True
    return False


def ensure_object_mode(context):
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is not None and obj.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def tag_redraw_all(area_types=("VIEW_3D", "PROPERTIES", "FILE_BROWSER")):
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for win in wm.windows:
        scr = win.screen
        if scr is None:
            continue
        for area in scr.areas:
            if area.type in area_types:
                area.tag_redraw()


def draw_progress(layout, factor, text):
    if HAS_LAYOUT_PROGRESS:
        layout.progress(factor=factor, type="BAR", text=text)
    else:
        layout.label(text="%s (%d%%)" % (text, int(factor * 100)))
