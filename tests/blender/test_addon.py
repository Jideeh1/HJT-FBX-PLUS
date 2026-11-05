# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#end to end add-on test, runs INSIDE blender (or the bpy module):
#   blender -b --factory-startup --python tests/blender/test_addon.py -- <repo> <archive or fbx> [...]
# installs the add-on into the user scripts folder, enables it, drives the operators like a user
# would, draws every panel through a strict fake layout, then unregisters. exit code 1 on failure

import os
import shutil
import sys
import time
import traceback

import bpy
import addon_utils

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
REPO = os.path.abspath(ARGS[0]) if ARGS else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INPUTS = ARGS[1:]
FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        FAILS.append(msg)


def install():
    dst_root = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
    dst = os.path.join(dst_root, "hjt_fbx_plus")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(os.path.join(REPO, "hjt_fbx_plus"), dst, ignore=shutil.ignore_patterns("__pycache__"))
    #the bpy module doesnt put the user addons folder on sys.path like blender.exe does at startup
    try:
        bpy.utils.refresh_script_paths()
    except Exception:  # noqa: BLE001
        pass
    if dst_root not in sys.path:
        sys.path.append(dst_root)
    addon_utils.modules_refresh()
    mod = addon_utils.enable("hjt_fbx_plus", default_set=True, handle_error=lambda e: traceback.print_exc())
    return mod


def pump(seconds=60.0, until=None):
    import hjt_fbx_plus.state as state
    t = time.monotonic()
    while time.monotonic() - t < seconds:
        state._tick()
        st = state.get()
        if until is not None and until(st):
            return True
        if until is None and not st.tasks.active():
            return True
        time.sleep(0.05)
    return False


#strict fake layout: every prop / operator / list / menu name gets validated

class FakeOp:
    def __init__(self, idname):
        mod, name = idname.split(".")
        self._rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()

    def __setattr__(self, k, v):
        if k.startswith("_"):
            return object.__setattr__(self, k, v)
        if k not in self._rna.properties:
            raise AttributeError("operator %s has no property %s" % (self._rna.identifier, k))


class FakeLayout:
    def __init__(self, errors):
        object.__setattr__(self, "_errors", errors)

    def __setattr__(self, k, v):
        if k not in ("scale_y", "scale_x", "enabled", "active", "alignment", "use_property_split",
                     "use_property_decorate", "alert", "emboss", "operator_context"):
            self._errors.append("layout has no attribute %s" % k)

    def _child(self, *a, **k):
        return FakeLayout(self._errors)

    row = column = box = split = grid_flow = column_flow = _child

    def label(self, text="", icon="NONE", **k):
        self._icon(icon)

    def _icon(self, icon):
        if icon and icon not in _ICONS:
            self._errors.append("unknown icon %s" % icon)

    def separator(self, factor=1.0, **k):
        pass

    def prop(self, data, name, **k):
        data = getattr(data, "_real", data)
        if data is None or name not in data.bl_rna.properties:
            self._errors.append("prop %s missing on %s" % (name, getattr(data, "bl_rna", data)))
        self._icon(k.get("icon", "NONE"))

    def prop_search(self, data, name, sdata, sprop, **k):
        self.prop(data, name)

    def operator(self, idname, **k):
        self._icon(k.get("icon", "NONE"))
        try:
            return FakeOp(idname)
        except Exception as exc:  # noqa: BLE001
            self._errors.append("operator %s: %s" % (idname, exc))
            return FakeLayout([])

    def operator_menu_enum(self, idname, prop, **k):
        op = self.operator(idname)
        if isinstance(op, FakeOp) and prop not in op._rna.properties:
            self._errors.append("operator %s has no enum %s" % (idname, prop))

    def template_list(self, listtype, list_id, data, prop, adata, aprop, **k):
        if not hasattr(bpy.types, listtype):
            self._errors.append("list type %s not registered" % listtype)
        for d, p in ((data, prop), (adata, aprop)):
            if p not in d.bl_rna.properties:
                self._errors.append("list prop %s missing" % p)

    def menu(self, name, **k):
        if not hasattr(bpy.types, name):
            self._errors.append("menu %s not registered" % name)
        self._icon(k.get("icon", "NONE"))

    def progress(self, **k):
        pass

    def __getattr__(self, k):
        self._errors.append("layout method %s not faked" % k)
        return lambda *a, **kw: FakeLayout(self._errors)


_ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())


def draw_all_panels():
    errors = []
    for name in dir(bpy.types):
        if not name.startswith("HJTFBX_PT_"):
            continue
        cls = getattr(bpy.types, name)
        self = type("S", (), {"layout": FakeLayout(errors), "bl_rna": None})()
        for fn in ("draw_header", "draw_header_preset", "draw"):
            f = getattr(cls, fn, None)
            if f is None:
                continue
            try:
                f(self, bpy.context)
            except Exception as exc:  # noqa: BLE001
                errors.append("%s.%s raised %s: %s" % (name, fn, type(exc).__name__, exc))
                traceback.print_exc()
    prefs_cls = bpy.types.AddonPreferences.bl_rna  # noqa: F841
    addon = bpy.context.preferences.addons.get("hjt_fbx_plus")
    if addon is not None:
        class Proxy:
            def __init__(self, real, layout):
                object.__setattr__(self, "_real", real)
                object.__setattr__(self, "layout", layout)

            def __getattr__(self, k):
                return getattr(self._real, k)

        self = Proxy(addon.preferences, FakeLayout(errors))
        try:
            type(addon.preferences).draw(self, bpy.context)
        except Exception as exc:  # noqa: BLE001
            errors.append("prefs draw raised %s: %s" % (type(exc).__name__, exc))
            traceback.print_exc()
    return errors


def main():
    print("== HJT FBX+ add-on test on Blender %s" % bpy.app.version_string)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = install()
    check(mod is not None, "add-on enabled")
    check(hasattr(bpy.types.Scene, "hjt_fbx_plus"), "scene settings registered")
    check(hasattr(bpy.ops.hjt_fbx_plus, "import_files"), "import operator registered")
    import hjt_fbx_plus.state as state
    st = state.get()
    check(st is not None and len(st.registry.profiles) >= 6, "profiles loaded (%d)" % (len(st.registry.profiles) if st else 0))
    errors = draw_all_panels()
    check(not errors, "panels draw with empty queue %s" % (errors[:5] if errors else ""))

    if INPUTS:
        r = bpy.ops.hjt_fbx_plus.import_files(filepath=INPUTS[0], files=[{"name": os.path.basename(p)} for p in INPUTS],
                                             directory=os.path.dirname(INPUTS[0]), preview=True)
        check(r == {"FINISHED"}, "import_files (preview) %s" % r)
        ok = pump(120, until=lambda s: s.session.models and all(m.status not in ("PENDING", "ANALYZING") for m in s.session.models.values())
                  and not any(x.status == "LISTING" for x in s.session.sources.values()))
        check(ok, "archives listed + models analyzed")
        models = list(st.session.models.values())
        check(len(models) >= len(INPUTS), "%d models in queue" % len(models))
        for m in models:
            check(m.status in ("READY", "WARN"), "model %s analyzed: %s (%s) profile=%s textures %d/%d/%d" % (
                m.label, m.status, m.message, m.profile_id, m.textures_faithful, m.textures_inferred, m.textures_missing))
        state._tick()
        errors = draw_all_panels()
        check(not errors, "panels draw with a full queue %s" % (errors[:5] if errors else ""))

        r = bpy.ops.hjt_fbx_plus.run()
        check(r == {"FINISHED"}, "batch import operator %s" % r)
        rep = st.last_report
        check(rep is not None and rep["totals"]["ok"] == len(models), "report: %s" % (rep["summary"] if rep else None))
        check(any(o.type == "ARMATURE" for o in bpy.data.objects), "armature imported")
        mats = [m for m in bpy.data.materials if m.get("hjt_fbx")]
        check(len(mats) > 0, "%d materials rebuilt by HJT FBX+" % len(mats))
        imgs = list(bpy.data.images)
        check(imgs and all(i.packed_file is not None for i in imgs), "all %d images packed" % len(imgs))
        check(not [o for o in bpy.data.objects if "hjt_fbx_uid" in o.keys()], "no internal uid props left")
        check(len(st.history.entries()) >= 1, "history saved")
        check(all(not m.selected for m in st.session.models.values() if m.status == "IMPORTED"), "imported models get unchecked")
        #one shot import (file browser w preview off, or a script) of something already in the queue
        first = os.path.abspath(INPUTS[0])
        n_first = sum(1 for m in st.session.models.values() if m.source_key == first)
        r = bpy.ops.hjt_fbx_plus.import_files(filepath=INPUTS[0], preview=False)
        rep2 = st.last_report
        check(r == {"FINISHED"} and rep2 is not rep and rep2["totals"]["ok"] == n_first,
              "one-shot re-import of %s: %s" % (os.path.basename(first), rep2["summary"] if rep2 else None))
        state._tick()
        errors = draw_all_panels()
        check(not errors, "panels draw after import %s" % (errors[:5] if errors else ""))

        #material tools
        target = next(o for o in bpy.data.objects if o.type == "MESH" and o.active_material and o.active_material.get("hjt_fbx"))
        for o in bpy.context.view_layer.objects:
            o.select_set(o == target)
        bpy.context.view_layer.objects.active = target
        r = bpy.ops.hjt_fbx_plus.rebuild_materials(template="PBR")
        check(r == {"FINISHED"} and target.active_material["hjt_fbx"]["template"] == "PBR", "rebuild materials -> PBR")
        r = bpy.ops.hjt_fbx_plus.rebuild_materials(template="TOON")
        check(r == {"FINISHED"} and target.active_material["hjt_fbx"]["template"] == "TOON", "rebuild materials -> TOON")
        r = bpy.ops.hjt_fbx_plus.report_open()
        check(r == {"FINISHED"} and "HJT FBX+ Report" in bpy.data.texts, "report opened as text")

    #profiles
    s = bpy.context.window_manager.hjt_fbx_plus
    from hjt_fbx_plus.ui import sync
    sync.sync_profiles(s, st)
    s.profiles_index = [p.pid for p in s.profiles].index("afk_journey")
    r = bpy.ops.hjt_fbx_plus.profile_duplicate()
    check(r == {"FINISHED"}, "duplicate profile")
    pid = s.profiles[s.profiles_index].pid
    check(pid != "afk_journey" and pid in st.registry.profiles, "duplicated as %s" % pid)
    pe = s.profile_edit
    check(pe.loaded_id == pid, "editor loaded the copy")
    pe.name = "AFK Test Copy"
    pe.template = "PBR"
    bpy.ops.hjt_fbx_plus.profile_rule(target="slot_rules", action="ADD")
    pe.slot_rules[-1].slot = "emission"
    pe.slot_rules[-1].pattern = "_glow$"
    r = bpy.ops.hjt_fbx_plus.profile_edit_save()
    check(r == {"FINISHED"}, "save edited profile")
    p = st.registry.get(pid)
    check(p.name == "AFK Test Copy" and p.material_default["template"] == "PBR", "edited values persisted")
    check(any(r_.get("pattern") == "_glow$" for r_ in p.raw["textures"]["slots"]), "new slot rule persisted")
    exp = os.path.join(bpy.app.tempdir or "/tmp", "hjt_profile_test.json")
    r = bpy.ops.hjt_fbx_plus.profile_export(filepath=exp)
    check(r == {"FINISHED"} and os.path.exists(exp), "export profile")
    r = bpy.ops.hjt_fbx_plus.profile_delete()
    check(r == {"FINISHED"} and pid not in st.registry.profiles, "delete profile")
    r = bpy.ops.hjt_fbx_plus.profile_import(filepath=exp)
    check(r == {"FINISHED"} and pid in st.registry.profiles, "import profile back")
    s.profiles_index = [x.pid for x in s.profiles].index(pid)
    bpy.ops.hjt_fbx_plus.profile_delete()

    #presets + unregister / re-register
    pname = "HJT Test Preset %d" % os.getpid()
    r = bpy.ops.hjt_fbx_plus.preset_add(name=pname)
    check(r == {"FINISHED"}, "preset add")
    pdir = bpy.utils.user_resource("SCRIPTS", path=os.path.join("presets", "hjt_fbx_plus", "import"))
    pfile = os.path.join(pdir, bpy.path.clean_name(pname) + ".py")
    check(os.path.isfile(pfile), "preset file written")
    if os.path.isfile(pfile):
        os.remove(pfile)  #blender 5 refuses to overwrite presets, dont leave junk for the next run
    addon_utils.disable("hjt_fbx_plus", default_set=True)
    check(not hasattr(bpy.types.Scene, "hjt_fbx_plus") and state.get() is None, "unregistered cleanly")
    addon_utils.enable("hjt_fbx_plus", default_set=True)
    check(state.get() is not None, "re-enabled")
    addon_utils.disable("hjt_fbx_plus", default_set=True)
    print("== %s (%d failures)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)
