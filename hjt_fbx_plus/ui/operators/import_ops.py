# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#import operators: pick 1 fbx or 1 archive (file browser or drag n drop) -> the modal import runs 1 model

from __future__ import annotations

import os
import time

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ... import state
from ...archives import is_archive_path
from ...blender import compat, engines, images as img_mod, pipeline
from ...blender.report import RunReport
from ...core.log import Diagnostics, log
from ...uemodel import ALL_PARTS
from .. import sync
from ..props import profile_items, snapshot

FILTER = "*.fbx;*.uemodel;*.zip;*.7z;*.rar;*.001"
NAV_EVENTS = {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN",
              "TRACKPADZOOM", "TIMER_REPORT", "WINDOW_DEACTIVATE"}
LISTING_TIMEOUT = 180.0


def _addon_version():
    return state.addon_version()


def _ctx_override():
    #timers have no window/area in context. grab a 3d view so modal ops n popups have somewhere to live
    wm = bpy.context.window_manager
    for win in wm.windows:
        scr = win.screen
        if scr is None:
            continue
        for area in scr.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return {"window": win, "screen": scr, "area": area, "region": region}
    return {"window": wm.windows[0]} if wm.windows else {}


def _popup(title, lines, icon="ERROR"):
    def draw(menu, _context):
        for ln in lines:
            menu.layout.label(text=ln)
    try:
        with bpy.context.temp_override(**_ctx_override()):
            bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    except Exception:  # noqa: BLE001
        log.warning("%s: %s", title, " ".join(lines))


def _start_one(st, model_key):
    #only this model gets imported. everything else in the archive stays unchecked
    for m in st.session.models.values():
        m.selected = m.key == model_key
    st.session.changed()
    with bpy.context.temp_override(**_ctx_override()):
        bpy.ops.hjt_fbx_plus.run("INVOKE_DEFAULT")


def _rank(models, archive_path):
    #best guess first: same name as the archive > character model looking names > biggest file
    base = os.path.splitext(os.path.basename(archive_path))[0].lower()

    def score(m):
        name = m.label.lower()
        return ((m.member or "").endswith(ALL_PARTS), name == base, base in name or name in base, "avatar" in name or "_ui" in name or "model" in name, m.size)
    return sorted(models, key=score, reverse=True)


def watch_archive(path, member=""):
    #polls until the background thread finished reading the archive, then: 1 fbx -> import it,
    # several -> ask which one, password -> ask for it. timers ftw, no blocking the ui
    path = os.path.abspath(path)
    t0 = time.monotonic()

    def tick():
        st = state.get()
        if st is None or st.batch is not None:
            return None
        src = st.session.sources.get(path)
        if src is None:
            return None
        wm = bpy.context.window_manager.hjt_fbx_plus
        if src.status in ("PENDING", "LISTING"):
            if time.monotonic() - t0 > LISTING_TIMEOUT:
                wm.status_text = ""
                _popup("HJT FBX+", ["Reading %s took too long" % src.label])
                return None
            wm.status_text = "Reading %s..." % src.label
            return 0.15
        wm.status_text = ""
        if src.status == "LOCKED":
            with bpy.context.temp_override(**_ctx_override()):
                bpy.ops.hjt_fbx_plus.archive_password("INVOKE_DEFAULT", path=path, member=member,
                                                      retry=src.message.startswith("Wrong"))
            return None
        if src.status == "ERROR":
            _popup("Can't open %s" % src.label, [ln for ln in _wrap_text(src.message or "Unknown error", 70)])
            return None
        models = [m for m in st.session.models.values() if m.source_key == src.key]
        if not models:
            _popup(src.label, ["No FBX or UEModel files in this archive"], icon="INFO")
            return None
        if member:
            hit = next((m for m in models if m.member == member), None)
            if hit is not None:
                _start_one(st, hit.key)
                return None
        if len(models) == 1:
            _start_one(st, models[0].key)
            return None
        with bpy.context.temp_override(**_ctx_override()):
            bpy.ops.hjt_fbx_plus.pick_model("INVOKE_DEFAULT", path=path)
        return None

    bpy.app.timers.register(tick, first_interval=0.1)


def _wrap_text(text, width):
    out, cur = [], ""
    for w in (text or "").split():
        if cur and len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out[:6] or [""]


class HJTFBX_OT_import(bpy.types.Operator, ImportHelper):
    #1 model in, 1 model out. loose fbx or an fbx inside a zip / 7z / rar. no queue no folder scanning
    bl_idname = "hjt_fbx_plus.import_files"
    bl_label = "Import FBX/UEMODEL"
    bl_description = "Import one FBX or UEModel, or one model from a ZIP / 7Z / RAR archive, with HJT FBX+"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".fbx"
    #SKIP_SAVE or blender remembers the last file n invoke() below thinks its a drag n drop. that was the
    # "undo, click import, same model again" bug lol
    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default=FILTER, options={"HIDDEN"})
    profile: EnumProperty(name="Game Profile", items=profile_items,
                          description="Game profile for texture discovery and material reconstruction")
    password: StringProperty(name="Archive Password", subtype="PASSWORD", options={"SKIP_SAVE"},
                             description="Only for encrypted archives (kept in memory, never saved)")
    member: StringProperty(options={"HIDDEN", "SKIP_SAVE"})  #path inside the archive, skips the picker

    @classmethod
    def poll(cls, context):
        st = state.get()
        return st is not None and st.batch is None

    def invoke(self, context, event):
        try:
            self.profile = context.scene.hjt_fbx_plus.profile
        except (TypeError, AttributeError):
            pass
        if self.filepath:
            #dropped on the viewport. u already picked the file, no dialog, just go
            return self.execute(context)
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(self, "profile")
        s = context.scene.hjt_fbx_plus
        col.prop(s, "align_chains")
        col.prop(s, "material_mode")
        col.separator()
        col.prop(self, "password")
        layout.label(text="All options: 3D View > Sidebar > HJT FBX+", icon="INFO")

    def execute(self, context):
        st = state.get()
        if st is None:
            self.report({"ERROR"}, "HJT FBX+ is not initialized")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        if not path or not os.path.isfile(path):
            self.report({"WARNING"}, "Pick an .fbx, .uemodel, .zip, .7z or .rar file")
            return {"CANCELLED"}
        is_fbx = path.lower().endswith((".fbx", ".uemodel"))
        if not is_fbx and not is_archive_path(path):
            self.report({"WARNING"}, "HJT FBX+ imports .fbx / .uemodel files or .zip / .7z / .rar archives")
            return {"CANCELLED"}
        settings = context.scene.hjt_fbx_plus
        if self.profile and self.profile != settings.profile:
            settings.profile = self.profile
        st.session.clear()  #fresh every time, nothing piles up between imports
        st.session.add_paths([path], password=self.password or None, auto_analyze=False, options=snapshot(settings))
        wm = context.window_manager.hjt_fbx_plus
        if is_fbx:
            bpy.ops.hjt_fbx_plus.run("INVOKE_DEFAULT", sources=os.path.abspath(path))
        else:
            wm.status_text = "Reading %s..." % os.path.basename(path)
            watch_archive(path, self.member)
        return {"FINISHED"}


def _model_items(self, context):
    #dynamic enum strings MUST outlive the call or blender shows garbage (saw the same model twice lmao)
    # so the list is only rebuilt when the archive or its models change
    st = state.get()
    path = os.path.abspath(self.path)
    models = [m for m in st.session.models.values() if m.source_key == path] if st is not None else []
    key = (path, tuple(m.key for m in models))
    if _model_items_cache.get("key") == key:
        return _model_items_cache["items"]
    items = []
    for i, m in enumerate(_rank(models, self.path)):
        where = os.path.dirname(m.member or "")
        desc = "%s  (%s)" % (m.member or m.label, _size(m.size))
        items.append((m.key, m.label + (("   - " + where) if where else ""), desc, "FILE_3D", i))
    _model_items_cache["key"] = key
    _model_items_cache["items"] = items or [("NONE", "No models", "", "ERROR", 0)]
    return _model_items_cache["items"]


_model_items_cache = {}


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


class HJTFBX_OT_pick_model(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.pick_model"
    bl_label = "Pick a Model"
    bl_description = "The archive has more than one model, pick the one to import"
    bl_options = {"INTERNAL"}

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    choice: EnumProperty(name="Model", items=_model_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460, title="%s has several models" % os.path.basename(self.path),
                                                          confirm_text="Import")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "choice", expand=True)

    def execute(self, context):
        st = state.get()
        if st is None or self.choice in ("", "NONE"):
            return {"CANCELLED"}
        _start_one(st, self.choice)
        return {"FINISHED"}


class HJTFBX_OT_archive_password(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.archive_password"
    bl_label = "Archive Password"
    bl_description = "This archive is encrypted"
    bl_options = {"INTERNAL"}

    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    member: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    retry: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})
    password: StringProperty(name="Password", subtype="PASSWORD", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340, title="%s is encrypted" % os.path.basename(self.path),
                                                          confirm_text="Unlock")

    def draw(self, context):
        col = self.layout.column()
        if self.retry:
            r = col.row()
            r.alert = True
            r.label(text="Wrong password, try again", icon="ERROR")
        col.activate_init = True
        col.prop(self, "password", text="")

    def execute(self, context):
        st = state.get()
        if st is None or not self.password:
            return {"CANCELLED"}
        st.session.unlock(os.path.abspath(self.path), self.password, auto_analyze=False,
                          options=snapshot(context.scene.hjt_fbx_plus))
        watch_archive(self.path, self.member)
        return {"FINISHED"}


def _belongs(source_key, picked):
    #model came from this picked path: the file itself or something found inside a picked folder
    if source_key == picked:
        return True
    base = picked.rstrip("/\\")
    return source_key.startswith(base + os.sep) or source_key.startswith(base + "/")


class HJTFBX_OT_run(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.run"
    bl_label = "Import"
    bl_description = "Run the import (Esc cancels)"
    bl_options = {"REGISTER", "UNDO"}

    sources: StringProperty(options={"HIDDEN", "SKIP_SAVE"})  #newline separated paths: import just these

    @classmethod
    def poll(cls, context):
        st = state.get()
        return st is not None and st.batch is None

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        st = state.get()
        self._st = st
        self.opts = snapshot(context.scene.hjt_fbx_plus)
        self.avail = engines.availability()
        if not any(self.avail.values()):
            self.report({"ERROR"}, "No FBX importer is enabled. Enable the 'FBX format' add-on in Preferences > Add-ons.")
            return {"CANCELLED"}
        self.only = [x for x in self.sources.split("\n") if x]
        self.done_keys = set()
        self.queue = []
        self._refill()
        if not self.queue and not self._waiting_for_listing():
            self.report({"WARNING"}, "Nothing to import")
            return {"CANCELLED"}
        self.total = max(1, len(self.queue))
        self.index = 0
        self.stage = "NEXT"
        self.task = None
        self.prep = None
        self.res = None
        self.cancelled = False
        self.current = None
        self.started = time.monotonic()
        self.run_report = RunReport(self.opts, _addon_version(), bpy.app.version_string)
        self.images = img_mod.ImageCache(reuse_existing=self.opts.get("reuse_images", True))
        self.extracted = []
        self.prefetched = set()
        st.batch = self
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        if bpy.app.background or context.window is None:
            return self._run_blocking(context)
        self.timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        self._status(context, "Starting...")
        return {"RUNNING_MODAL"}

    def _run_blocking(self, context):
        #no window / background blender (scripts, command line): same steps, just in a loop.
        # also what the automated tests drive
        st = self._st
        while True:
            st.tasks.poll()  #deliver archive listing callbacks so new models show up
            if self.stage in ("PREPARING", "PREFETCH") and self.task is not None:
                try:
                    self.task.future.result()
                except Exception:  # noqa: BLE001
                    pass  #_step records the failure
            elif self.stage == "NEXT" and self._waiting_for_listing():
                time.sleep(0.05)
            try:
                r = self._step(context)
            except Exception as exc:  # noqa: BLE001
                log.exception("batch step failed")
                self._fail_current(str(exc) or type(exc).__name__)
                self.stage = "NEXT"
                continue
            if r != {"RUNNING_MODAL"}:
                return r

    #queue
    def _waiting_for_listing(self):
        return any(src.status == "LISTING" for src in self._st.session.sources.values())

    def _refill(self):
        #checked models, or with `sources` every model of those files. imported ones get unchecked
        # after import, so whatever is checked here the user wants (again)
        queued = {q.key for q in self.queue}
        for m in self._st.session.models.values():
            if m.key in self.done_keys or m.key in queued:
                continue
            if self.only:
                if not any(_belongs(m.source_key, x) for x in self.only):
                    continue
            elif not m.selected:
                continue
            self.queue.append(m)
            queued.add(m.key)

    def _status(self, context, text):
        s = context.window_manager.hjt_fbx_plus
        frac = min(1.0, (self.index + self._stage_frac()) / max(1, self.total))
        s.progress = frac
        s.status_text = text
        s.running = True
        try:
            context.window_manager.progress_update(int(frac * 1000))
            context.workspace.status_text_set("HJT FBX+  %d/%d  %s   (Esc to cancel)" % (min(self.index + 1, self.total), self.total, text))
        except AttributeError:
            pass
        compat.tag_redraw_all(("VIEW_3D",))

    def _stage_frac(self):
        if self.stage == "PREPARING" and self.task is not None:
            return 0.5 * self.task.progress
        return {"IMPORT": 0.55, "FINISH": 0.8}.get(self.stage, 0.0)

    #modal
    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS" and not self.cancelled:
            self.cancelled = True
            if self.task is not None:
                self.task.cancel.set()
            self._status(context, "Cancelling after the current step...")
        if event.type != "TIMER":
            return {"PASS_THROUGH"} if event.type in NAV_EVENTS else {"RUNNING_MODAL"}
        try:
            return self._step(context)
        except Exception as exc:  # noqa: BLE001
            log.exception("batch step failed")
            self._fail_current(str(exc) or type(exc).__name__)
            self.stage = "NEXT"
            return {"RUNNING_MODAL"}

    def _step(self, context):
        st = self._st
        if self.stage == "NEXT":
            self._cleanup_model()
            if self.cancelled:
                return self._finish(context)
            self._refill()
            pending = [m for m in self.queue if m.key not in self.done_keys]
            if not pending:
                if self._waiting_for_listing():
                    self._status(context, "Reading archive...")
                    return {"RUNNING_MODAL"}
                return self._finish(context)
            self.total = max(self.total, len(self.done_keys) + len(pending))
            m = pending[0]
            self.current = m
            src = st.session.sources.get(m.source_key)
            if src is not None and src.one_pass and src.key not in self.prefetched:
                #solid / external tool archive: pull what every queued model from it needs in one go
                self.prefetched.add(src.key)
                todo = [q for q in pending if q.source_key == src.key and q.member]
                if len(todo) > 1:
                    self.task = st.tasks.submit(pipeline.prefetch, st.services, src.path, src.password,
                                                [q.member for q in todo], [q.plans for q in todo],
                                                key=("prefetch", src.key), label="Reading %s" % src.label)
                    self.stage = "PREFETCH"
                    self._status(context, "Reading %d models from %s" % (len(todo), src.label))
                    return {"RUNNING_MODAL"}
            source = pipeline.Source(m.path, m.member, src.password if src else None, m.label)
            self.diag = Diagnostics()
            self.t0 = time.monotonic()
            self.task = st.tasks.submit(pipeline.prepare_job, source, self.opts, st.services, self.avail,
                                        key=("import", m.key), label="Preparing %s" % m.label)
            self.stage = "PREPARING"
            self._status(context, "Preparing %s" % m.label)
            return {"RUNNING_MODAL"}
        if self.stage == "PREFETCH":
            if not self.task.done:
                self._status(context, self.task.message or "Reading archive")
                return {"RUNNING_MODAL"}
            exc = self.task.future.exception()
            if exc is None:
                self.extracted.extend(self.task.future.result() or [])
            elif not self.cancelled:
                #not fatal, every model can still extract its own files. just slower
                log.warning("archive prefetch failed: %s", exc)
            self.task = None
            self.stage = "NEXT"
            return {"RUNNING_MODAL"}
        if self.stage == "PREPARING":
            if not self.task.done:
                self._status(context, "%s: %s" % (self.current.label, self.task.message or "preparing"))
                return {"RUNNING_MODAL"}
            exc = self.task.future.exception()
            if exc is not None:
                self._fail_current(str(exc) or type(exc).__name__)
                self.stage = "NEXT"
                return {"RUNNING_MODAL"}
            self.prep = self.task.future.result()
            self.extracted.extend(self.prep.extracted)
            for n in self.prep.notes:
                self.diag.info(n, self.current.label)
            self.stage = "IMPORT"
            self._status(context, "Importing %s" % self.current.label)
            return {"RUNNING_MODAL"}
        if self.stage == "IMPORT":
            self.res = pipeline.import_job(context, self.prep, self.opts, self.diag)
            if not self.res.ok:
                self._fail_current(self.res.error)
                self.stage = "NEXT"
                return {"RUNNING_MODAL"}
            self.stage = "FINISH"
            self._status(context, "Building materials for %s" % self.current.label)
            return {"RUNNING_MODAL"}
        if self.stage == "FINISH":
            rep = pipeline.finish_job(context, self.prep, self.res, self.opts, self.diag, self.images, st.cache.session)
            self._record(self.current, "OK", rep)
            self.current.status = "IMPORTED"
            self.current.selected = False
            st.session.changed()
            self.stage = "NEXT"
            self.index += 1
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _record(self, m, status, rep=None, error=""):
        prep = self.prep
        entry = {
            "name": m.label, "status": status, "error": error,
            "source": m.path + (("::" + m.member) if m.member else ""),
            "profile": prep.profile.id if prep is not None and prep.profile else "",
            "engine": prep.engine_id if prep is not None else "",
            "timings": {k: round(v, 3) for k, v in (prep.timings.items() if prep is not None else [])},
            "fixes": dict(prep.prep_result.fixes) if prep is not None and prep.prep_result else {},
            "messages": list(self.diag.messages),
        }
        if prep is not None and prep.summary is not None:
            entry["issues"] = prep.summary.get("issues", [])
        if rep:
            entry.update({"materials": rep.get("materials", []), "stats": rep.get("stats", {}),
                          "categories": rep.get("categories", {}), "skinning": rep.get("skinning", {}),
                          "approximations": rep.get("approximations", 0)})
        entry["seconds"] = round(time.monotonic() - self.t0, 3)
        self.run_report.add_model(entry)
        self.done_keys.add(m.key)

    def _fail_current(self, error):
        m = self.current
        if m is None:
            return
        self.diag.error(error, m.label)
        self._record(m, "FAILED", error=error)
        m.status = "ERROR"
        m.message = error[:300]
        self._st.session.changed()
        self.index += 1
        if self.opts.get("stop_on_error"):
            self.cancelled = True

    def _cleanup_model(self):
        if self.prep is not None:
            self.prep.close()
            #the prepared fbx copy can be huge, drop it right away
            if self.prep.prepared_path and self.prep.prepared_path != self.prep.local_fbx:
                self._st.cache.release_files([self.prep.prepared_path])
        self.prep = None
        self.res = None
        self.task = None
        self.current = None

    def _finish(self, context):
        st = self._st
        wm = context.window_manager
        try:
            wm.event_timer_remove(self.timer)
        except (ValueError, AttributeError):
            pass
        wm.progress_end()
        try:
            context.workspace.status_text_set(None)
        except AttributeError:
            pass
        #nothing from the model stays behind: extracted files, prepared copies, the archive's model list
        st.cache.release_files(self.extracted)
        st.cache.cleanup_session()
        st.session.clear()
        self.run_report.finish(self.cancelled)
        d = self.run_report.to_dict()
        st.last_report = d
        try:
            st.history.save(self.run_report)
        except OSError as exc:
            log.warning("could not save history: %s", exc)
        st.batch = None
        s = wm.hjt_fbx_plus
        s.running = False
        s.progress = 1.0
        s.status_text = ""
        s.last_summary = self.run_report.summary_line()
        sync.sync_all(context)
        sync.sync_history(s, st)
        t = self.run_report.totals()
        level = {"ERROR"} if t["failed"] and not t["ok"] else ({"WARNING"} if t["failed"] or t["warnings"] else {"INFO"})
        self.report(level, "HJT FBX+: " + self.run_report.summary_line())
        return {"FINISHED"} if t["ok"] else {"CANCELLED"}

    def cancel(self, context):
        #blender kills modal ops on file load etc. leave everything consistent
        self.cancelled = True
        if self.task is not None:
            self.task.cancel.set()
        try:
            self._finish(context)
        except Exception:  # noqa: BLE001
            self._st.batch = None


class HJTFBX_OT_cancel(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.cancel"
    bl_label = "Cancel"
    bl_description = "Stop background work (archive reading, analysis). A running import stops with Esc"

    def execute(self, context):
        st = state.get()
        if st is not None:
            st.tasks.cancel()
            if st.batch is not None:
                st.batch.cancelled = True
        return {"FINISHED"}


CLASSES = (HJTFBX_OT_import, HJTFBX_OT_pick_model, HJTFBX_OT_archive_password, HJTFBX_OT_run, HJTFBX_OT_cancel)
