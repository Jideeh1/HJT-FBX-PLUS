# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#builds a json friendly summary of an fbx doc for the preview panel
# no bpy, runs in background threads. big arrays only get decoded when
#the count cant be read straight from the array header


from __future__ import annotations

import math

from .elements import FbxArray, FbxBlob, FbxError, decode_name
from .document import FBX_KTIME, FbxDocument

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

BONE_SUBTYPES = frozenset((b"LimbNode", b"Root", b"Limb"))
MESH_SUBTYPES = frozenset((b"Mesh",))

#fbx material prop names -> generic slot hint. maya n max each invented their own names ofc
FBX_TEXTURE_SLOTS = {
    b"DiffuseColor": "base_color", b"Diffuse": "base_color", b"Maya|baseColor": "base_color",
    b"3dsMax|Parameters|base_color_map": "base_color", b"Maya|TEX_color_map": "base_color",
    b"NormalMap": "normal", b"Bump": "normal", b"Maya|normalCamera": "normal",
    b"3dsMax|Parameters|bump_map": "normal", b"Maya|TEX_normal_map": "normal",
    b"SpecularColor": "specular", b"SpecularFactor": "specular", b"Maya|specularColor": "specular",
    b"ShininessExponent": "glossiness", b"Shininess": "glossiness", b"ReflectionFactor": "metallic",
    b"Maya|metalness": "metallic", b"3dsMax|Parameters|metalness_map": "metallic",
    b"Maya|TEX_metallic_map": "metallic", b"Maya|specularRoughness": "roughness",
    b"3dsMax|Parameters|roughness_map": "roughness", b"Maya|TEX_roughness_map": "roughness",
    b"EmissiveColor": "emission", b"EmissiveFactor": "emission", b"Maya|emissionColor": "emission",
    b"3dsMax|Parameters|emit_color_map": "emission", b"Maya|TEX_emissive_map": "emission",
    b"TransparentColor": "opacity", b"TransparencyFactor": "opacity", b"Opacity": "opacity",
    b"Maya|opacity": "opacity", b"3dsMax|Parameters|cutout_map": "opacity",
    b"AmbientColor": "ao", b"AmbientFactor": "ao", b"Maya|TEX_ao_map": "ao",
    b"DisplacementColor": "height", b"VectorDisplacementColor": "height",
    b"ReflectionColor": "reflection",
}

_MATERIAL_SCALARS = (
    b"DiffuseColor", b"DiffuseFactor", b"Diffuse", b"AmbientColor", b"EmissiveColor",
    b"EmissiveFactor", b"SpecularColor", b"SpecularFactor", b"Shininess", b"ShininessExponent",
    b"TransparentColor", b"TransparencyFactor", b"Opacity", b"ReflectionFactor", b"BumpFactor",
    b"NormalMap", b"Emissive", b"Ambient", b"Specular",
)

MAX_NAMES = 4000  #cap on names we keep for detection n categorizing


def _issue(level, code, message, **extra):
    d = {"level": level, "code": code, "message": message}
    if extra:
        d.update(extra)
    return d


def _array_count(node, name):
    child = node.find(name) if node is not None else None
    if child is None or not child.props:
        return 0
    v = child.props[0]
    if isinstance(v, FbxArray):
        return v.count
    return len(child.props)


def _count_polygons(pvi_node):
    #polygon count = how many negative numbers r in PolygonVertexIndex (last index of each poly is stored as ~i lmao)
    if pvi_node is None or not pvi_node.props:
        return 0
    arr = pvi_node.props[0]
    if not isinstance(arr, FbxArray):
        return sum(1 for v in pvi_node.props if isinstance(v, int) and v < 0)
    if _np is not None:
        a = arr.numpy()
        n = int(_np.count_nonzero(a < 0))
        arr.release()
        return n
    vals = arr.values()
    n = sum(1 for v in vals if v < 0)
    arr.release()
    return n


def _layer_names(geom_node, layer_id):
    names = []
    for el in geom_node.find_all(layer_id):
        nm = el.value(b"Name", 0, b"")
        names.append(decode_name(nm) if nm else "")
    return names


def _matrix(node):
    if node is None or not node.props:
        return None
    v = node.props[0]
    if isinstance(v, FbxArray):
        vals = v.values()
        return tuple(vals) if len(vals) == 16 else None
    return None


def _matrices_close(a, b, tol=1e-4):
    scale = max(1.0, max(abs(x) for x in a))
    return all(abs(x - y) <= tol * scale for x, y in zip(a, b))


def texture_info(doc, tex_obj):
    #file refs + mapping info for one Texture
    node = tex_obj.node
    info = {
        "uid": _uid_str(tex_obj.uid),
        "name": tex_obj.name,
        "filename": decode_name(node.value(b"FileName", 0, b"") or b"").replace("\\", "/"),
        "relative": decode_name(node.value(b"RelativeFilename", 0, b"") or b"").replace("\\", "/"),
        "embedded": False,
        "embedded_size": 0,
        "video_uid": "",
    }
    props = tex_obj.props
    uvset = props.get(b"UVSet")
    if uvset is not None and uvset.value:
        info["uv_set"] = decode_name(uvset.value)
    for key, label in ((b"Translation", "translation"), (b"Rotation", "rotation"), (b"Scaling", "scaling")):
        p = props.get(key)
        if p is not None and isinstance(p.value, tuple):
            info[label] = [float(x) for x in p.value[:3]]
    for key, label in ((b"WrapModeU", "wrap_u"), (b"WrapModeV", "wrap_v")):
        p = props.get(key)
        if p is not None and isinstance(p.value, int):
            info[label] = int(p.value)
    uv_t = node.find(b"ModelUVTranslation")
    if uv_t is not None and len(uv_t.props) >= 2:
        info["uv_translation"] = [float(uv_t.props[0]), float(uv_t.props[1])]
    uv_s = node.find(b"ModelUVScaling")
    if uv_s is not None and len(uv_s.props) >= 2:
        info["uv_scaling"] = [float(uv_s.props[0]), float(uv_s.props[1])]
    alpha_src = node.value(b"Texture_Alpha_Source")
    if alpha_src:
        info["alpha_source"] = decode_name(alpha_src)
    for video in doc.children(tex_obj.uid, b"Video"):
        info["video_uid"] = _uid_str(video.uid)
        vnode = video.node
        if not info["filename"]:
            info["filename"] = decode_name(vnode.value(b"Filename", 0, b"") or vnode.value(b"FileName", 0, b"") or b"").replace("\\", "/")
        if not info["relative"]:
            info["relative"] = decode_name(vnode.value(b"RelativeFilename", 0, b"") or b"").replace("\\", "/")
        content = vnode.find(b"Content")
        if content is not None and content.props:
            blob = content.props[0]
            size = len(blob) if isinstance(blob, (bytes, FbxBlob)) else 0
            if size > 0:
                info["embedded"] = True
                info["embedded_size"] = size
        break
    return info


def _uid_str(uid):
    if isinstance(uid, bytes):
        return uid.decode("utf-8", "replace").replace("\x00\x01", "::")
    return str(uid)


def _material_props(mat):
    out = {}
    for key in _MATERIAL_SCALARS:
        p = mat.props.get(key)
        if p is None:
            continue
        v = p.value
        if isinstance(v, tuple):
            try:
                out[key.decode()] = [float(x) for x in v[:4]]
            except (TypeError, ValueError):
                continue
        elif isinstance(v, (int, float)):
            out[key.decode()] = float(v)
    sm = mat.node.value(b"ShadingModel")
    if sm:
        out["ShadingModel"] = decode_name(sm)
    #user (U) props, game exporters love stashing shader params here
    user = {}
    for name, p in mat.props.items():
        if not p.is_user:
            continue
        v = p.value
        key = decode_name(name)
        if isinstance(v, (int, float)):
            user[key] = v
        elif isinstance(v, bytes):
            user[key] = decode_name(v)
        elif isinstance(v, tuple) and all(isinstance(x, (int, float)) for x in v):
            user[key] = list(v)
    if user:
        out["user"] = user
    return out


def material_textures(doc, mat_obj):
    #textures hooked up to a material, w the slot prop + a hint
    out = []
    for link in doc.child_links(mat_obj.uid):
        child = doc.get(link.child)
        if child is None:
            continue
        prop = link.prop or b""
        if child.cls == b"Texture":
            textures = [child]
        elif child.cls == b"LayeredTexture":
            textures = [t for t in doc.children(child.uid, b"Texture")]
        else:
            continue
        for layer_index, tex in enumerate(textures):
            info = texture_info(doc, tex)
            info["property"] = decode_name(prop)
            info["slot_hint"] = FBX_TEXTURE_SLOTS.get(prop, "")
            if child.cls == b"LayeredTexture":
                info["layer"] = layer_index
            out.append(info)
    return out


def inspect_document(doc, deep=True, cancel=None):
    #summary dict for an FbxDocument. long function, sorry not sorry
    summary = {
        "format": doc.format,
        "version": doc.version,
        "header": doc.header_info(),
        "issues": [],
    }
    issues = summary["issues"]
    gs = doc.global_settings()
    summary["global_settings"] = {k: (v if isinstance(v, (int, float, str)) else (list(v) if isinstance(v, tuple) else decode_name(v)))
                                  for k, v in gs.items()}
    try:
        summary["fps"] = doc.frame_rate()
    except Exception:  # pragma: no cover
        summary["fps"] = 24.0

    if doc.format == "ascii":
        issues.append(_issue("info", "ascii", "ASCII FBX file: it is converted to binary for Blender's Python importer (the C++ importer reads it directly)."))
    if doc.is_v6:
        issues.append(_issue("warning", "fbx6", "FBX %d.%d file (pre-2011 format): only Blender's C++ FBX importer (Blender 4.5+) can read it." % (doc.version // 1000, (doc.version // 100) % 10)))
    elif doc.version > 7700:
        issues.append(_issue("warning", "version_new", "FBX version %d is newer than any version known to this add-on; import may fail." % doc.version))

    models = doc.objects_of(b"Model")
    counts = {
        "models": len(models), "meshes": 0, "bones": 0, "armatures": 0, "nulls": 0, "cameras": 0,
        "lights": 0, "lod_groups": 0, "materials": len(doc.objects_of(b"Material")),
        "textures": len(doc.objects_of(b"Texture")), "videos": len(doc.objects_of(b"Video")),
        "embedded_textures": 0, "skins": 0, "clusters": 0, "blend_shapes": 0, "shape_channels": 0,
        "shapes": 0, "anim_stacks": 0, "anim_layers": len(doc.objects_of(b"AnimationLayer")),
        "anim_curve_nodes": len(doc.objects_of(b"AnimationCurveNode")),
        "anim_curves": len(doc.objects_of(b"AnimationCurve")), "poses": len(doc.objects_of(b"Pose")),
        "nurbs": 0,
    }
    summary["counts"] = counts

    #skeleton
    bone_uids = set(m.uid for m in models if m.subtype in BONE_SUBTYPES)
    clusters = doc.objects_of(b"Deformer", b"Cluster")
    for cl in clusters:
        for bone in doc.children(cl.uid, b"Model"):
            bone_uids.add(bone.uid)
    counts["bones"] = len(bone_uids)
    roots = []
    for uid in bone_uids:
        parent = doc.model_parent(uid)
        if parent is None or parent.uid not in bone_uids:
            roots.append(uid)
    counts["armatures"] = len(set((doc.model_parent(u).uid if doc.model_parent(u) else 0) for u in roots)) if roots else 0
    summary["bone_names"] = [doc.objects[u].name for u in list(bone_uids)[:MAX_NAMES] if u in doc.objects]
    summary["skeleton_roots"] = [doc.objects[u].name for u in roots[:64] if u in doc.objects]

    # models
    names = []
    seen_names = {}
    negative_scale = 0
    for m in models:
        st = m.subtype
        if st in MESH_SUBTYPES and m.uid not in bone_uids:
            counts["meshes"] += 1
        elif st == b"Null" and m.uid not in bone_uids:
            counts["nulls"] += 1
        elif st == b"Camera":
            counts["cameras"] += 1
        elif st == b"Light":
            counts["lights"] += 1
        elif st == b"LodGroup":
            counts["lod_groups"] += 1
        elif st in (b"NurbsCurve", b"Nurb", b"NurbsSurface", b"Patch"):
            counts["nurbs"] += 1
        if m.uid not in bone_uids and len(names) < MAX_NAMES:
            names.append(m.name)
        seen_names[(m.name, m.uid in bone_uids)] = seen_names.get((m.name, m.uid in bone_uids), 0) + 1
        sc = m.prop(b"Lcl Scaling")
        if isinstance(sc, tuple) and any(isinstance(x, (int, float)) and x < 0 for x in sc[:3]):
            negative_scale += 1
    summary["object_names"] = names
    dups = sorted(n for (n, _b), c in seen_names.items() if c > 1)
    if dups:
        issues.append(_issue("info", "duplicate_names", "%d object names are used more than once (Blender adds .001 suffixes): %s" % (len(dups), ", ".join(dups[:8]) + (" ..." if len(dups) > 8 else ""))))
    if negative_scale:
        issues.append(_issue("info", "negative_scale", "%d objects use negative scale (mirrored); normals are kept as stored." % negative_scale))
    if counts["nurbs"]:
        issues.append(_issue("warning", "nurbs", "%d NURBS/patch objects are not imported by Blender's FBX importers." % counts["nurbs"]))



    #geometry n meshes
    mesh_infos = []
    total_vertices = 0
    total_polygons = 0
    geometries = doc.objects_of(b"Geometry", b"Mesh")
    geom_to_models = {}
    for m in models:
        if m.subtype in MESH_SUBTYPES:
            for g in doc.children(m.uid, b"Geometry"):
                geom_to_models.setdefault(g.uid, []).append(m)
    if doc.is_v6:
        geometries = []  #v6 shoves the geometry inside the Model node itself
        for m in models:
            if m.subtype in MESH_SUBTYPES:
                geom_to_models[m.uid] = [m]
    mat_users = {}
    for m in models:
        for mat in doc.children(m.uid, b"Material"):
            mat_users.setdefault(mat.uid, []).append(m.name)
    geom_objects = geometries if not doc.is_v6 else [m for m in models if m.subtype in MESH_SUBTYPES]
    uv_missing = 0
    for g in geom_objects:
        if cancel is not None and cancel.is_set():
            raise FbxError("cancelled")
        node = g.node
        verts = _array_count(node, b"Vertices") // 3
        polys = _count_polygons(node.find(b"PolygonVertexIndex")) if deep else -1
        total_vertices += verts
        if polys > 0:
            total_polygons += polys
        users = geom_to_models.get(g.uid, [])
        uv_sets = _layer_names(node, b"LayerElementUV")
        info = {
            "uid": _uid_str(g.uid),
            "name": users[0].name if users else g.name,
            "geometry": g.name,
            "instances": len(users),
            "vertices": verts,
            "polygons": polys,
            "uv_sets": uv_sets,
            "color_sets": _layer_names(node, b"LayerElementColor"),
            "has_normals": node.find(b"LayerElementNormal") is not None,
            "has_tangents": node.find(b"LayerElementTangent") is not None,
            "has_binormals": node.find(b"LayerElementBinormal") is not None,
            "has_smoothing": node.find(b"LayerElementSmoothing") is not None,
            "materials": [mat.name for mat in (doc.children(users[0].uid, b"Material") if users else [])],
            "shape_keys": [],
            "skinned": False,
            "parent": "",
        }
        if users:
            parent = doc.model_parent(users[0].uid)
            info["parent"] = parent.name if parent is not None else ""
        if not uv_sets:
            uv_missing += 1
        mesh_infos.append(info)
    summary["meshes"] = mesh_infos
    summary["geometry"] = {"vertices": total_vertices, "polygons": total_polygons if deep else -1}
    if total_vertices > 2_000_000:
        issues.append(_issue("info", "high_poly", "High-poly content: %s vertices in total." % format(total_vertices, ",")))

    #deformers (skins, blendshapes)
    skins = doc.objects_of(b"Deformer", b"Skin")
    counts["skins"] = len(skins)
    counts["clusters"] = len(clusters)
    geom_index = {mi["uid"]: mi for mi in mesh_infos}
    for skin in skins:
        for g in doc.parents(skin.uid, b"Geometry"):
            mi = geom_index.get(_uid_str(g.uid))
            if mi is not None:
                mi["skinned"] = True
    blend_shapes = doc.objects_of(b"Deformer", b"BlendShape")
    channels = doc.objects_of(b"Deformer", b"BlendShapeChannel")
    counts["blend_shapes"] = len(blend_shapes)
    counts["shape_channels"] = len(channels)
    counts["shapes"] = len(doc.objects_of(b"Geometry", b"Shape"))
    inbetween = []
    missing_full_weights = 0
    for bs in blend_shapes:
        geoms = doc.parents(bs.uid, b"Geometry")
        for ch in doc.children(bs.uid, b"Deformer"):
            shapes = doc.children(ch.uid, b"Geometry")
            if len(shapes) > 1:
                inbetween.append(ch.name)
            fw = ch.node.find(b"FullWeights")
            if len(shapes) >= 1 and (fw is None or _array_count(ch.node, b"FullWeights") < len(shapes)):
                missing_full_weights += 1
            for g in geoms:
                mi = geom_index.get(_uid_str(g.uid))
                if mi is not None:
                    mi["shape_keys"].append(ch.name)
    summary["inbetween_channels"] = inbetween[:256]
    if inbetween:
        issues.append(_issue("warning", "inbetween_shapes",
                             "%d shape-key channels use in-between shapes. Blender's Python importer aborts on these; HJT FBX+ splits them into separate shape keys and can rebuild the blending with drivers." % len(inbetween)))
    if missing_full_weights:
        issues.append(_issue("info", "missing_full_weights", "%d shape channels lack FullWeights data (repaired before import)." % missing_full_weights))

    #bind poses. comparing TransformLink matrices across meshes bc game rips love having 3 diff bind poses for 1 bone, wtf
    bind_by_bone = {}
    inconsistent = set()
    for cl in clusters:
        link = _matrix(cl.node.find(b"TransformLink"))
        if link is None:
            continue
        for bone in doc.children(cl.uid, b"Model"):
            prev = bind_by_bone.get(bone.uid)
            if prev is None:
                bind_by_bone[bone.uid] = link
            elif not _matrices_close(prev, link):
                inconsistent.add(bone.name)
    if inconsistent:
        issues.append(_issue("warning", "inconsistent_bind_poses",
                             "%d bones are bound with different bind matrices in different meshes. Blender keeps one rest pose per armature, so some meshes may deform differently from the game: %s" % (
                                 len(inconsistent), ", ".join(sorted(inconsistent)[:6]) + (" ..." if len(inconsistent) > 6 else ""))))

    #materials n textures
    materials = []
    embedded = 0
    for mat in doc.objects_of(b"Material"):
        texs = material_textures(doc, mat)
        for t in texs:
            if t.get("embedded"):
                embedded += 1
        materials.append({
            "uid": _uid_str(mat.uid),
            "name": mat.name,
            "users": mat_users.get(mat.uid, [])[:32],
            "props": _material_props(mat),
            "textures": texs,
        })
    summary["materials"] = materials
    counts["embedded_textures"] = embedded
    #textures w no material, still listed so dependency stuff can find them
    attached = {t["uid"] for m in materials for t in m["textures"]}
    loose = []
    for tex in doc.objects_of(b"Texture"):
        if _uid_str(tex.uid) not in attached:
            loose.append(texture_info(doc, tex))
    summary["loose_textures"] = loose
    if embedded:
        issues.append(_issue("info", "embedded_textures", "%d textures are embedded in the FBX file." % embedded))
    if uv_missing and materials:
        issues.append(_issue("info", "uv_missing", "%d meshes have no UV map; textures cannot be mapped on them." % uv_missing))



    #animation
    anims = []
    fps = summary["fps"] or 24.0
    for stack in doc.objects_of(b"AnimationStack"):
        start = stack.prop(b"LocalStart")
        stop = stack.prop(b"LocalStop")
        if start is None or stop is None:
            start = stack.prop(b"ReferenceStart", 0)
            stop = stack.prop(b"ReferenceStop", 0)
        layers = doc.children(stack.uid, b"AnimationLayer")
        curve_nodes = 0
        for layer in layers:
            curve_nodes += len(doc.children(layer.uid, b"AnimationCurveNode"))
        try:
            s_sec = float(start) / FBX_KTIME
            e_sec = float(stop) / FBX_KTIME
        except (TypeError, ValueError):
            s_sec = e_sec = 0.0
        anims.append({
            "name": stack.name,
            "start_frame": round(s_sec * fps, 3),
            "end_frame": round(e_sec * fps, 3),
            "duration_s": round(max(0.0, e_sec - s_sec), 4),
            "layers": len(layers),
            "curve_nodes": curve_nodes,
        })
        if len(layers) > 1:
            issues.append(_issue("warning", "anim_layers", "Animation '%s' has %d layers; Blender imports each layer as a separate action and links only the first." % (stack.name, len(layers))))
    if doc.is_v6:
        takes = doc.root.find(b"Takes")
        if takes is not None:
            for take in takes.find_all(b"Take"):
                anims.append({"name": decode_name(take.prop(0, b"")), "start_frame": 0, "end_frame": 0,
                              "duration_s": 0.0, "layers": 1, "curve_nodes": len(take.children)})
    summary["animations"] = anims
    counts["anim_stacks"] = len(anims)

    if counts["meshes"] == 0:
        if anims:
            issues.append(_issue("info", "animation_only", "No meshes: this looks like an animation-only file."))
        else:
            issues.append(_issue("warning", "empty", "No meshes or animations were found."))

    #units
    usf = gs.get("UnitScaleFactor")
    if isinstance(usf, (int, float)) and usf > 0 and not math.isclose(usf, 1.0) and not math.isclose(usf, 100.0):
        issues.append(_issue("info", "unit_scale", "Unit scale factor is %g (%s per unit); Blender converts it automatically." % (usf, _unit_label(usf))))

    summary["material_names"] = [m["name"] for m in materials][:MAX_NAMES]
    summary["texture_files"] = sorted({_basename(t["relative"] or t["filename"]) for m in materials for t in m["textures"] if (t["relative"] or t["filename"])})[:MAX_NAMES]
    return summary


def _basename(path):
    path = path.replace("\\", "/")
    return path.rsplit("/", 1)[-1]


def _unit_label(usf):
    table = {1.0: "cm", 100.0: "m", 2.54: "inch", 30.48: "foot", 0.1: "mm", 100000.0: "km"}
    for k, v in table.items():
        if math.isclose(usf, k, rel_tol=1e-6):
            return v
    return "%g cm" % usf


def inspect_file(path, deep=True, cancel=None):
    #open, inspect, close. gives back the summary dict
    with FbxDocument.open(path, lazy=True, cancel=cancel) as doc:
        summary = inspect_document(doc, deep=deep, cancel=cancel)
        summary["node_count"] = doc.node_count
    return summary
