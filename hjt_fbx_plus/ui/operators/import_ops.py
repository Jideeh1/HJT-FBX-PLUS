# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#import operators: the file browser one (also drag n drop), add folder, n the modal batch import

from __future__ import annotations

import os
import time

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ... import state
from ...blender import compat, engines, images as img_mod, pipeline
from ...blender.report import RunReport
from ...core.log import Diagnostics, log
from .. import sync
from ..props import profile_items, snapshot

FILTER = "*.fbx;*.zip;*.7z;*.rar;*.001"
NAV_EVENTS = {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN",
              "TRACKPADZOOM", "TIMER_REPORT", "WINDOW_DEACTIVATE"}


def _addon_version():
    return state.addon_version()


class HJTFBX_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "hjt_fbx_plus.import_files"
    bl_label = "HJT FBX+"
    bl_description = "Import FBX files or ZIP / 7Z / RAR archives containing FBX models with HJT FBX+"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".fbx"
    filter_glob: StringProperty(default=FILTER, options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    preview: BoolProperty(name="Preview Before Import", default=True,
                          description="Add the files to the HJT FBX+ queue (3D View sidebar) so you can inspect and pick models first")
    profile: EnumProperty(name="Game Profile", items=profile_items,
                          description="Game profile for texture discovery and material reconstruction")
    password: StringProperty(name="Archive Password", subtype="PASSWORD", options={"SKIP_SAVE"},
                             description="Password for encrypted archives (kept in memory only)")

    def invoke(self, context, event):
        st = state.get()
        try:
            self.profile = context.scene.hjt_fbx_plus.profile
        except (TypeError, AttributeError):
            pass
        if self.filepath or self.files:
            #came from drag n drop (FileHandler), ask the 2 questions that matter then go
            return context.window_manager.invoke_props_dialog(self, width=360)
        return ImportHelper.invoke(self, context, event) if st is not None else {"CANCELLED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(self, "profile")
        col.prop(self, "preview")
        col.prop(self, "password")
        box = layout.box()
        box.label(text="All options: 3D View > Sidebar > HJT FBX+", icon="INFO")

    def _paths(self):
        out = []
        if self.files and any(f.name for f in self.files):
            for f in self.files:
                if f.name:
                    out.append(os.path.join(self.directory or os.path.dirname(self.filepath), f.name))
        elif self.filepath:
            out.append(self.filepath)
        return [p for p in out if os.path.exists(p)]

    def execute(self, context):
        st = state.get()
        if st is None:
            self.report({"ERROR"}, "HJT FBX+ is not initialized")
            return {"CANCELLED"}
        paths = self._paths()
        if not paths:
            self.report({"WARNING"}, "Nothing to import")
            return {"CANCELLED"}
        settings = context.scene.hjt_fbx_plus
        if self.profile and self.profile != settings.profile:
            settings.profile = self.profile
        opts = snapshot(settings)
        #direct import parses everything in prepare anyway, analyzing too would just do it twice
        st.session.add_paths(paths, password=self.password or None, auto_analyze=self.preview and state.auto_analyze(),
                             options=opts)
        _reveal_sidebar(context)
        if self.preview:
            self.report({"INFO"}, "Added to the HJT FBX+ queue - review the models in the sidebar, then press Import")
            return {"FINISHED"}
        #no preview: import exactly what was picked, even if it was in the queue already (or imported
        # before). archives list in the background, the batch waits for their models to show up
        bpy.ops.hjt_fbx_plus.run("INVOKE_DEFAULT", sources="\n".join(sorted({os.path.abspath(p) for p in paths})))
        return {"FINISHED"}


def _reveal_sidebar(context):
    try:
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == "UI" and hasattr(region, "active_panel_category"):
                        try:
                            region.active_panel_category = "HJT FBX+"
                        except (TypeError, AttributeError, ValueError):
                            pass
                break
    except AttributeError:
        pass


class HJTFBX_OT_add_folder(bpy.types.Operator, ImportHelper):
    bl_idname = "hjt_fbx_plus.add_folder"
    bl_label = "Add Folder"
    bl_description = "Scan a folder (and its subfolders) for FBX files and archives and add them to the queue"
    bl_options = {"REGISTER"}

    filename_ext = ""
    use_filter_folder = True
    directory: StringProperty(subtype="DIR_PATH")
    filter_glob: StringProperty(default=FILTER, options={"HIDDEN"})

    def execute(self, context):
        st = state.get()
        d = self.directory or os.path.dirname(self.filepath)
        if not d or not os.path.isdir(d):
            self.report({"ERROR"}, "Pick a folder")
            return {"CANCELLED"}
        added = st.session.add_folder(d, auto_analyze=state.auto_analyze(), options=snapshot(context.scene.hjt_fbx_plus))
        self.report({"INFO"}, "Scanning %s (%d FBX added, archives are read in the background)" % (os.path.basename(d.rstrip("/\\")), len(added)))
        return {"FINISHED"}


def _belongs(source_key, picked):
    #model came from this picked path: the file itself or something found inside a picked folder
    if source_key == picked:
        return True
    base = picked.rstrip("/\\")
    return source_key.startswith(base + os.sep) or source_key.startswith(base + "/")


class HJTFBX_OT_run(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.run"
    bl_label = "Import Selected"
    bl_description = "Import every checked model in the queue (Esc cancels after the current model)"
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
            self.report({"WARNING"}, "No models are checked for import")
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
                    self._status(context, "Waiting for archives to be read...")
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
        if st.cache.policy == "AFTER_IMPORT":
            st.cache.release_files(self.extracted)
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


CLASSES = (HJTFBX_OT_import, HJTFBX_OT_add_folder, HJTFBX_OT_run, HJTFBX_OT_cancel)
