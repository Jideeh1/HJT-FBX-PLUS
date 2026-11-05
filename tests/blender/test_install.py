# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#installs the BUILT zip the way users do: as an extension (4.2+) n as a legacy addon, runs an import,
#uninstalls. test_addon copies the source folder, this one catches packaging stuff (blender deletes
# bl_info from extensions, manifest one folder down, missing files...)
#   python tools/build.py
#   blender -b --factory-startup --python tests/blender/test_install.py -- dist/hjt_fbx_plus-1.0.0.zip <archive or fbx>

import os
import shutil
import sys
import traceback

import bpy
import addon_utils

ZIP, SAMPLE = (os.path.abspath(a) for a in sys.argv[sys.argv.index("--") + 1:][:2])
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def manifest_version():
    import zipfile
    with zipfile.ZipFile(ZIP) as zf:
        txt = zf.read("hjt_fbx_plus/blender_manifest.toml").decode("utf-8")
    for line in txt.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def exercise(name, label):
    check(name in bpy.context.preferences.addons, "%s: enabled as %s" % (label, name))
    check(hasattr(bpy.ops.hjt_fbx_plus, "import_files") and hasattr(bpy.types, "HJTFBX_PT_import"),
          "%s: operators and panels registered" % label)
    state = sys.modules.get(name + ".state")
    st = state.get() if state is not None else None
    check(st is not None and state.prefs() is not None, "%s: add-on state and preferences" % label)
    if st is None:
        return
    check(len(st.registry.profiles) >= 6, "%s: built-in profiles (%d)" % (label, len(st.registry.profiles)))
    check(state.addon_version() == manifest_version(), "%s: version %r" % (label, state.addon_version()))
    n = len(bpy.data.objects)
    r = bpy.ops.hjt_fbx_plus.import_files(filepath=SAMPLE, preview=False)
    rep = st.last_report
    check(r == {"FINISHED"} and rep is not None and rep["totals"]["ok"] > 0 and len(bpy.data.objects) > n,
          "%s: import %s" % (label, rep["summary"] if rep else r))
    check(rep is not None and rep.get("addon_version") == manifest_version(), "%s: version in report" % label)


def main():
    print("== HJT FBX+ install test on Blender %s with %s" % (bpy.app.version_string, os.path.basename(ZIP)))
    if bpy.app.version >= (4, 2, 0) and hasattr(bpy.ops.extensions, "package_install_files"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
            check(r == {"FINISHED"}, "extension: install from disk")
        except RuntimeError as exc:
            check(False, "extension: install from disk (%s)" % exc)
        exercise("bl_ext.user_default.hjt_fbx_plus", "extension")
        try:
            idx = [x.module for x in bpy.context.preferences.extensions.repos].index("user_default")
            r = bpy.ops.extensions.package_uninstall(repo_index=idx, pkg_id="hjt_fbx_plus")
            check(r == {"FINISHED"} and not hasattr(bpy.types, "HJTFBX_PT_import"), "extension: uninstall")
        except (RuntimeError, ValueError) as exc:
            check(False, "extension: uninstall (%s)" % exc)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        r = bpy.ops.preferences.addon_install(filepath=ZIP, overwrite=True)
        check(r == {"FINISHED"}, "legacy: install")
    except RuntimeError as exc:
        check(False, "legacy: install (%s)" % exc)
    addon_utils.modules_refresh()
    addon_utils.enable("hjt_fbx_plus", default_set=True, handle_error=lambda e: traceback.print_exc())
    exercise("hjt_fbx_plus", "legacy")
    addon_utils.disable("hjt_fbx_plus", default_set=True)
    check(not hasattr(bpy.types, "HJTFBX_PT_import"), "legacy: disabled cleanly")
    #not preferences.addon_remove: it redraws an area that doesnt exist in background mode
    mod_dir = os.path.join(bpy.utils.user_resource("SCRIPTS", path="addons"), "hjt_fbx_plus")
    shutil.rmtree(mod_dir, ignore_errors=True)
    print("== %s (%d failures)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 0 if not FAILS else 1


try:
    code = main()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    code = 2
sys.stdout.flush()
os._exit(code)
