# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#copies the python side session into the window manager property groups the ui lists draw from
# lists only get rebuilt when their keys change, otherwise fields r updated in place.
#the archive tree only builds rows for EXPANDED folders so a 50k file archive stays snappy

from __future__ import annotations

import time

import bpy

from .. import state

TREE_ROW_LIMIT = 4000


def sync_all(context):
    st = state.get()
    wm = getattr(context, "window_manager", None)
    if st is None or wm is None or not hasattr(wm, "hjt_fbx_plus"):
        return
    s = wm.hjt_fbx_plus
    sync_models(s, st)
    sync_sources(s, st)
    sync_tree(s, st)
    sync_messages(s, st)
    sync_profiles(s, st)
    b = st.batch
    s.running = b is not None
    if b is None:
        active = st.tasks.active()
        if active:
            t = active[0]
            s.status_text = "%s%s" % (t.label, (" - " + t.message) if t.message else "")
            s.progress = t.progress
        elif s.status_text and not s.running:
            s.status_text = ""


def sync_models(s, st):
    models = list(st.session.models.values())
    keys = [m.key for m in models]
    if [i.key for i in s.models] != keys:
        s.models.clear()
        for m in models:
            it = s.models.add()
            it.key = m.key
        s.models_index = min(s.models_index, max(0, len(s.models) - 1))
    for it, m in zip(s.models, models):
        if it.name != m.label:
            it.name = m.label  #default uilist filter searches .name
        it.label = m.label
        src = st.session.sources.get(m.source_key)
        it.source = src.label if src else ""
        it.member = m.member or ""
        if it.status != m.status:
            it.status = m.status
        if it.selected != m.selected:
            it.selected = m.selected
        it.message = m.message
        p = st.registry.profiles.get(m.profile_id)
        it.profile = p.name if p else ""
        it.size = min(m.size, 2 ** 31 - 1)


def sync_sources(s, st):
    srcs = list(st.session.sources.values())
    keys = [x.key for x in srcs]
    if [i.key for i in s.sources] != keys:
        old_pw = {i.key: i.password for i in s.sources}
        s.sources.clear()
        for x in srcs:
            it = s.sources.add()
            it.key = x.key
            it.password = old_pw.get(x.key, "")
        s.sources_index = min(s.sources_index, max(0, len(s.sources) - 1))
    for it, x in zip(s.sources, srcs):
        it.label = x.label
        it.kind = x.kind
        it.status = x.status
        it.message = x.message
        it.backend = x.backend


def active_source(s, st):
    if not s.sources or s.sources_index >= len(s.sources):
        return None
    return st.session.sources.get(s.sources[s.sources_index].key)


def sync_tree(s, st):
    src = active_source(s, st)
    rows = []
    if src is not None and src.aidx is not None:
        aidx = src.aidx
        kind = s.tree_kind
        flt = s.tree_filter.strip().lower()

        def keep(e):
            k = e.kind
            if kind == "FBX":
                return k == "fbx"
            if kind == "IMAGE":
                return k == "image"
            if kind == "DATA":
                return k == "data"
            return True

        if flt:
            for e in aidx.files:
                if flt in e.path.lower() and keep(e):
                    rows.append((e.path, e.path.count("/") * 0, False, False, e.kind, e.size, ""))
                    if len(rows) >= TREE_ROW_LIMIT:
                        break
        else:
            def walk(path, depth):
                node = aidx.dirs.get(path)
                if node is None:
                    return
                for sub in node.dirs:
                    d = aidx.dirs[sub]
                    open_ = sub in src.expanded
                    rows.append((sub, depth, True, open_, "dir", d.size, "%d FBX, %d images" % (d.fbx, d.images) if (d.fbx or d.images) else ""))
                    if open_ and len(rows) < TREE_ROW_LIMIT:
                        walk(sub, depth + 1)
                for e in node.files:
                    if keep(e):
                        rows.append((e.path, depth, False, False, e.kind, e.size, ""))
                    if len(rows) >= TREE_ROW_LIMIT:
                        return
            walk("", 0)
    sig = (id(src), len(rows), rows[:1] and rows[0][0], rows[-1:] and rows[-1][0], s.tree_filter, s.tree_kind,
           tuple(sorted(src.expanded)) if src else ())
    if getattr(sync_tree, "_sig", None) == sig and len(s.tree) == len(rows):
        return
    sync_tree._sig = sig
    s.tree.clear()
    for path, depth, is_dir, exp, kind, size, info in rows:
        it = s.tree.add()
        it.path = path
        it.depth = depth
        it.is_dir = is_dir
        it.expanded = exp
        it.kind = kind
        it.size = min(size, 2 ** 31 - 1)
        it.info = info


def sync_messages(s, st):
    rep = st.last_report
    msgs = []
    if rep:
        for m in rep.get("models", []):
            for msg in m.get("messages", []):
                if s.msg_filter == "ALL" or msg["level"] == s.msg_filter:
                    msgs.append((msg["level"], msg["text"], m.get("name", "")))
    sig = (rep.get("id") if rep else None, s.msg_filter, len(msgs))
    if getattr(sync_messages, "_sig", None) == sig:
        return
    sync_messages._sig = sig
    s.messages.clear()
    for level, text, model in msgs[:5000]:
        it = s.messages.add()
        it.level = level
        it.text = text
        it.model = model


def sync_history(s, st):
    s.history.clear()
    for e in st.history.entries(100):
        it = s.history.add()
        it.rid = e["id"]
        it.summary = e["summary"]
        it.when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e["started"]))
        it.names = e["names"]


def sync_profiles(s, st):
    items = st.registry.ordered()
    if [r.pid for r in s.profiles] == [p.id for p in items]:
        return
    s.profiles.clear()
    for p in items:
        it = s.profiles.add()
        it.pid = p.id
        it.name = p.name
        it.builtin = p.builtin
    s.profiles_index = min(s.profiles_index, max(0, len(s.profiles) - 1))


def on_load_post(_dummy=None):
    st = state.get()
    if st is not None:
        st.dirty = True
        sync_tree._sig = None
        sync_messages._sig = None
        try:
            sync_history(bpy.context.window_manager.hjt_fbx_plus, st)
        except Exception:  # noqa: BLE001
            pass
