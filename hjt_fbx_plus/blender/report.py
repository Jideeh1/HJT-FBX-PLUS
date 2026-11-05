# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#import reports + history. one json per run in the user folder, newest first, pruned to N
# also renders a readable text version for the text editor

from __future__ import annotations

import json
import os
import time
import uuid


class RunReport:
    def __init__(self, settings, addon_version="", blender_version=""):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.started = time.time()
        self.finished = 0.0
        self.settings = dict(settings)
        self.addon_version = addon_version
        self.blender_version = blender_version
        self.models = []
        self.cancelled = False

    def add_model(self, entry):
        self.models.append(entry)

    def finish(self, cancelled=False):
        self.finished = time.time()
        self.cancelled = cancelled

    def totals(self):
        t = {"models": len(self.models), "ok": 0, "failed": 0, "warnings": 0, "errors": 0}
        for m in self.models:
            if m.get("status") == "OK":
                t["ok"] += 1
            else:
                t["failed"] += 1
            for msg in m.get("messages", []):
                if msg["level"] == "WARNING":
                    t["warnings"] += 1
                elif msg["level"] == "ERROR":
                    t["errors"] += 1
        return t

    def summary_line(self):
        t = self.totals()
        dur = (self.finished or time.time()) - self.started
        s = "%d/%d imported" % (t["ok"], t["models"])
        if t["failed"]:
            s += ", %d failed" % t["failed"]
        if t["warnings"]:
            s += ", %d warnings" % t["warnings"]
        if self.cancelled:
            s += " (cancelled)"
        return s + " in %.1fs" % dur

    def to_dict(self):
        return {"id": self.id, "started": self.started, "finished": self.finished, "cancelled": self.cancelled,
                "addon_version": self.addon_version, "blender_version": self.blender_version,
                "settings": self.settings, "totals": self.totals(), "summary": self.summary_line(),
                "models": self.models}


def to_text(d):
    lines = []
    lines.append("HJT FBX+ Import Report")
    lines.append("=" * 72)
    lines.append("Run:      %s" % d.get("id"))
    lines.append("When:     %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.get("started", 0))))
    lines.append("Result:   %s" % d.get("summary", ""))
    lines.append("Blender:  %s    HJT FBX+: %s" % (d.get("blender_version", ""), d.get("addon_version", "")))
    lines.append("")
    for m in d.get("models", []):
        lines.append("-" * 72)
        lines.append("%s  [%s]" % (m.get("name"), m.get("status")))
        lines.append("  Source:  %s" % m.get("source", ""))
        if m.get("profile"):
            lines.append("  Profile: %s    Engine: %s" % (m.get("profile"), m.get("engine", "")))
        if m.get("error"):
            lines.append("  ERROR:   %s" % m["error"])
        st = m.get("stats") or {}
        if st:
            lines.append("  Created: %s" % ", ".join("%s %s" % (v, k) for k, v in st.items() if v))
        fx = m.get("fixes") or {}
        if fx:
            lines.append("  FBX repairs before import: %s" % ", ".join("%s x%d" % (k.replace("_", " "), v) for k, v in fx.items()))
        tm = m.get("timings") or {}
        if tm:
            lines.append("  Timings: %s" % ", ".join("%s %.2fs" % (k, v) for k, v in tm.items()))
        mats = m.get("materials") or []
        if mats:
            lines.append("  Materials:")
            for mat in mats:
                faithful = sum(1 for a in mat.get("assignments", []) if a["source"] in ("fbx", "embedded", "unity-json"))
                inferred = len(mat.get("assignments", [])) - faithful
                lines.append("    %-40s %-28s textures: %d faithful, %d inferred" % (
                    mat.get("name", "")[:40], mat.get("shading", ""), faithful, inferred))
                for a in mat.get("assignments", []):
                    lines.append("        %-12s %-10s %s%s" % (a["slot"], a["source"], a["file"], "  (ambiguous)" if a.get("ambiguous") else ""))
                for miss in mat.get("missing", []):
                    lines.append("        MISSING %-8s %s" % (miss.get("slot", ""), miss.get("name", "")))
        msgs = m.get("messages") or []
        if msgs:
            lines.append("  Messages:")
            for msg in msgs:
                lines.append("    %-7s %s" % (msg["level"], msg["text"]))
        lines.append("")
    return "\n".join(lines)


class History:
    def __init__(self, folder, keep=50):
        self.folder = folder
        self.keep = keep

    def save(self, report):
        if not self.folder:
            return None
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, "%s.json" % report.id)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=1, ensure_ascii=False, default=str)
        self.prune()
        return path

    def entries(self, limit=100):
        if not self.folder or not os.path.isdir(self.folder):
            return []
        out = []
        for fn in sorted(os.listdir(self.folder), reverse=True)[:limit]:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(self.folder, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    head = json.load(fh)
                out.append({"id": head.get("id", fn[:-5]), "started": head.get("started", 0),
                            "summary": head.get("summary", ""), "path": path,
                            "names": ", ".join(m.get("name", "") for m in head.get("models", [])[:4])})
            except (OSError, ValueError):
                continue
        return out

    def load(self, rid):
        path = os.path.join(self.folder, "%s.json" % rid)
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def prune(self):
        files = sorted(f for f in os.listdir(self.folder) if f.endswith(".json"))
        for f in files[:-self.keep] if self.keep > 0 else []:
            try:
                os.remove(os.path.join(self.folder, f))
            except OSError:
                pass

    def clear(self):
        if not self.folder or not os.path.isdir(self.folder):
            return 0
        n = 0
        for f in os.listdir(self.folder):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(self.folder, f))
                    n += 1
                except OSError:
                    pass
        return n
