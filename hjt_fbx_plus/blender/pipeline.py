# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#the import pipeline for ONE model, in 3 stages
#  prepare_job  (worker thread, no bpy) find + extract only whats needed, inspect, detect profile,
#               plan textures, write the sanitized/uid stamped copy
#  import_job   (main thread) run blenders importer into a fresh collection, diff + map
#  finish_job   (main thread) materials, images, tangents, skin check, bones, categories, storage
#the batch operator calls these one after another so the ui can breathe between them

from __future__ import annotations

import hashlib
import os
import threading
import time

from ..archives import open_archive
from ..archives.base import ArchiveError, Cancelled
from ..archives.index import ArchiveIndex
from ..core.log import log
from ..fbx.document import PREPARE_LAZY_NODES, FbxDocument
from ..fbx.elements import FbxBlob, decode_name
from ..fbx.inspect import inspect_document
from ..fbx.prepare import PrepareOptions, prepare_document, uid_key
from ..profiles.detect import detect as detect_profile
from ..textures.discovery import plan_materials, required_entries
from ..textures.imageinfo import probe_file
from ..textures.index import TexEntry, TextureIndex
from ..textures.slots import SLOTS
from ..uemodel import ALL_PARTS, is_uemodel, read_uemodel, summarize as ue_summarize

#these touch bpy, only used from the main thread stages
import bpy  # noqa: E402

from . import chains, compat, engines, images as img_mod, mapping as map_mod, materials, postprocess, uebuild  # noqa: E402

UE_ENGINE = "UEMODEL"


class _UEEngine:
    id = UE_ENGINE
    label = "UEFormat (.uemodel)"


class Source:
    #what to import: a loose fbx or an fbx inside an archive
    def __init__(self, path, member=None, password=None, label=None):
        self.path = os.path.abspath(path)
        self.member = member
        self.password = password
        self.label = label or (os.path.splitext(os.path.basename(member or path))[0])

    @property
    def is_archive(self):
        return self.member is not None

    @property
    def key(self):
        return "%s|%s" % (self.path, self.member or "")


class Services:
    #shared stuff across jobs: profiles, cache, open archives, texture indexes
    def __init__(self, registry, cache, tool_paths=None):
        self.registry = registry
        self.cache = cache
        self.tool_paths = tool_paths or {}
        self._archives = {}
        self._disk = {}
        self._lock = threading.Lock()

    def archive(self, path, password=None):
        key = os.path.abspath(path)
        with self._lock:
            hit = self._archives.get(key)
            if hit is not None and hit[3] == password:
                return hit[0], hit[1], hit[2]
            if hit is not None:
                try:
                    hit[0].close()
                except Exception:  # noqa: BLE001
                    pass
        reader = open_archive(path, password=password, tool_paths=self.tool_paths)
        aidx = ArchiveIndex(reader.entries)
        tidx = TextureIndex.from_archive(aidx)
        with self._lock:
            self._archives[key] = (reader, aidx, tidx, password)
        return reader, aidx, tidx

    def forget_archive(self, path):
        with self._lock:
            hit = self._archives.pop(os.path.abspath(path), None)
        if hit is not None:
            try:
                hit[0].close()
            except Exception:  # noqa: BLE001
                pass

    def disk_index(self, roots, depth, cancel=None):
        key = (tuple(roots), depth)
        stamp = tuple(_mtime(r) for r in roots)
        with self._lock:
            hit = self._disk.get(key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        idx = TextureIndex.from_disk(roots, max_depth=depth, cancel=cancel)
        with self._lock:
            self._disk[key] = (stamp, idx)
        return idx

    def read_entry(self, reader, entry, cancel=None, track=None):
        #small archive files (material json) go through the cache too. reader.read() unpacks to a
        # throwaway folder, which on a solid archive behind an external tool means decoding from
        # the very start again for EVERY json. 12 materials = 12 full passes, nope
        if entry.size > 64 * 1024 * 1024:
            raise ArchiveError("'%s' is too large to read into memory" % entry.path)
        local = self.cache.materialize(reader, [entry], cancel=cancel)[entry.path]
        if track is not None:
            track.append(local)
        with open(local, "rb") as fh:
            return fh.read()

    def close(self):
        with self._lock:
            items = list(self._archives.values())
            self._archives.clear()
            self._disk.clear()
        for it in items:
            try:
                it[0].close()
            except Exception:  # noqa: BLE001
                pass


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


class JobPrep:
    def __init__(self, source):
        self.source = source
        self.local_fbx = ""
        self.prepared_path = ""
        self.prep_result = None
        self.doc = None
        self.summary = None
        self.profile = None
        self.profile_scores = {}
        self.plans = []
        self.tex_local = {}
        self.image_info = {}
        self.engine_id = ""
        self.notes = []
        self.timings = {}
        self.extracted = []
        self.base_dir = ""
        self.fatal = ""
        self.ue_parts = None  #[(label, UEModel)] when the source is .uemodel

    def close(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None


def resolve_settings(opts, profile):
    #scene settings + profile import overrides -> settings for the engine
    s = dict(opts)
    if opts.get("apply_profile_import", True):
        for k, v in profile.import_overrides.items():
            s[k] = v
    return s


def prepare_job(task, source, opts, services, engine_avail):
    #worker thread. NO bpy in here. engine_avail = engines.availability() taken on the main thread
    t0 = time.monotonic()
    prep = JobPrep(source)
    cancel = task.cancel if task is not None else None

    def step(frac, msg):
        if task is not None:
            task.set_progress(frac, msg)
        if cancel is not None and cancel.is_set():
            raise Cancelled("cancelled")

    reader = aidx = tidx = None
    ue = is_uemodel(source.member or source.path)
    ue_files = []
    step(0.02, "Locating %s" % source.label)
    if source.is_archive:
        reader, aidx, tidx = services.archive(source.path, source.password)
        if source.member.endswith(ALL_PARTS):
            folder = source.member[:-len(ALL_PARTS)].rstrip("/")
            entries = [e for e in aidx.fbx if is_uemodel(e.path) and e.path.rpartition("/")[0] == folder]
        else:
            entry = reader.find(source.member)
            entries = [entry] if entry is not None else []
        if not entries:
            raise ArchiveError("'%s' is no longer in %s" % (source.member, os.path.basename(source.path)))
        got = services.cache.materialize(reader, entries, cancel=cancel)
        for e in entries:
            prep.extracted.append(got[e.path])
            ue_files.append((os.path.splitext(e.name)[0], got[e.path]))
        prep.local_fbx = got[entries[0].path]
        prep.base_dir = entries[0].path.rpartition("/")[0]
    else:
        prep.local_fbx = source.path
        prep.base_dir = os.path.dirname(source.path).replace("\\", "/")
        if ue and source.path.endswith(ALL_PARTS):
            folder = source.path[:-len(ALL_PARTS)]
            ue_files = [(os.path.splitext(f)[0], os.path.join(folder, f)) for f in sorted(os.listdir(folder)) if is_uemodel(f)]
            prep.base_dir = folder.rstrip("\\/").replace("\\", "/")
        elif ue:
            ue_files = [(source.label, source.path)]
    prep.timings["locate"] = time.monotonic() - t0

    doc = None
    t1 = time.monotonic()
    if ue:
        step(0.15, "Reading UEModel")
        prep.ue_parts = []
        for label, path in ue_files:
            with open(path, "rb") as fh:
                prep.ue_parts.append((label, read_uemodel(fh.read())))
            step(0.15 + 0.15 * len(prep.ue_parts) / max(1, len(ue_files)), "Reading %s" % label)
        prep.summary = ue_summarize(prep.ue_parts, source.label)
    else:
        step(0.15, "Reading FBX")
        doc = FbxDocument.open(prep.local_fbx, lazy=False, cancel=cancel, lazy_names=PREPARE_LAZY_NODES)
        prep.doc = doc
        prep.summary = inspect_document(doc, deep=True, cancel=cancel)
    prep.timings["parse"] = time.monotonic() - t1

    step(0.35, "Finding textures")
    t2 = time.monotonic()
    if tidx is None:
        roots = [os.path.dirname(source.path)]
        parent = roots[0]
        for _ in range(int(opts.get("search_parent_levels", 1))):
            nxt = os.path.dirname(parent)
            if nxt and nxt != parent:
                parent = nxt
        if parent != roots[0]:
            roots.append(parent)
        tidx = services.disk_index(roots, int(opts.get("search_depth", 4)), cancel)
    #embedded media -> real files in the cache so they behave like any other texture
    embedded = _extract_embedded(doc, services.cache, source, prep) if doc is not None else {}
    #profile
    nearby = [e.name for e in tidx.images if e.dir_l.startswith(prep.base_dir.lower())][:400]
    if opts.get("profile", "AUTO") == "AUTO":
        pid, scores, _hits = detect_profile(services.registry, prep.summary, nearby, (), source.member or source.path)
        prep.profile_scores = scores
    else:
        pid = opts["profile"]
    prep.profile = services.registry.get(pid)
    tex_opts = prep.profile.data["textures"]
    tex_opts["use_material_json"] = tex_opts.get("use_material_json", True) and opts.get("use_material_json", True)
    tex_opts["use_fbx_references"] = tex_opts.get("use_fbx_references", True) and opts.get("use_fbx_references", True)
    tex_opts["name_matching"] = tex_opts.get("name_matching", True) and opts.get("name_matching", True)

    def read_json(e):
        if e.archive_entry is not None:
            return services.read_entry(reader, e.archive_entry, cancel, prep.extracted)
        with open(e.abspath, "rb") as fh:
            return fh.read()

    prep.plans = plan_materials(prep.summary, tidx, prep.profile, prep.base_dir, embedded, read_json)
    needed = required_entries(prep.plans)
    prep.timings["textures"] = time.monotonic() - t2

    step(0.55, "Extracting %d textures" % len(needed))
    t3 = time.monotonic()
    arc = [t for t in needed if t.archive_entry is not None]
    if arc:
        def prog(done, total):
            if task is not None:
                task.set_progress(0.55 + 0.3 * done / max(1, total), "Extracting textures")
        got = services.cache.materialize(reader, [t.archive_entry for t in arc], progress=prog, cancel=cancel)
        for t in arc:
            prep.tex_local[t.key] = got[t.archive_entry.path]
            prep.extracted.append(got[t.archive_entry.path])
    for t in needed:
        if t.archive_entry is None:
            prep.tex_local[t.key] = t.abspath or t.path
    for key, path in prep.tex_local.items():
        prep.image_info[key] = probe_file(path)
    prep.timings["extract"] = time.monotonic() - t3

    if ue:
        prep.engine_id = UE_ENGINE
        if len(prep.ue_parts) > 1:
            prep.notes.append("Imported %d parts onto one armature: %s." % (len(prep.ue_parts), ", ".join(lb for lb, _m in prep.ue_parts)))
        step(1.0, "Ready")
        return prep

    step(0.88, "Preparing FBX")
    t4 = time.monotonic()
    eng, notes = engines.choose(opts.get("engine", "AUTO"), doc.format, doc.version, engine_avail,
                                anim_curves=int(prep.summary.get("counts", {}).get("anim_curves", 0) or 0),
                                settings=resolve_settings(opts, prep.profile))
    prep.notes.extend(notes)
    if eng is None:
        raise RuntimeError(" ".join(notes) or "no FBX importer available")
    prep.engine_id = eng.id
    if doc.is_v6:
        prep.prepared_path = prep.local_fbx  #ufbx eats fbx 6 as is, we just cant stamp uids into it
        prep.notes.append("FBX 6 file: objects are matched by name after import.")
    else:
        h = hashlib.sha1(source.key.encode("utf-8", "surrogateescape")).hexdigest()[:10]
        out_dir = services.cache.loose_dir("prepared")
        out = os.path.join(out_dir, "%s_%s.fbx" % (_safe(source.label)[:40], h))
        po = PrepareOptions(inject_uids=True, sanitize=True, split_inbetweens=opts.get("split_inbetweens", True),
                            strip_user_props=not opts.get("use_custom_props", True))
        prep.prep_result = prepare_document(doc, out, po)
        prep.prepared_path = out
        prep.extracted.append(out)
    prep.timings["prepare"] = time.monotonic() - t4
    step(1.0, "Ready")
    return prep


JSON_PREFETCH_LIMIT = 32 * 1024 * 1024


def prefetch(task, services, path, password, members, plans=()):
    #worker. a solid archive is one long compressed stream, so pulling models out one by one means
    # decoding from the start again for each (external tools cant pause like our own 7z reader can).
    # before a batch or an analysis touches a solid archive we grab everything the queued models
    # need in ONE pass: their fbx, the textures they r planned to use n the material json.
    # 40 models out of a solid rar: 39 s -> about 2 s. -> list of local files
    cancel = task.cancel if task is not None else None
    reader, aidx, _tidx = services.archive(path, password)
    if not (reader.solid or reader.one_pass_preferred):
        return []
    want = {}
    for m in members:
        e = reader.find(m)
        if e is not None and not e.is_dir:
            want[e.path] = e
    for plan_list in plans:
        for t in required_entries(plan_list or []):
            if t.archive_entry is not None:
                e = reader.find(t.archive_entry.path)  #by path, the plan may come from an older reader
                if e is not None:
                    want[e.path] = e
    js = [e for e in aidx.files if e.path.lower().endswith(".json")]
    if js and sum(e.size for e in js) <= JSON_PREFETCH_LIMIT:
        for e in js:
            want[e.path] = e
    if not want:
        return []

    def prog(done, total):
        if task is not None:
            task.set_progress(done / max(1, total), "Reading %d files from %s" % (len(want), os.path.basename(path)))
    got = services.cache.materialize(reader, list(want.values()), progress=prog, cancel=cancel)
    return list(got.values())


def _safe(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "model"


def _extract_embedded(doc, cache, source, prep):
    out = {}
    videos = doc.objects_of(b"Video")
    if not videos:
        return out
    d = cache.loose_dir("embedded_" + hashlib.sha1(source.key.encode("utf-8", "surrogateescape")).hexdigest()[:10])
    for v in videos:
        content = v.node.find(b"Content")
        if content is None or not content.props:
            continue
        blob = content.props[0]
        data = blob.tobytes() if isinstance(blob, FbxBlob) else bytes(blob) if isinstance(blob, (bytes, bytearray)) else b""
        if not data:
            continue
        fname = decode_name(v.node.value(b"RelativeFilename", 0, b"") or v.node.value(b"Filename", 0, b"") or b"")
        fname = fname.replace("\\", "/").rsplit("/", 1)[-1] or (_safe(v.name) + ".png")
        path = os.path.join(d, fname)
        if not os.path.exists(path) or os.path.getsize(path) != len(data):
            with open(path, "wb") as fh:
                fh.write(data)
        e = TexEntry(path.replace("\\", "/"), len(data), None, None, abspath=path)
        out[uid_key(v.uid)] = e
        prep.extracted.append(path)
    return out


#main thread stages

class ImportResult:
    def __init__(self):
        self.collection = None
        self.new = {}
        self.mapping = None
        self.engine = None
        self.ok = False
        self.error = ""


def _target_collection(context, prep, opts):
    mode = opts.get("organize", "MODEL")
    parent = context.view_layer.active_layer_collection.collection
    if mode == "NONE":
        return parent
    if mode == "ARCHIVE" and prep.source.is_archive:
        aname = os.path.splitext(os.path.basename(prep.source.path))[0]
        coll = None
        for c in parent.children:
            if c.get("hjt_archive") == prep.source.path:
                coll = c
                break
        if coll is None:
            coll = bpy.data.collections.new(aname)
            coll["hjt_archive"] = prep.source.path
            parent.children.link(coll)
        parent = coll
    coll = bpy.data.collections.new(prep.source.label)
    parent.children.link(coll)
    return coll


def _import_ue(context, prep, opts, diag, res):
    compat.ensure_object_mode(context)
    coll = _target_collection(context, prep, opts)
    if opts.get("organize", "MODEL") == "NONE":
        coll = context.view_layer.active_layer_collection.collection
    res.collection = coll
    res.engine = _UEEngine
    before = map_mod.snapshot()
    t = time.monotonic()
    try:
        built, res.mapping = uebuild.build(context, prep.ue_parts, prep.source.label, coll)
    except Exception as exc:  # noqa: BLE001
        log.exception("uemodel build failed")
        res.new = map_mod.diff(before)
        _discard(res.new)
        res.error = "Could not build %s: %s" % (prep.source.label, exc)
        return res
    prep.timings["import"] = time.monotonic() - t
    res.new = map_mod.diff(before)
    for w in built.warnings:
        diag.warning(w, prep.source.label)
    res.ok = True
    return res


def import_job(context, prep, opts, diag):
    res = ImportResult()
    if prep.ue_parts is not None:
        return _import_ue(context, prep, opts, diag, res)
    model = prep.source.label
    eng = engines.ENGINES.get(prep.engine_id)
    if eng is None:
        res.error = "no FBX importer available"
        return res
    res.engine = eng
    settings = resolve_settings(opts, prep.profile)
    settings["use_custom_props"] = True  #uid mapping needs these, stripped again in prepare when user said no
    settings["use_image_search"] = False  #we do textures ourselves, way faster on big folders
    settings["mtl_name_collision_mode"] = "MAKE_UNIQUE"
    settings["validate_meshes"] = True
    kwargs, ignored = eng.build_kwargs(settings)
    if ignored:
        diag.info("%s ignores: %s" % (eng.label, ", ".join(sorted(ignored))), model)
    compat.ensure_object_mode(context)
    prev_active = context.view_layer.active_layer_collection
    coll = _target_collection(context, prep, opts)
    res.collection = coll
    compat.set_active_collection(context, coll)
    before = map_mod.snapshot()
    t = time.monotonic()
    try:
        ret = eng.run(prep.prepared_path, kwargs)
    except RuntimeError as exc:
        res.error = str(exc).strip().splitlines()[-1] if str(exc).strip() else "import failed"
        ret = {"CANCELLED"}
    finally:
        try:
            context.view_layer.active_layer_collection = prev_active
        except Exception:  # noqa: BLE001
            pass
    prep.timings["import"] = time.monotonic() - t
    res.new = map_mod.diff(before)
    if "FINISHED" not in ret:
        if not res.error:
            res.error = "%s could not import this file" % eng.label
        _discard(res.new)
        if coll is not None and opts.get("organize", "MODEL") != "NONE" and not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)
        return res
    res.mapping = map_mod.build(res.new)
    if not res.mapping.exact:
        res.mapping = map_mod.build_by_name(res.new, prep.summary, prep.doc)
        diag.info("Objects matched by name (exact FBX ids were not available).", model)
    res.ok = True
    return res


def _discard(new):
    #a failed import can still leave half built stuff behind, clean it so the scene stays consistent
    for name in ("objects", "meshes", "materials", "armatures", "actions", "images", "cameras", "lights"):
        coll = getattr(bpy.data, name, None)
        for idb in new.get(name, []):
            try:
                coll.remove(idb)
            except (ReferenceError, RuntimeError, TypeError):
                pass


def _alpha_mode_for(slot, settings):
    #channel packed for anything where alpha is data, so rgb doesnt get premultiplied garbage
    if slot != "base_color":
        return "CHANNEL_PACKED"
    usage = settings.get("base_alpha_usage", "AUTO")
    if usage in ("NONE", "EMISSION_MASK"):
        return "CHANNEL_PACKED"
    return settings.get("base_image_alpha", "STRAIGHT")


def finish_job(context, prep, res, opts, diag, image_cache, cache_root=""):
    model = prep.source.label
    report = {"materials": [], "approximations": 0}
    t = time.monotonic()
    profile = prep.profile
    mp = res.mapping
    importer_images = list(res.new.get("images", []))
    #materials
    mode_override = opts.get("material_mode", "PROFILE")
    template_mat = opts.get("template_material", "")
    for plan in prep.plans:
        mat = mp.materials.get(plan.uid)
        if mat is None:
            #name fallback (fbx 6 / ufbx w/o props)
            base = plan.name
            mat = next((m for m in res.new.get("materials", []) if m.name == base or m.name.rsplit(".", 1)[0] == base), None)
        if mat is None:
            diag.warning("Material '%s' was not created by the importer; skipped." % plan.name, model)
            continue
        settings = dict(plan.settings)
        if mode_override != "PROFILE":
            settings["template"] = mode_override
        if settings.get("template") == "TEMPLATE" and template_mat:
            settings["template_material"] = template_mat
        loaded = {}
        info = {}
        for a in plan.assignments:
            local = prep.tex_local.get(a.tex.key)
            if not local or not os.path.exists(local):
                continue
            cs = SLOTS.get(a.slot, SLOTS["aux"])["colorspace"]
            try:
                img, _new = image_cache.get(a.tex, local, cs, _alpha_mode_for(a.slot, settings),
                                            source=("%s:%s" % (os.path.basename(prep.source.path), a.tex.path)) if a.tex.archive_entry is not None else a.tex.path)
            except RuntimeError as exc:
                diag.warning(str(exc), model)
                continue
            loaded[a] = img
            info[img.name] = prep.image_info.get(a.tex.key, {})
        color_attr = ""
        for ob in mp.objects.values():
            if ob.type == "MESH" and any(sl.material == mat for sl in ob.material_slots):
                attrs = getattr(ob.data, "color_attributes", None)
                if attrs is not None and len(attrs):
                    color_attr = attrs[0].name
                break
        notes = []
        ctx = materials._Ctx(loaded, settings, plan, color_attr, info, profile.id, notes)
        try:
            materials.build(mat, plan, ctx)
        except Exception as exc:  # noqa: BLE001
            log.exception("material build failed")
            diag.error("Could not rebuild material '%s': %s" % (plan.name, exc), model)
            continue
        if ctx.template_result is not None:
            old = mat
            mat = ctx.template_result
            try:
                bpy.data.materials.remove(old)
            except (ReferenceError, RuntimeError):
                pass
        if plan.shader and settings.get("template") in ("PBR", "TOON", "FLAT"):
            notes.append("Original game shader '%s' cannot be reproduced exactly." % plan.shader)
        meta = materials.write_meta(mat, plan, profile.id, settings.get("template"), notes)
        if "approximation" in meta["shading"] or plan.shader:
            report["approximations"] += 1
        for w in plan.warnings:
            diag.warning(w, model)
        for miss in plan.missing:
            diag.warning("Missing texture for '%s' (%s): %s" % (plan.name, miss.get("slot"), miss.get("name")), model)
        report["materials"].append(dict(plan.as_dict(), shading=meta["shading"]))
    removed = img_mod.remove_unused(importer_images)
    prep.timings["materials"] = time.monotonic() - t

    #mesh level stuff
    t = time.monotonic()
    doc = prep.doc
    if doc is not None and opts.get("keep_tangents", True) and not opts.get("bake_space_transform", False):
        n = postprocess.store_tangents(doc, mp, diag, model)
        if n:
            diag.info("Kept FBX tangents on %d meshes (attributes fbx_tangent / fbx_binormal)." % n, model)
    corrections = profile.corrections
    if doc is None:
        bound = sum(1 for o in mp.objects.values() if o.type == "MESH" and o.find_armature() is not None)
        report["skinning"] = {"ok": bound, "repaired": 0}
    elif opts.get("verify_skinning", True) and corrections.get("verify_skinning", True):
        ok, fixed = postprocess.verify_skinning(doc, mp, diag, model)
        report["skinning"] = {"ok": ok, "repaired": fixed}
        if ok and not fixed:
            diag.info("Skinning verified: %d meshes bound to their armature." % ok, model)
    if prep.prep_result is not None and prep.prep_result.inbetweens and opts.get("inbetween_drivers", True):
        postprocess.rebuild_inbetweens(prep.prep_result.inbetweens, mp, diag, model)
    arms = {o for o in mp.objects.values() if o.type == "ARMATURE"} | {v[0] for v in mp.bones.values()}
    meshes = [o for o in mp.objects.values() if o.type == "MESH"]
    aligned_arms = set()
    al = opts.get("align_chains", "PROFILE")
    #raw ue axes point every bone sideways no matter the game, so From Profile always aligns those
    if al == "ON" or (al == "PROFILE" and (corrections.get("align_bone_chains", False) or prep.ue_parts is not None)):
        for arm in arms:
            try:
                r = chains.align_chains(arm, meshes)
                aligned_arms.add(arm)
                diag.info("Aligned bone chains on '%s': %d bones point at their chain, %d forks/leaves kept straight%s." % (
                    arm.name, r["aligned"], r["straight"], (", %d animation keys re-expressed" % r["keys"]) if r["keys"] else ""), model)
            except RuntimeError as exc:
                diag.warning("Bone chain alignment skipped on '%s': %s" % (arm.name, exc), model)
    fit = opts.get("fit_bones", "PROFILE")
    if fit == "ON" or (fit == "PROFILE" and corrections.get("fit_bone_lengths", False)):
        for arm in arms - aligned_arms:  #aligned ones already have fitted lengths
            try:
                n = postprocess.fit_bone_lengths(arm, meshes)
                diag.info("Fitted %d bone lengths on '%s' (display only; deformation unchanged)." % (n, arm.name), model)
            except RuntimeError as exc:
                diag.warning("Bone fitting skipped: %s" % exc, model)

    def sub_collection(name):
        if res.collection is None or opts.get("organize", "MODEL") == "NONE":
            return None
        full = "%s %s" % (prep.source.label, name)
        for c in res.collection.children:
            if c.name == full or c.get("hjt_sub") == name:
                return c
        c = bpy.data.collections.new(full)
        c["hjt_sub"] = name
        res.collection.children.link(c)
        return c

    report["categories"] = {k: len(v) for k, v in postprocess.categorize(doc, mp, profile, opts, diag, model, sub_collection).items()}
    prep.timings["post"] = time.monotonic() - t

    #textures storage. anything living in our temp cache gets packed no matter what, else it dies w the cache
    mode = opts.get("texture_storage", "AUTO")
    if mode == "AUTO":
        mode = "PACK" if prep.source.is_archive else "REFERENCE"
    dest = None
    if mode == "COPY":
        d = opts.get("copy_dir", "//textures") or "//textures"
        dest = bpy.path.abspath(d)
        if d.startswith("//") and not bpy.data.filepath:
            diag.warning("Save the .blend first to copy textures next to it; textures were packed instead.", model)
            mode = "PACK"
        else:
            dest = os.path.join(dest, _safe(prep.source.label))
    done, problems = img_mod.finalize_storage(image_cache, mode, cache_root, dest,
                                              _safe(os.path.splitext(os.path.basename(prep.source.path))[0]))
    for p in problems:
        diag.warning("Texture storage: %s" % p, model)

    #metadata on the collection
    if res.collection is not None and opts.get("tag_metadata", True) and opts.get("organize", "MODEL") != "NONE":
        c = res.collection
        c["hjt_source"] = prep.source.path + (("::" + prep.source.member) if prep.source.member else "")
        c["hjt_profile"] = profile.id
        c["hjt_engine"] = res.engine.id if res.engine else ""
        hdr = prep.summary.get("header", {})
        c["hjt_fbx"] = {"version": prep.summary.get("version", 0), "format": prep.summary.get("format", ""),
                        "creator": hdr.get("creator", ""), "unit_scale": float(prep.summary.get("global_settings", {}).get("UnitScaleFactor", 1.0) or 1.0)}
    report["removed_importer_images"] = removed
    report["stats"] = _stats(res.new)
    return report


def _stats(new):
    out = {}
    for k in ("objects", "meshes", "materials", "images", "armatures", "actions"):
        out[k] = len(new.get(k, []))
    bones = 0
    shape_keys = 0
    for ob in new.get("objects", []):
        try:
            if ob.type == "ARMATURE":
                bones += len(ob.data.bones)
            elif ob.type == "MESH" and ob.data.shape_keys is not None:
                shape_keys += max(0, len(ob.data.shape_keys.key_blocks) - 1)
        except ReferenceError:
            continue
    out["bones"] = bones
    out["shape_keys"] = shape_keys
    return out
