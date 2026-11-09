# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#File > Import entry + drag n drop (FileHandler, blender 4.1+)

from __future__ import annotations

import bpy

from ..blender import compat


def menu_import(self, context):
    self.layout.operator("hjt_fbx_plus.import_files", text="HJT FBX+ (.fbx, .uemodel, .zip, .7z, .rar)")


CLASSES = []

if compat.HAS_FILE_HANDLER:
    class HJTFBX_FH_import(bpy.types.FileHandler):
        bl_idname = "HJTFBX_FH_import"
        bl_label = "HJT FBX+"
        bl_import_operator = "hjt_fbx_plus.import_files"
        bl_file_extensions = ".fbx;.uemodel;.zip;.7z;.rar"

        @classmethod
        def poll_drop(cls, context):
            return context.area is not None and context.area.type in ("VIEW_3D", "OUTLINER")

    CLASSES.append(HJTFBX_FH_import)
