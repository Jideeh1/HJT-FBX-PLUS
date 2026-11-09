# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#the import queue: sources (archives / fbx files / folders) n the models found in them
# lives in python, the ui property groups r just a mirror (rebuilt by sync()).
#archive listing + model analysis run on worker threads, callbacks mutate this on the main thread

from __future__ import annotations

import os
from collections import OrderedDict

from ..archives import is_archive_path
from ..archives.base import ArchiveError, BackendUnavailable, PasswordRequired, WrongPassword
from ..fbx.document import FbxDocument, sniff_fbx
from ..fbx.inspect import inspect_document
from ..uemodel import ALL_PARTS, EXT as UE_EXT, is_uemodel, read_uemodel, summarize as ue_summarize
from ..profiles.detect import detect as detect_profile
from ..textures.discovery import plan_materials


class SourceState:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.label = os.path.basename(path)
        low = path.lower()
        self.kind = "FBX" if low.endswith((".fbx", UE_EXT)) else ("ZIP" if low.endswith(".zip") else ("RAR" if (low.endswith(".rar")) else ("7Z" if ".7z" in low else "FOLDER")))
        if os.path.isdir(path):
            self.kind = "FOLDER"
        self.password = None
        self.status = "PENDING"  # PENDING LISTING READY LOCKED ERROR
        self.message = ""
        self.backend = ""
        self.size = 0
        self.entries = 0
        self.fbx = 0
        self.images = 0
        self.encrypted = False
        self.solid = False
        self.one_pass = False  #extract in one go (solid or external tool), see pipeline.prefetch
        self.notes = []
        self.aidx = None
        self.expanded = set([""])

    @property
    def key(self):
        return self.path

    @property
    def is_archive(self):
        return self.kind in ("ZIP", "7Z", "RAR")


class ModelState:
    def __init__(self, source_key, path, member=None, size=0):
        self.source_key = source_key
        self.path = path
        self.member = member
        self.size = size
        self.label = os.path.splitext(os.path.basename(member or path))[0]
        self.status = "PENDING"  # PENDING ANALYZING READY WARN ERROR IMPORTED
        self.selected = True
        self.summary = None
        self.plans = []
        self.profile_id = ""
        self.profile_scores = {}
        self.message = ""
        self.textures_faithful = 0
        self.textures_inferred = 0
        self.textures_missing = 0

    @property
    def key(self):
        return "%s|%s" % (self.source_key, self.member or "")


class Session:
    def __init__(self, services, tasks):
        self.services = services
        self.tasks = tasks
        self.sources = OrderedDict()
        self.models = OrderedDict()
        self.revision = 0
        self.on_change = None  #main thread callback -> ui sync

    def changed(self):
        self.revision += 1
        if self.on_change is not None:
            self.on_change()

    #adding stuff
    def add_paths(self, paths, password=None, auto_analyze=True, options=None):
        added = []
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                added += self.add_folder(p, auto_analyze=auto_analyze, options=options)
                continue
            if not os.path.isfile(p):
                continue
            if p.lower().endswith((".fbx", UE_EXT)):
                src = self.sources.get(p) or SourceState(p)
                src.status = "READY"
                src.size = os.path.getsize(p)
                src.fbx = 1
                self.sources[p] = src
                m = ModelState(p, p, None, src.size)
                if m.key not in self.models:
                    self.models[m.key] = m
                    added.append(m.key)
            elif is_archive_path(p):
                src = self.sources.get(p) or SourceState(p)
                if password:
                    src.password = password
                self.sources[p] = src
                self.list_archive(src, auto_analyze, options)
        self.changed()
        if auto_analyze and added:
            self.analyze(added, options)
        return added

    def add_folder(self, folder, auto_analyze=True, options=None, max_depth=6):
        #fbx files + archives anywhere under the folder (depth limited)
        found = []
        for root, dirs, files in os.walk(folder):
            depth = root[len(folder):].count(os.sep)
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(files):
                full = os.path.join(root, f)
                if f.lower().endswith((".fbx", UE_EXT)) or is_archive_path(f):
                    found.append(full)
        return self.add_paths(found, auto_analyze=auto_analyze, options=options) if found else []

    def list_archive(self, src, auto_analyze=True, options=None):
        src.status = "LISTING"
        src.message = "Reading archive..."

        def work(task, path, password):
            reader, aidx, _tidx = self.services.archive(path, password)
            return (reader.backend, reader.has_encrypted, reader.solid, list(reader.notes), aidx, os.path.getsize(path),
                    reader.solid or reader.one_pass_preferred)

        def done(result):
            backend, enc, solid, notes, aidx, size, one_pass = result
            src.status = "READY"
            src.backend = backend
            src.encrypted = enc
            src.solid = solid
            src.one_pass = one_pass
            src.notes = notes
            src.aidx = aidx
            src.size = size
            st = aidx.stats()
            src.entries = st["files"]
            src.fbx = st["fbx"]
            src.images = st["images"]
            src.message = "%d models, %d images, %d files" % (st["fbx"], st["images"], st["files"])
            new = []
            for m in _all_parts_models(src, aidx.fbx) + [ModelState(src.key, src.path, e.path, e.size) for e in aidx.fbx]:
                if m.key not in self.models:
                    self.models[m.key] = m
                    new.append(m.key)
            if not aidx.fbx:
                src.message = "No FBX or UEModel files in this archive"
            self.changed()
            if auto_analyze and new:
                self.analyze(new, options)

        def fail(exc, tb):
            if isinstance(exc, (PasswordRequired, WrongPassword)):
                src.status = "LOCKED"
                src.message = "Password required" if isinstance(exc, PasswordRequired) else "Wrong password"
            else:
                src.status = "ERROR"
                src.message = str(exc) if isinstance(exc, ArchiveError) else "%s: %s" % (type(exc).__name__, exc)
            self.changed()

        self.tasks.submit(work, src.path, src.password, key=("list", src.key), label="Reading %s" % src.label,
                          on_done=done, on_error=fail)

    def unlock(self, source_key, password, auto_analyze=True, options=None):
        src = self.sources.get(source_key)
        if src is None:
            return
        src.password = password
        self.services.forget_archive(src.path)
        self.list_archive(src, auto_analyze, options)

    def remove(self, model_keys):
        for k in model_keys:
            self.models.pop(k, None)
        used = {m.source_key for m in self.models.values()}
        for sk in list(self.sources):
            if sk not in used and self.sources[sk].status != "LISTING":
                src = self.sources.pop(sk)
                if src.is_archive:
                    self.services.forget_archive(src.path)
        self.changed()

    def clear(self):
        self.tasks.cancel()
        for src in self.sources.values():
            if src.is_archive:
                self.services.forget_archive(src.path)
        self.sources.clear()
        self.models.clear()
        self.changed()

    #analysis (preview)
    def analyze(self, model_keys, options=None):
        opts = options or {}
        #solid archives: one pass for all their fbx first. the pool is fifo so this runs before the
        # analysis tasks, n those just wait on the cache lock n then find their file already there
        by_src = {}
        for key in model_keys:
            m = self.models.get(key)
            src = self.sources.get(m.source_key) if m is not None else None
            if m is not None and m.member and src is not None and src.one_pass:
                by_src.setdefault(src.key, (src, []))[1].append(m.member)
        for src, members in by_src.values():
            if len(members) > 1:
                self.tasks.submit(pipeline_prefetch, src.path, src.password, self.services, members,
                                  key=("prefetch", src.key), label="Reading %s" % src.label)
        for key in model_keys:
            m = self.models.get(key)
            if m is None:
                continue
            m.status = "ANALYZING"
            m.message = "Analyzing..."
            src = self.sources.get(m.source_key)
            self.tasks.submit(_analyze_model, m.path, m.member, src.password if src else None, self.services, opts,
                              key=("analyze", key), label="Analyzing %s" % m.label,
                              on_done=self._analysis_done(key), on_error=self._analysis_failed(key))
        self.changed()

    def _analysis_done(self, key):
        def cb(result):
            m = self.models.get(key)
            if m is None:
                return
            m.summary, m.plans, m.profile_id, m.profile_scores = result
            faithful = inferred = missing = 0
            for p in m.plans:
                for a in p.assignments:
                    if a.faithful:
                        faithful += 1
                    else:
                        inferred += 1
                missing += len(p.missing)
                if not p.has("base_color") and any(w.startswith("No base color") for w in p.warnings):
                    missing += 1
            m.textures_faithful, m.textures_inferred, m.textures_missing = faithful, inferred, missing
            issues = [i for i in m.summary.get("issues", []) if i["level"] in ("warning", "error")]
            warn_plans = any(p.warnings for p in m.plans)
            m.status = "WARN" if (issues or missing or warn_plans) else "READY"
            m.message = "%d meshes, %d materials, %d bones" % (m.summary["counts"]["meshes"], m.summary["counts"]["materials"], m.summary["counts"]["bones"])
            self.changed()
        return cb

    def _analysis_failed(self, key):
        def cb(exc, tb):
            m = self.models.get(key)
            if m is None:
                return
            m.status = "ERROR"
            if isinstance(exc, PasswordRequired):
                m.message = "Password required"
            else:
                m.message = str(exc)[:300]
            self.changed()
        return cb

    def selected_models(self):
        return [m for m in self.models.values() if m.selected and m.status not in ("ANALYZING",)]


def _analyze_model(task, path, member, password, services, opts):
    #worker: extract just the fbx, inspect it, plan textures (no texture extraction for a preview)
    reader = tidx = None
    all_parts = (member or path).endswith(ALL_PARTS)
    if member is not None:
        reader, aidx, tidx = services.archive(path, password)
        if all_parts:
            base_dir = member[:-len(ALL_PARTS)].rstrip("/")
            entries = [e for e in aidx.fbx if is_uemodel(e.path) and e.path.rpartition("/")[0] == base_dir]
        else:
            entry = reader.find(member)
            entries = [entry] if entry is not None else []
            base_dir = member.rpartition("/")[0]
        if not entries:
            raise ArchiveError("'%s' is missing from the archive" % member)
        got = services.cache.materialize(reader, entries, cancel=task.cancel)
        files = [got[e.path] for e in entries]
    else:
        folder = os.path.dirname(path)
        files = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if is_uemodel(f)] if all_parts else [path]
        base_dir = folder.replace("\\", "/")
        roots = [folder]
        parent = os.path.dirname(roots[0])
        if int(opts.get("search_parent_levels", 1)) > 0 and parent and parent != roots[0]:
            roots.append(parent)
        tidx = services.disk_index(roots, int(opts.get("search_depth", 4)), task.cancel)
    local = files[0]
    task.set_progress(0.3, "Parsing")
    if is_uemodel(member or path):
        parts = []
        for f in files:
            with open(f, "rb") as fh:
                parts.append((os.path.splitext(os.path.basename(f))[0], read_uemodel(fh.read())))
        summary = ue_summarize(parts)
    else:
        with FbxDocument.open(local, lazy=True, cancel=task.cancel) as doc:
            summary = inspect_document(doc, deep=True, cancel=task.cancel)
    nearby = [e.name for e in tidx.images if e.dir_l.startswith(base_dir.lower())][:400]
    if opts.get("profile", "AUTO") == "AUTO":
        pid, scores, _hits = detect_profile(services.registry, summary, nearby, (), member or path)
    else:
        pid, scores = opts["profile"], {}
    profile = services.registry.get(pid)

    def read_json(e):
        if e.archive_entry is not None:
            return services.read_entry(reader, e.archive_entry, task.cancel)
        with open(e.abspath, "rb") as fh:
            return fh.read()

    plans = plan_materials(summary, tidx, profile, base_dir, {}, read_json)
    task.set_progress(1.0, "Done")
    return summary, plans, profile.id, scores


def _all_parts_models(src, entries):
    #ue characters come as body + face + hair + ... sharing one skeleton. a folder w 2+ of them
    # gets an extra "all parts" model so they land on ONE armature instead of 4
    by_dir = {}
    for e in entries:
        if is_uemodel(e.path):
            by_dir.setdefault(e.path.rpartition("/")[0], []).append(e)
    out = []
    for folder, lst in by_dir.items():
        if len(lst) < 2:
            continue
        m = ModelState(src.key, src.path, (folder + "/" if folder else "") + ALL_PARTS, sum(e.size for e in lst))
        m.label = "%s (all %d parts)" % (folder.rsplit("/", 1)[-1] or os.path.splitext(src.label)[0], len(lst))
        out.append(m)
    return out


def pipeline_prefetch(task, path, password, services, members):
    from .pipeline import prefetch
    return prefetch(task, services, path, password, members)


def sniff(path):
    return sniff_fbx(path)


def is_password_error(exc):
    return isinstance(exc, (PasswordRequired, WrongPassword))


def is_backend_error(exc):
    return isinstance(exc, BackendUnavailable)
