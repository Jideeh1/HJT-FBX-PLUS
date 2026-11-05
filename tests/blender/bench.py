# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#performance numbers. stock blender import vs the hjt pipeline, plus big archives
#   python tests/make_bench_assets.py -- <bench dir>        (once, takes ~3 min)
#   blender -b --factory-startup --python tests/blender/bench.py -- <repo> <bench dir> [--profile] [--archives-only]
#prints a table + writes <bench dir>/results.json

import cProfile
import io
import json
import os
import pstats
import resource
import shutil
import subprocess
import sys
import time
import zipfile

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
REPO, BENCH = os.path.abspath(ARGS[0]), os.path.abspath(ARGS[1])
PROFILE = "--profile" in ARGS
sys.path.insert(0, REPO)

from hjt_fbx_plus.blender import engines, images, pipeline, session  # noqa: E402
from hjt_fbx_plus.core.cache import CacheManager  # noqa: E402
from hjt_fbx_plus.core.log import Diagnostics  # noqa: E402
from hjt_fbx_plus.core.options import defaults  # noqa: E402
from hjt_fbx_plus.core.tasks import Task  # noqa: E402
from hjt_fbx_plus.profiles.registry import Registry  # noqa: E402

TMP = os.path.join(BENCH, "_work")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)
CACHE = CacheManager(root=os.path.join(TMP, "cache"))
SVC = pipeline.Services(Registry(), CACHE)
AVAIL = engines.availability()
ROWS = []


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def row(name, secs, note=""):
    ROWS.append({"case": name, "seconds": round(secs, 3), "peak_rss_mb": round(rss_mb()), "note": note})
    print("BENCH %-58s %8.2f s   %s" % (name, secs, note), flush=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.actions, bpy.data.armatures):
        for x in list(blk):
            blk.remove(x)


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


def counts():
    verts = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == "MESH")
    return "%d obj, %d verts, %d mats, %d imgs, %d actions" % (
        len(bpy.data.objects), verts, len(bpy.data.materials), len(bpy.data.images), len(bpy.data.actions))


def hjt(path, member=None, password=None, keep=False, **over):
    if not keep:
        reset()
    opts = defaults()
    opts.update(over)
    src = pipeline.Source(path, member, password)
    t0 = time.perf_counter()
    prep = pipeline.prepare_job(None, src, opts, SVC, AVAIL)
    t1 = time.perf_counter()
    diag = Diagnostics()
    res = pipeline.import_job(bpy.context, prep, opts, diag)
    t2 = time.perf_counter()
    if not res.ok:
        prep.close()
        raise RuntimeError(res.error)
    pipeline.finish_job(bpy.context, prep, res, opts, diag, images.ImageCache(), CACHE.session)
    t3 = time.perf_counter()
    prep.close()
    return t1 - t0, t2 - t1, t3 - t2, diag


def analyze(path, member=None, password=None, **over):
    opts = defaults()
    opts.update(over)
    task = Task(("bench",), "bench", None, None)
    return timed(lambda: session._analyze_model(task, path, member, password, SVC, opts))


def heavy(reps=3):
    #interleaved n median of 3, single shots on a busy box r basically dice rolls
    path = os.path.join(BENCH, "heavy", "Heavy.fbx")
    size = os.path.getsize(path) / 1e6
    print("== Heavy.fbx %.1f MB" % size)
    (_r, secs) = analyze(path)
    row("Heavy: preview analysis (parse + texture plan)", secs)
    engs = [e for e in ("PYTHON", "CPP") if AVAIL.get(e)]
    got = {}
    note = {}
    for _ in range(reps):
        reset()
        _o, secs = timed(lambda: bpy.ops.import_scene.fbx(filepath=path))
        got.setdefault("py", []).append(secs)
        note["py"] = counts()
        if AVAIL.get("CPP"):
            reset()
            _o, secs = timed(lambda: bpy.ops.wm.fbx_import(filepath=path))
            got.setdefault("cpp", []).append(secs)
            note["cpp"] = counts()
        for eng in engs:
            p, i, f, _diag = hjt(path, engine=eng)
            got.setdefault(eng, []).append((p + i + f, p, i, f))
            note[eng] = counts()

    def med(vals):
        return sorted(vals)[len(vals) // 2]

    row("Heavy: stock import_scene.fbx (Python importer)", med(got["py"]), note["py"])
    if "cpp" in got:
        row("Heavy: stock wm.fbx_import (C++ importer)", med(got["cpp"]), note["cpp"])
    for eng in engs:
        runs = got[eng]
        row("Heavy: HJT FBX+ [%s] total" % eng, med([r[0] for r in runs]), note[eng])
        row("  prepare (parse, sanitize, rewrite)", med([r[1] for r in runs]))
        row("  importer", med([r[2] for r in runs]))
        row("  finish (materials, textures, post)", med([r[3] for r in runs]))
    if PROFILE:
        reset()
        pr = cProfile.Profile()
        pr.enable()
        hjt(path, engine="PYTHON")
        pr.disable()
        s = io.StringIO()
        st = pstats.Stats(pr, stream=s).sort_stats("cumulative")
        st.print_stats("hjt_fbx_plus", 30)
        print(s.getvalue())


def build_archives():
    src = os.path.join(BENCH, "many")
    out = {}
    z = os.path.join(TMP, "many.zip")
    t = time.perf_counter()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for root, _dirs, files in os.walk(src):
            for f in sorted(files):
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, src))
    print("built zip in %.1fs (%.0f MB)" % (time.perf_counter() - t, os.path.getsize(z) / 1e6))
    out["ZIP"] = z
    sz = shutil.which("7z") or shutil.which("7zz")
    if sz:
        for label, extra in (("7Z solid LZMA2", []), ("7Z AES-256 (headers encrypted)", ["-phjt-bench", "-mhe=on"])):
            a = os.path.join(TMP, "many_%d.7z" % len(out))
            subprocess.run([sz, "a", "-bd", "-mx1", "-mmt=4", a, "."] + extra, cwd=src, stdout=subprocess.DEVNULL, check=True)
            out[label] = a
    rar = shutil.which("rar")
    if rar:
        #rar can only be made w the real rar tool, reading it goes through whatever extractor is installed
        a = os.path.join(TMP, "many_solid.rar")
        subprocess.run([rar, "a", "-idq", "-r", "-s", "-m1", a, "."], cwd=src, check=True)
        out["RAR5 solid (external tool)"] = a
    return out


def archives():
    arcs = build_archives()
    for label, path in arcs.items():
        pw = "hjt-bench" if "AES" in label else None
        print("== %s %.0f MB" % (label, os.path.getsize(path) / 1e6))
        SVC.forget_archive(path)
        (reader, aidx, _tidx), secs = timed(lambda: SVC.archive(path, pw))
        st = aidx.stats()
        row("%s: open + index" % label, secs, "%d files, %d fbx, %d images" % (st["files"], st["fbx"], st["images"]))
        member = aidx.fbx[0].path
        _r, secs = analyze(path, member, pw)
        row("%s: preview one model" % label, secs, member)
        big = [e for e in reader.entries if "/Large/" in "/" + e.path]
        CACHE.release_archive(path)
        _r, secs = timed(lambda: CACHE.materialize(reader, big))
        mb = sum(e.size for e in big) / 1e6
        row("%s: extract 12 large textures (%.0f MB)" % (label, mb), secs, "%.0f MB/s" % (mb / max(secs, 1e-6)))
        CACHE.release_archive(path)
        #same flow as the ui: analyze everything on add, then import the batch
        members = [e.path for e in aidx.fbx]
        one_pass = reader.solid or reader.one_pass_preferred
        t = time.perf_counter()
        if one_pass:
            pipeline.prefetch(None, SVC, path, pw, members)  #Session.analyze does this
        plans = [analyze(path, m, pw)[0][1] for m in members]
        row("%s: analyze all %d models (preview)" % (label, len(members)), time.perf_counter() - t)
        reset()
        t = time.perf_counter()
        if one_pass:
            pipeline.prefetch(None, SVC, path, pw, members, plans)  #the batch operator does this
        for m in members:
            hjt(path, m, pw, keep=True)
        row("%s: batch import all %d models" % (label, len(members)), time.perf_counter() - t, counts())
        CACHE.release_archive(path)
        SVC.forget_archive(path)


if "--archives-only" not in ARGS:
    heavy()
archives()
with open(os.path.join(BENCH, "results.json"), "w") as fh:
    json.dump({"blender": bpy.app.version_string, "rows": ROWS}, fh, indent=1)
shutil.rmtree(TMP, ignore_errors=True)
sys.stdout.flush()
os._exit(0)
