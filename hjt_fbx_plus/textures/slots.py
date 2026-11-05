# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#texture slot vocabulary + the default "what is this file" rules
# profiles can add their own rules in front of these (or replace them)

from __future__ import annotations

import re

#slot -> how to treat it. aux = load it into the material but dont wire it into the shader
# (data for the original game shader we cant reproduce, so its there for the user, labeled)
SLOTS = {
    "base_color": {"label": "Base Color", "colorspace": "sRGB", "aux": False},
    "normal": {"label": "Normal", "colorspace": "Non-Color", "aux": False},
    "metallic": {"label": "Metallic", "colorspace": "Non-Color", "aux": False},
    "roughness": {"label": "Roughness", "colorspace": "Non-Color", "aux": False},
    "smoothness": {"label": "Smoothness", "colorspace": "Non-Color", "aux": False},
    "specular": {"label": "Specular", "colorspace": "Non-Color", "aux": False},
    "ao": {"label": "Ambient Occlusion", "colorspace": "Non-Color", "aux": False},
    "emission": {"label": "Emission", "colorspace": "sRGB", "aux": False},
    "emission_mask": {"label": "Emission Mask", "colorspace": "Non-Color", "aux": False},
    "opacity": {"label": "Opacity", "colorspace": "Non-Color", "aux": False},
    "height": {"label": "Height", "colorspace": "Non-Color", "aux": True},
    #packed ones, channel layout in PACKED below
    "orm": {"label": "ORM (AO/Rough/Metal)", "colorspace": "Non-Color", "aux": False},
    "mra": {"label": "MRA (Metal/Rough/AO)", "colorspace": "Non-Color", "aux": False},
    "rma": {"label": "RMA (Rough/Metal/AO)", "colorspace": "Non-Color", "aux": False},
    "metallic_smoothness": {"label": "Metallic + Smoothness (A)", "colorspace": "Non-Color", "aux": False},
    "hdrp_mask": {"label": "HDRP Mask (M/AO/D/S)", "colorspace": "Non-Color", "aux": False},
    #game shader data, kept as labeled aux textures
    "mask": {"label": "Mask", "colorspace": "Non-Color", "aux": True},
    "lightmap": {"label": "Light Map (ILM)", "colorspace": "Non-Color", "aux": True},
    "ramp": {"label": "Shadow Ramp", "colorspace": "sRGB", "aux": True},
    "face_map": {"label": "Face Shadow / SDF", "colorspace": "Non-Color", "aux": True},
    "matcap": {"label": "MatCap", "colorspace": "sRGB", "aux": True},
    "detail": {"label": "Detail", "colorspace": "Non-Color", "aux": True},
    "env": {"label": "Environment / Reflection", "colorspace": "sRGB", "aux": True},
    "misc": {"label": "Misc Data", "colorspace": "Non-Color", "aux": True},
    "stocking": {"label": "Stockings", "colorspace": "Non-Color", "aux": True},
    "expression": {"label": "Expression Map", "colorspace": "Non-Color", "aux": True},
    "aux": {"label": "Auxiliary", "colorspace": "Non-Color", "aux": True},
}

PACKED = {
    "orm": {"ao": "R", "roughness": "G", "metallic": "B"},
    "mra": {"metallic": "R", "roughness": "G", "ao": "B"},
    "rma": {"roughness": "R", "metallic": "G", "ao": "B"},
    "metallic_smoothness": {"metallic": "R", "smoothness": "A"},
    "hdrp_mask": {"metallic": "R", "ao": "G", "smoothness": "A"},
}

#end-of-name separators: _ - . space or a camelCase-ish boundary we dont try to guess
_SEP = r"(?:^|[_\-. ])"

#ordered, first match wins. regex runs on the LOWERCASE stem n must match at the end
DEFAULT_SLOT_RULES = [
    ("orm", _SEP + r"(?:orm|arm|occlusionroughnessmetallic)$"),
    ("mra", _SEP + r"(?:mra|mrao)$"),
    ("rma", _SEP + r"rma$"),
    ("metallic_smoothness", _SEP + r"(?:metallicgloss(?:map)?|metallic_?smoothness|ms)$"),
    ("normal", _SEP + r"(?:normal|normals|normalmap|normal_map|nrm|nor|norm|nml|nm|n|bump|bumpmap|bump_map|ddn)$"),
    ("base_color", _SEP + r"(?:diffuse|diff|dif|albedo|alb|basecolou?r|base_colou?r|colou?r|col|maintex|main_tex|bc|d|c|tex_diffuse)$"),
    ("roughness", _SEP + r"(?:roughness|rough|rgh|r)$"),
    ("smoothness", _SEP + r"(?:smoothness|smooth|gloss|glossiness|gls)$"),
    ("metallic", _SEP + r"(?:metallic|metalness|metal|met|mtl)$"),
    ("specular", _SEP + r"(?:specular|spec|spc|sp|s)$"),
    ("ao", _SEP + r"(?:ao|ambientocclusion|ambient_occlusion|occlusion|occ|o)$"),
    ("emission", _SEP + r"(?:emission|emissive|emit|glow|illum|selfillum|self_illum|e|em|luminance|lum)$"),
    ("opacity", _SEP + r"(?:opacity|alpha|transparency|transparent|trans|cutout|clip|a)$"),
    ("height", _SEP + r"(?:height|heightmap|disp|displacement|parallax|h|hgt)$"),
    ("lightmap", _SEP + r"(?:lightmap|light_map|ilm|lm)$"),
    ("ramp", r"(?:ramp|shadow_ramp|ramp_map)$"),
    ("face_map", _SEP + r"(?:facemap|face_map|sdf|faceshadow|face_shadow)$"),
    ("matcap", _SEP + r"(?:matcap|mat_cap|sphere)$"),
    ("mask", _SEP + r"(?:mask|msk|m|mk|id)$"),
    ("misc", _SEP + r"misc$"),
    ("detail", _SEP + r"(?:detail|dtl)$"),
]

#unity material property -> slot. covers built-in, urp, hdrp n the usual custom shader names
DEFAULT_UNITY_SLOTS = {
    "_MainTex": "base_color", "_BaseMap": "base_color", "_BaseColorMap": "base_color", "_BaseTex": "base_color",
    "_Albedo": "base_color", "_AlbedoMap": "base_color", "_DiffuseMap": "base_color", "_ColorMap": "base_color",
    "_MainTexture": "base_color", "_Diffuse": "base_color", "_BaseColorTexture": "base_color",
    "_BumpMap": "normal", "_NormalMap": "normal", "_Normal": "normal", "_NormalTex": "normal", "_BumpTex": "normal",
    "_MetallicGlossMap": "metallic_smoothness", "_MetallicMap": "metallic", "_RoughnessMap": "roughness",
    "_SpecGlossMap": "specular", "_SpecularMap": "specular", "_OcclusionMap": "ao", "_AOMap": "ao",
    "_EmissionMap": "emission", "_EmissiveMap": "emission", "_EmissiveColorMap": "emission", "_GlowTex": "emission",
    "_ParallaxMap": "height", "_HeightMap": "height", "_MaskMap": "hdrp_mask",
    "_DetailAlbedoMap": "detail", "_DetailNormalMap": "detail", "_DetailMask": "detail", "_DetailTex": "detail",
    "_LightMap": "lightmap", "_LightMapTex": "lightmap", "_ilmTex": "lightmap", "_ILMTex": "lightmap",
    "_RampTex": "ramp", "_RampMap": "ramp", "_ShadowRamp": "ramp", "_PackedShadowRampTex": "ramp",
    "_MatCap": "matcap", "_MatCapTex": "matcap", "_MatcapTex": "matcap", "_MTMap": "matcap",
    "_SDF": "face_map", "_FaceMap": "face_map", "_FaceMapTex": "face_map", "_FaceShadowTex": "face_map", "_SDFTex": "face_map",
    "_Mask": "mask", "_MaskTex": "mask", "_MiscMap": "misc", "_OtherDataTex": "mask", "_OtherDataTex2": "mask",
    "_CustomEnvironmentReflection": "env", "_EnvMap": "env", "_Cube": "env", "_ReflectionTex": "env",
    "_StockingsMap": "stocking", "_ExpressionMap": "expression",
}

_compiled_cache = {}


def compile_rules(rules):
    #rules = [(slot, pattern)] or [{"slot":..., "pattern":...}] -> [(slot, compiled)]
    key = id(rules)
    hit = _compiled_cache.get(key)
    if hit is not None and hit[0] is rules:
        return hit[1]
    out = []
    for r in rules:
        if isinstance(r, dict):
            slot, pat = r.get("slot"), r.get("pattern")
        else:
            slot, pat = r
        if not slot or not pat or slot not in SLOTS:
            continue
        try:
            out.append((slot, re.compile(pat, re.IGNORECASE)))
        except re.error:
            continue  #bad user regex, skip it instead of exploding the whole import
    _compiled_cache[key] = (rules, out)
    return out


def classify_name(stem, rules):
    #-> (slot, base_name) or (None, stem). base_name = stem w the slot suffix cut off
    low = stem.lower()
    for slot, rx in rules:
        m = rx.search(low)
        if m:
            base = low[:m.start()].rstrip("_-. ")
            return slot, base
    return None, low


def slot_info(slot):
    return SLOTS.get(slot) or SLOTS["aux"]
