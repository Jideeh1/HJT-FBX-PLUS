# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#rebuilds materials from a MaterialPlan
#  PBR      principled bsdf w every texture wired where it belongs
#  TOON     simple 2 tone toon for eevee (shader to rgb) + a principled fallback output for cycles
#  FLAT     unlit, shows the texture colors as is
#  KEEP     leave the importers material alone, just repair missing images
#  TEMPLATE copy a material the user made (reference material) n drop our textures into its image nodes
#
#honesty rule: textures from fbx links / unity json r "faithful", game shaders we cant reproduce r
# "approximation". thats written onto the material (hjt_fbx prop) n into the report, no pretending

from __future__ import annotations

import json

import bpy

from . import compat
from .nodes import Tree, normal_rg_group
from ..textures.slots import PACKED, SLOTS

META_PROP = "hjt_fbx"

X_TEX = -900
X_MID = -450
X_BSDF = 0
X_OUT = 400


def _rgba(c, alpha=1.0):
    c = list(c or (1.0, 1.0, 1.0))
    while len(c) < 3:
        c.append(1.0)
    return (float(c[0]), float(c[1]), float(c[2]), float(c[3]) if len(c) > 3 else alpha)


def _fbx_diffuse(plan):
    p = plan.fbx_props or {}
    col = p.get("DiffuseColor") or p.get("Diffuse")
    if not isinstance(col, list):
        return None
    fac = p.get("DiffuseFactor", 1.0)
    try:
        return tuple(min(1.0, max(0.0, float(c) * float(fac))) for c in col[:3])
    except (TypeError, ValueError):
        return None


def _fbx_opacity(plan):
    p = plan.fbx_props or {}
    op = p.get("Opacity")
    if isinstance(op, float):
        return max(0.0, min(1.0, op))
    tf = p.get("TransparencyFactor")
    tc = p.get("TransparentColor")
    if isinstance(tf, float) and isinstance(tc, list) and tf > 0.0:
        avg = sum(float(x) for x in tc[:3]) / 3.0
        if avg > 0.0:
            return max(0.0, min(1.0, 1.0 - tf * avg))
    return 1.0


class _Ctx:
    #what the builder needs besides the plan
    def __init__(self, images, settings, plan, color_attr="", image_info=None, profile_id="", notes=None):
        self.images = images            #assignment -> bpy image (only loaded ones)
        self.settings = settings
        self.plan = plan
        self.color_attr = color_attr
        self.image_info = image_info or {}  #image name -> header probe dict (alpha, format, size)
        self.profile_id = profile_id
        self.notes = notes if notes is not None else []
        self.template_result = None    #set when TEMPLATE swapped the material for a copy


def decide_alpha(ctx, base_img, opacity_img):
    s = ctx.settings
    params = ctx.plan.params if s.get("use_material_params", True) else {}
    mode = s.get("alpha_mode", "AUTO")
    usage = s.get("base_alpha_usage", "AUTO")
    pblend = params.get("blend")
    base_has_alpha = (ctx.image_info.get(base_img.name) or {}).get("alpha") if base_img is not None else None
    fbx_op = _fbx_opacity(ctx.plan)
    if mode == "AUTO":
        if pblend in ("BLEND", "ADD", "MULTIPLY"):
            mode = "BLEND"
        elif pblend == "CLIP":
            mode = "CLIP"
        elif pblend == "OPAQUE":
            mode = "OPAQUE"
        elif opacity_img is not None:
            mode = "HASHED"
        elif (ctx.plan.alpha_from_base or usage == "TRANSPARENCY") and base_has_alpha is not False:
            mode = "HASHED"
        elif fbx_op < 0.999:
            mode = "BLEND"
        else:
            mode = "OPAQUE"
    use_base_alpha = (usage == "TRANSPARENCY" or ctx.plan.alpha_from_base or
                      (usage == "AUTO" and mode in ("CLIP", "HASHED", "BLEND") and opacity_img is None and base_has_alpha))
    const = fbx_op
    tint = params.get("tint")
    if tint is not None and mode == "BLEND":
        const *= float(tint[3])
    return mode, use_base_alpha, const


def build(mat, plan, ctx):
    template = ctx.settings.get("template", "PBR")
    if template == "KEEP":
        return _keep(mat, plan, ctx)
    if template == "TEMPLATE":
        return _from_template(mat, plan, ctx)
    t = Tree(mat, clear=True)
    by_slot = {}
    aux = []
    for a, img in ctx.images.items():
        if SLOTS.get(a.slot, {}).get("aux") or a.slot in by_slot:
            aux.append((a, img))
        else:
            by_slot[a.slot] = (a, img)
    y = 300
    image_nodes = {}

    def img_node(slot, label=None):
        nonlocal y
        if slot not in by_slot:
            return None
        if slot in image_nodes:
            return image_nodes[slot]
        a, img = by_slot[slot]
        uv = t.uv_input(X_TEX - 120, y, a.uv_set, a.scale, a.offset)
        n = t.image(img, X_TEX, y, label or SLOTS[slot]["label"], uv)
        _tag(n, a)
        image_nodes[slot] = n
        y -= 290
        return n

    base_node = img_node("base_color")
    opacity_node = img_node("opacity")
    mode, use_base_alpha, alpha_const = decide_alpha(ctx, base_node.image if base_node else None,
                                                     opacity_node.image if opacity_node else None)

    #base color signal: texture (or fbx color) x tint x vertex color x ao
    s = ctx.settings
    params = ctx.plan.params if s.get("use_material_params", True) else {}
    if base_node is not None:
        base = base_node.outputs["Color"]
    else:
        base = None
    tint = params.get("tint")
    const_color = _rgba(_fbx_diffuse(plan) or (0.8, 0.8, 0.8))
    if tint is not None and (tint[0], tint[1], tint[2]) != (1.0, 1.0, 1.0):
        if base is not None:
            base = t.mix_color("MULTIPLY", base, _rgba(tint), 1.0, X_MID, 400, "Tint (from game material)")
        else:
            const_color = _rgba(tint)
    vc = s.get("vertex_color", "NONE")
    if ctx.color_attr and (vc == "MULTIPLY" or (vc == "AUTO" and base_node is None)):
        vcn = t.node("ShaderNodeVertexColor", X_TEX, 700, "Vertex Color")
        vcn.layer_name = ctx.color_attr
        base = t.mix_color("MULTIPLY", base if base is not None else const_color, vcn.outputs["Color"], 1.0, X_MID, 600, "x Vertex Color")
    ao_src = _channel(t, by_slot, "ao", img_node)
    if ao_src is not None and s.get("ao_mode") == "MULTIPLY" and base is not None:
        base = t.mix_color("MULTIPLY", base, ao_src, 1.0, X_MID + 150, 400, "x AO")

    #alpha
    alpha = None
    if opacity_node is not None:
        alpha = opacity_node.outputs["Color"]
    elif use_base_alpha and base_node is not None:
        alpha = base_node.outputs["Alpha"]
    if alpha is not None and alpha_const < 0.999:
        alpha = t.math("MULTIPLY", alpha, alpha_const, X_MID, -50, label="Opacity")
    elif alpha is None and alpha_const < 0.999:
        alpha = alpha_const
    if alpha is not None and mode == "CLIP" and compat.needs_clip_nodes() and not isinstance(alpha, float):
        alpha = t.math("GREATER_THAN", alpha, float(params.get("alpha_threshold", s.get("alpha_threshold", 0.5))),
                       X_MID + 150, -50, label="Alpha Clip")

    #normal
    normal = None
    nnode = img_node("normal")
    if nnode is not None:
        col = nnode.outputs["Color"]
        fmt = s.get("normal_format", "AUTO")
        if fmt == "AUTO":
            #only thing we can know for sure from the header: bc5 dds = 2 channel normal
            fmt = "RG" if "BC5" in str((ctx.image_info.get(nnode.image.name) or {}).get("format", "")) else "RGB"
        if fmt in ("RG", "AG") or s.get("normal_convention") == "DIRECTX":
            sep = t.separate(col, X_TEX + 300, nnode.location.y - 40)
            xch = sep.outputs[0] if fmt != "AG" else nnode.outputs["Alpha"]
            ych = sep.outputs[1]
            if s.get("normal_convention") == "DIRECTX":
                ych = t.math("SUBTRACT", 1.0, ych, X_TEX + 450, nnode.location.y - 80, label="Flip Green (DirectX)")
            if fmt in ("RG", "AG"):
                grp = t.node("ShaderNodeGroup", X_TEX + 600, nnode.location.y, "Rebuild Normal Z")
                grp.node_tree = normal_rg_group()
                t.link(xch, grp.inputs[0])
                t.link(ych, grp.inputs[1])
                col = grp.outputs[0]
            else:
                col = t.combine(xch, ych, sep.outputs[2], X_TEX + 600, nnode.location.y)
        nm = t.node("ShaderNodeNormalMap", X_MID + 150, nnode.location.y, "Normal Map")
        a = by_slot["normal"][0]
        if a.uv_set:
            nm.uv_map = a.uv_set
        strength = float(s.get("normal_strength", 1.0)) * float(params.get("normal_strength", 1.0))
        nm.inputs["Strength"].default_value = strength
        t.link(col, nm.inputs["Color"])
        normal = nm.outputs["Normal"]

    #metal / rough / spec / emission
    metallic = _channel(t, by_slot, "metallic", img_node)
    rough = _channel(t, by_slot, "roughness", img_node)
    smooth = _channel(t, by_slot, "smoothness", img_node)
    if rough is None and smooth is not None:
        rough = t.math("SUBTRACT", 1.0, smooth, X_MID, -400, label="Smoothness -> Roughness")
    spec = _channel(t, by_slot, "specular", img_node)
    emis_node = img_node("emission")
    emission = emis_node.outputs["Color"] if emis_node is not None else None
    if emission is None and s.get("base_alpha_usage") == "EMISSION_MASK" and base_node is not None:
        #base color alpha = where it glows
        emission = t.mix_color("MIX", (0.0, 0.0, 0.0, 1.0), base_node.outputs["Color"], base_node.outputs["Alpha"],
                               X_MID, -600, "Emission (base alpha mask)")

    def principled(x, yy):
        b = t.node("ShaderNodeBsdfPrincipled", x, yy, "Principled BSDF")
        t.set_input(b, "base_color", base if base is not None else const_color)
        if metallic is not None:
            t.set_input(b, "metallic", metallic)
        elif s.get("metallic") is not None or "metallic" in params:
            t.set_input(b, "metallic", float(params.get("metallic", s.get("metallic") or 0.0)))
        if rough is not None:
            t.set_input(b, "roughness", rough)
        elif s.get("roughness") is not None or "roughness" in params:
            t.set_input(b, "roughness", float(params.get("roughness", s.get("roughness") if s.get("roughness") is not None else 0.5)))
        if spec is not None:
            t.set_input(b, "specular", spec)
        elif s.get("specular") is not None:
            t.set_input(b, "specular", float(s["specular"]))
        if normal is not None:
            t.set_input(b, "normal", normal)
        if alpha is not None:
            t.set_input(b, "alpha", alpha)
        if emission is not None:
            t.set_input(b, "emission_color", emission)
            t.set_input(b, "emission_strength", float(s.get("emission_strength", 1.0)))
        return b

    outputs = []
    if template == "TOON":
        bsdf = principled(X_BSDF, -700)
        out_cy = t.node("ShaderNodeOutputMaterial", X_OUT, -700, "Cycles (Principled fallback)", target="CYCLES")
        t.link(bsdf.outputs[0], out_cy.inputs["Surface"])
        toon = _toon(t, base if base is not None else const_color, normal, ctx, params)
        surf = _with_alpha(t, toon, alpha, X_BSDF + 200, 200)
        out_ev = t.node("ShaderNodeOutputMaterial", X_OUT, 200, "EEVEE (toon approximation)", target="EEVEE")
        t.link(surf, out_ev.inputs["Surface"])
        outputs = [out_ev, out_cy]
        ctx.notes.append("Toon shading is an approximation of the game shader (EEVEE); Cycles uses the Principled fallback output.")
    elif template == "FLAT":
        em = t.node("ShaderNodeEmission", X_BSDF, 200, "Unlit (texture colors)")
        t.set_input(em, "Color", base if base is not None else const_color)
        em.inputs["Strength"].default_value = 1.0
        surf = _with_alpha(t, em.outputs[0], alpha, X_BSDF + 200, 200)
        out = t.node("ShaderNodeOutputMaterial", X_OUT, 200)
        t.link(surf, out.inputs["Surface"])
        outputs = [out]
        ctx.notes.append("Unlit shading: shows texture colors without lighting.")
    else:
        bsdf = principled(X_BSDF, 200)
        out = t.node("ShaderNodeOutputMaterial", X_OUT, 200)
        t.link(bsdf.outputs[0], out.inputs["Surface"])
        outputs = [out]
    for o in outputs:
        o.is_active_output = True

    #aux textures: loaded + labeled, not wired (game shader data)
    aux_nodes = []
    ay = y - 80
    for a, img in aux:
        label = SLOTS.get(a.slot, SLOTS["aux"])["label"] + (" (%s)" % a.unity_slot if a.unity_slot else "")
        n = t.image(img, X_TEX - 450, ay, label)
        _tag(n, a)
        aux_nodes.append(n)
        ay -= 270
    if aux_nodes:
        t.frame("Game shader data - loaded, not used by Blender shading", aux_nodes, (0.25, 0.22, 0.28))
    if base_node is not None:
        t.nodes.active = base_node  #solid view "texture" color uses the active image node

    #material settings
    compat.set_blend_mode(mat, "BLEND" if mode == "BLEND" else ("HASHED" if mode == "HASHED" else ("CLIP" if mode == "CLIP" else "OPAQUE")),
                          float(params.get("alpha_threshold", s.get("alpha_threshold", 0.5))))
    cull = s.get("backface_culling", "AUTO")
    if cull == "AUTO":
        pc = params.get("cull")
        mat.use_backface_culling = pc == "BACK"
    else:
        mat.use_backface_culling = cull == "ON"
    if base is None and base_node is None:
        mat.diffuse_color = const_color
    return mode


def _tag(node, a):
    #remember what this image node is so "Rebuild Materials" can redo the tree later
    try:
        node["hjt_slot"] = a.slot
        if a.unity_slot:
            node["hjt_unity"] = a.unity_slot
        if a.uv_set:
            node["hjt_uv"] = a.uv_set
        if tuple(a.scale) != (1.0, 1.0):
            node["hjt_scale"] = [float(x) for x in a.scale]
        if tuple(a.offset) != (0.0, 0.0):
            node["hjt_offset"] = [float(x) for x in a.offset]
    except (TypeError, AttributeError):
        pass


def _channel(t, by_slot, want, img_node):
    #socket for a scalar channel, from its own map or out of a packed one (orm mra ...)
    if want in by_slot:
        n = img_node(want)
        return n.outputs["Color"]
    for packed, layout in PACKED.items():
        if packed in by_slot and want in layout:
            n = img_node(packed)
            ch = layout[want]
            if ch == "A":
                n.image.alpha_mode = "CHANNEL_PACKED"
                return n.outputs["Alpha"]
            key = "__sep_" + packed
            sep = getattr(t, key, None)
            if sep is None:
                sep = t.separate(n.outputs["Color"], n.location.x + 300, n.location.y)
                setattr(t, key, sep)
            return sep.outputs["RGB".index(ch)]
    return None


def _with_alpha(t, shader_out, alpha, x, y):
    if alpha is None:
        return shader_out
    tr = t.node("ShaderNodeBsdfTransparent", x, y - 150, "Transparent")
    mix = t.node("ShaderNodeMixShader", x + 180, y, "Alpha")
    if isinstance(alpha, float):
        mix.inputs[0].default_value = alpha
    else:
        t.link(alpha, mix.inputs[0])
    t.link(tr.outputs[0], mix.inputs[1])
    t.link(shader_out, mix.inputs[2])
    return mix.outputs[0]


def _toon(t, base, normal, ctx, params):
    #2 tone cel shading: diffuse -> shader to rgb -> step w feather -> lerp(shadow, lit)
    s = ctx.settings
    tint = params.get("shadow_tint") or s.get("shadow_tint") or (0.78, 0.74, 0.84)
    thr = float(params.get("shadow_threshold", s.get("shadow_threshold", 0.5)))
    feather = max(0.001, float(params.get("shadow_feather", s.get("shadow_feather", 0.02))))
    diff = t.node("ShaderNodeBsdfDiffuse", X_MID - 200, 650, "Light")
    if normal is not None:
        t.link(normal, diff.inputs["Normal"])
    s2r = t.node("ShaderNodeShaderToRGB", X_MID, 650, "Shader to RGB")
    t.link(diff.outputs[0], s2r.inputs[0])
    ramp = t.node("ShaderNodeValToRGB", X_MID + 180, 650, "Shadow step (game values)")
    el = ramp.color_ramp.elements
    lo = max(0.0, min(1.0, thr - feather * 0.5))
    hi = max(lo + 0.0005, min(1.0, thr + feather * 0.5))
    el[0].position = lo
    el[0].color = (0.0, 0.0, 0.0, 1.0)
    el[1].position = hi
    el[1].color = (1.0, 1.0, 1.0, 1.0)
    t.link(s2r.outputs["Color"], ramp.inputs["Fac"])
    shadow = t.mix_color("MULTIPLY", base, _rgba(tint), 1.0, X_MID + 200, 400, "Shadow color (tint)")
    lit_mix = t.mix_color("MIX", shadow, base, 1.0, X_BSDF - 100, 450, "Lit / Shadow")
    t.link(ramp.outputs["Color"], compat.socket_by_id(lit_mix.node, "Factor_Float"))
    em = t.node("ShaderNodeEmission", X_BSDF + 50, 300, "Toon")
    t.link(lit_mix, em.inputs["Color"])
    return em.outputs[0]


def _keep(mat, plan, ctx):
    #importer material stays, broken image paths get pointed at what we found
    tree = mat.node_tree
    if tree is None:
        return "OPAQUE"
    found = {a.tex.name_l: img for a, img in ctx.images.items() if a.tex is not None}
    fixed = 0
    for n in tree.nodes:
        if n.type != "TEX_IMAGE" or n.image is None:
            continue
        img = n.image
        if img.packed_file is not None or img.has_data:
            continue
        name = bpy.path.basename(img.filepath).lower() or img.name.lower()
        repl = found.get(name)
        if repl is not None:
            n.image = repl
            fixed += 1
    if fixed:
        ctx.notes.append("Repaired %d missing image paths." % fixed)
    return getattr(mat, "blend_method", "OPAQUE")


def _from_template(mat, plan, ctx):
    #reference material: user built a material w image nodes labeled base_color / normal / _MainTex etc
    name = ctx.settings.get("template_material", "")
    tmpl = bpy.data.materials.get(name) if name else None
    if tmpl is None:
        ctx.notes.append("Template material '%s' not found; used PBR instead." % name)
        ctx.settings = dict(ctx.settings, template="PBR")
        return build(mat, plan, ctx)
    new = tmpl.copy()
    old_name = mat.name
    mat.name = old_name + "__hjt_replaced"
    new.name = old_name
    mat.user_remap(new)  #every slot that used the imported material now uses the template copy
    filled = 0
    keys = {}
    for a, img in ctx.images.items():
        keys.setdefault(a.slot.lower(), img)
        if a.unity_slot:
            keys.setdefault(a.unity_slot.lower(), img)
    for n in new.node_tree.nodes:
        if n.type != "TEX_IMAGE":
            continue
        for k in (n.label.lower(), n.name.lower()):
            if k in keys:
                n.image = keys[k]
                filled += 1
                break
    ctx.notes.append("Built from template material '%s' (%d image slots filled)." % (name, filled))
    ctx.template_result = new
    return getattr(new, "blend_method", "OPAQUE")


def write_meta(mat, plan, profile_id, template, notes):
    faithful = sum(1 for a in plan.assignments if a.faithful)
    inferred = len(plan.assignments) - faithful
    shading = {"PBR": "principled (approximation of the game shader)" if plan.shader else "principled",
               "TOON": "toon approximation", "FLAT": "unlit", "KEEP": "as imported", "TEMPLATE": "user template"}.get(template, template)
    def plain(v):
        if isinstance(v, (list, tuple)):
            return [plain(x) for x in v]
        return v
    fbx_keep = {k: plain(v) for k, v in (plan.fbx_props or {}).items()
                if k in ("DiffuseColor", "DiffuseFactor", "Opacity", "TransparencyFactor", "TransparentColor")}
    meta = {
        "profile": profile_id,
        "template": template,
        "shading": shading,
        "textures_faithful": faithful,
        "textures_inferred": inferred,
        "game_shader": plan.shader or "",
        "category": plan.category or "",
        "notes": " | ".join(dict.fromkeys(notes))[:1000],
        "params": json.dumps({k: plain(v) for k, v in (plan.params or {}).items()}),
        "fbx_props": json.dumps(fbx_keep),
        "settings": json.dumps({k: plain(v) for k, v in (plan.settings or {}).items() if k != "unity_slots"}),
        "alpha_from_base": bool(plan.alpha_from_base),
    }
    try:
        mat[META_PROP] = meta
    except (TypeError, ValueError):
        pass
    return meta
